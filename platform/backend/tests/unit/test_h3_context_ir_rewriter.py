"""P1: local H3 Context-IR rewrite (spark LLM/VLM, fail-open)."""

from __future__ import annotations

import logging
import re
import pytest

from app.agents.video_agent import VideoAgent
from app.config import settings
from app.models.schemas import VideoRequest
from app.services.h3_context_ir_rewriter import (
    ADULT_VOCAB,
    H3RewriteSpec,
    looks_like_base_ir,
    looks_like_ref2va_ir,
    rewrite_h3_prompt,
    sfw_templates_blob,
    validate_rewrite_output,
)


def _fl2va_ir(duration: str = "5.00") -> str:
    return (
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from Shot 1) aligns with the {duration}-second mark of the target video.\n\n"
        "integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium shot "
        "frames a delivery rider at a convenience-store door. The camera pushes in with "
        "small amplitude at slow speed as he (S1) says <d>[zh] 这单地址怎么这么熟悉？</d>\n"
        "overall_soundscape: Refrigerator hum and rain on the awning. Distant scooters pass.\n"
        "non_diegetic_music: Sparse piano notes at a slow tempo."
    )


def _ref2va_ir(n_pictures: int = 2) -> str:
    pics = "\n".join(
        f"<Picture {i}> is the {'composition keyframe' if i == 1 else 'identity reference'} "
        f"for the rider (job: {'style' if i == 1 else 'identity'})."
        for i in range(1, n_pictures + 1)
    )
    tags = " ".join(f"<Picture {i}>" for i in range(1, n_pictures + 1))
    return (
        "subject_definitions:\n"
        f"{pics}\n"
        "summary:\n"
        f"[reference generation] The target video keeps {tags} on the same rider.\n"
        "retention_analysis:\n"
        "<Picture 1> ([Shot 1] first frame): fully_preserved - framing kept.\n"
        "<Picture 2> (appears in [Shot 1]): fully_preserved - face and jacket kept.\n"
        "detailed_description:\n"
        "The target video uses a live-action cinematic look.\n"
        f"[Shot 1] A medium shot begins from <Picture 1> with identity from <Picture 2>. "
        "The camera holds a static shot.\n"
        "overall_soundscape: Rain on jackets.\n"
        "non_diegetic_music: N/A"
    )


class TestSfwTemplates:
    def test_sfw_template_has_no_adult_vocabulary(self):
        blob = sfw_templates_blob().lower()
        for word in ADULT_VOCAB:
            assert re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", blob) is None, word


class TestRewriteFl2va:
    async def test_output_contains_shot_and_soundscape_fields(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)

        async def llm(_messages):
            return _fl2va_ir()

        out = await rewrite_h3_prompt(
            H3RewriteSpec(
                prompt="rider waits in the rain outside a store",
                mode="fl2va",
                duration_seconds=5,
                original_fallback="rider waits in the rain",
            ),
            llm_caller=llm,
        )
        assert "integrated_multimodal_description:" in out
        assert "[Shot 1]" in out
        assert "overall_soundscape:" in out
        assert "non_diegetic_music:" in out
        assert looks_like_base_ir(out)


class TestRewriteRef2va:
    async def test_output_contains_picture_tags_when_n_images(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)
        n = 3

        async def llm(_messages):
            return _ref2va_ir(n_pictures=n)

        async def vlm(_content):
            return (
                "<Picture 1> identity: wet jacket fully_preserved - face\n"
                "<Picture 2> identity: front view fully_preserved - hair\n"
                "<Picture 3> style: key light fully_preserved - palette"
            )

        out = await rewrite_h3_prompt(
            H3RewriteSpec(
                prompt="keep this character walking",
                mode="ref2va",
                n_pictures=n,
                n_videos=0,
                n_audios=0,
                reference_image_urls=[
                    "http://x/a.png",
                    "http://x/b.png",
                    "http://x/c.png",
                ],
                original_fallback="keep this character walking",
            ),
            llm_caller=llm,
            vlm_caller=vlm,
        )
        assert "subject_definitions:" in out
        assert "retention_analysis:" in out
        for i in range(1, n + 1):
            assert f"<Picture {i}>" in out
        assert looks_like_ref2va_ir(out, n_pictures=n)


class TestFailOpen:
    async def test_failure_falls_back_to_original(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)
        original = "cinematic, high quality, smooth motion"

        async def boom(_messages):
            raise RuntimeError("spark down")

        with caplog.at_level(logging.WARNING):
            out = await rewrite_h3_prompt(
                H3RewriteSpec(
                    prompt="a girl in rain",
                    mode="fl2va",
                    original_fallback=original,
                ),
                llm_caller=boom,
            )
        assert out == original
        assert any("original prompt" in r.message for r in caplog.records)

    async def test_invalid_llm_output_falls_back_to_original(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)
        original = "plain scene prompt"

        async def bad(_messages):
            return "sure, here is a nice video idea without fields"

        out = await rewrite_h3_prompt(
            H3RewriteSpec(
                prompt="plain scene prompt",
                mode="fl2va",
                original_fallback=original,
            ),
            llm_caller=bad,
        )
        assert out == original

    async def test_disabled_returns_original(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", False)

        async def llm(_messages):
            raise AssertionError("LLM must not be called when rewrite is off")

        out = await rewrite_h3_prompt(
            H3RewriteSpec(prompt="x", original_fallback="keep-me"),
            llm_caller=llm,
        )
        assert out == "keep-me"


class TestValidate:
    def test_base_requires_shot_and_soundscape(self):
        assert validate_rewrite_output(_fl2va_ir(), "fl2va") is True
        assert validate_rewrite_output("overall_soundscape: rain", "fl2va") is False

    def test_ref2va_requires_picture_when_n(self):
        text = _ref2va_ir(2)
        assert validate_rewrite_output(text, "ref2va", n_pictures=2) is True
        missing = text.replace("<Picture", "<Image")
        assert validate_rewrite_output(missing, "ref2va", n_pictures=2) is False


class TestVideoAgentWiring:
    async def test_fl2va_sends_rewritten_prompt(
        self,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)
        rewritten = _fl2va_ir("3.00")

        async def llm(_messages):
            return rewritten

        monkeypatch.setattr(
            "app.services.h3_context_ir_rewriter._default_llm_caller", llm
        )
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        agent = VideoAgent()
        resp = await agent.execute(
            VideoRequest(
                scene_id=1,
                image_url="http://x/sb.png",
                last_frame_url="http://x/end.png",
                prompt="rider waits in the rain",
                duration_seconds=3,
            )
        )
        assert resp.success is True
        prompt = mock_call_comfyui.call_args[0][1]["20"]["inputs"]["prompt"]
        assert "integrated_multimodal_description:" in prompt
        assert "overall_soundscape:" in prompt
        assert "[Shot 1]" in prompt

    async def test_r2v_sends_picture_tags(
        self,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)

        async def llm(_messages):
            return _ref2va_ir(n_pictures=2)

        async def vlm(_content):
            return "<Picture 1> identity fully_preserved"

        monkeypatch.setattr(
            "app.services.h3_context_ir_rewriter._default_llm_caller", llm
        )
        monkeypatch.setattr(
            "app.services.h3_context_ir_rewriter._default_vlm_caller", vlm
        )
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        agent = VideoAgent()
        resp = await agent.execute(
            VideoRequest(
                scene_id=2,
                image_url="http://x/sb.png",
                prompt="lock this face",
                reference_images=["http://x/ref.png"],
                duration_seconds=4,
            )
        )
        assert resp.success is True
        prompt = mock_call_comfyui.call_args[0][1]["20"]["inputs"]["prompt"]
        assert "subject_definitions:" in prompt
        assert "<Picture 1>" in prompt
        assert "<Picture 2>" in prompt

    async def test_llm_failure_keeps_original_on_wire(
        self,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        caplog,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)

        async def boom(_messages):
            raise RuntimeError("no spark")

        monkeypatch.setattr(
            "app.services.h3_context_ir_rewriter._default_llm_caller", boom
        )
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        with caplog.at_level(logging.WARNING):
            agent = VideoAgent()
            resp = await agent.execute(
                VideoRequest(
                    scene_id=3,
                    image_url="http://x/sb.png",
                    prompt="cinematic street at night",
                    duration_seconds=3,
                )
            )
        assert resp.success is True
        prompt = mock_call_comfyui.call_args[0][1]["20"]["inputs"]["prompt"]
        assert "cinematic street at night" in prompt
        assert any("original prompt" in r.message for r in caplog.records)



class TestOrchestratorWiring:
    async def test_non_multishot_rewrites_item_prompt(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_context_ir_rewrite_enabled", True)
        from app.services.pipeline_orchestrator import PipelineOrchestrator

        async def llm(_messages):
            return _fl2va_ir("3.00")

        monkeypatch.setattr(
            "app.services.h3_context_ir_rewriter._default_llm_caller", llm
        )
        orch = PipelineOrchestrator()
        items = [
            VideoRequest(
                scene_id=1,
                image_url="http://x/a.png",
                last_frame_url="http://x/b.png",
                prompt="rider waits in the rain",
                duration_seconds=3,
            )
        ]
        out = await orch._rewrite_h3_video_prompts(items)
        assert "integrated_multimodal_description:" in out[0].prompt
        assert "overall_soundscape:" in out[0].prompt
