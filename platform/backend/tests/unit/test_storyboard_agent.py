"""分镜 Agent 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.storyboard_agent import BEAT_VISUAL_HINTS, StoryboardAgent
from app.config import settings
from app.models.schemas import Scene, StoryboardRequest


@pytest.fixture
def agent():
    return StoryboardAgent()


class TestStoryboardAgentExecute:
    """基础执行测试（默认 sdxl 后端，由 conftest 设置）。"""

    async def test_success_with_existing_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        sample_scene.prompt = "existing prompt"
        sample_scene.negative_prompt = "existing negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {
                "images": [
                    {"filename": "sb_1.png", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["scene_id"] == 1
        # 提示词现在总是经 LLM 重写
        assert response.data["prompt_used"] == "rewritten prompt"
        # 默认 LTX 关闭 → preview_video_url 为空字符串
        assert response.data["preview_video_url"] == ""

    async def test_success_generate_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        sample_scene.prompt = ""
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "generated prompt",
                "negative_prompt": "generated negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {
                "images": [
                    {"filename": "sb_1.png", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["prompt_used"] == "generated prompt"

    async def test_exception_returns_error(self, agent, sample_scene, mock_call_llm):
        sample_scene.prompt = ""
        mock_call_llm.side_effect = RuntimeError("失败")

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is False
        assert "失败" in response.error


class TestBeatVisualHints:
    """narrative_beat → 分镜视觉指令注入测试。"""

    def _make_scene(self, beat: str) -> Scene:
        return Scene(
            scene_id=1,
            episode=1,
            shot_type="特写",
            description="主角盯着手机屏幕",
            narrative_beat=beat,
        )

    async def test_beat_hint_injected_into_llm_message(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        scene = self._make_scene("hook")

        await agent._generate_prompts(scene, [], "写实电影感")

        messages = mock_call_llm.call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "叙事节拍" in user_msg
        assert "hook" in user_msg
        assert BEAT_VISUAL_HINTS["hook"] in user_msg

    async def test_no_beat_no_hint(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        scene = self._make_scene("")

        await agent._generate_prompts(scene, [], "写实电影感")

        messages = mock_call_llm.call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "叙事节拍" not in user_msg

    async def test_unknown_beat_no_hint(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps({"prompt": "p", "negative_prompt": "n"})
        scene = self._make_scene("unknown_beat")

        await agent._generate_prompts(scene, [], "写实电影感")

        messages = mock_call_llm.call_args.kwargs["messages"]
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "叙事节拍" not in user_msg

    def test_all_valid_beats_have_hints(self):
        for beat in ("hook", "escalation", "reversal", "cliffhanger", "emotional_beat", "transition"):
            assert beat in BEAT_VISUAL_HINTS
            assert BEAT_VISUAL_HINTS[beat]


# ============================================================================
# P4.3: HunyuanImage / FLUX+PuLID 双后端派发测试
# ============================================================================


class TestStoryboardDualBackend:
    """P4.3: HunyuanImage / FLUX+PuLID 主后端 + SDXL 回退测试。"""

    def _attach_mock_hunyuanimage(self, agent, return_value=None, side_effect=None):
        mock_svc = MagicMock()
        mock_svc.generate_one = AsyncMock(
            return_value=return_value or b"hunyuanimage-png",
            side_effect=side_effect,
        )
        agent._hunyuanimage = mock_svc
        return mock_svc

    def _attach_mock_flux_pulid(self, agent, return_value=None, side_effect=None):
        mock_svc = MagicMock()
        mock_svc.generate_one = AsyncMock(
            return_value=return_value or b"flux-png",
            side_effect=side_effect,
        )
        agent._flux_pulid = mock_svc
        return mock_svc

    async def test_hunyuanimage_backend_success(
        self, agent, sample_scene, mock_call_llm, monkeypatch, tmp_path
    ):
        """image_backend='hunyuanimage' → 走 HunyuanImage 主路径，保存到本地。"""
        monkeypatch.setattr(settings, "image_backend", "hunyuanimage")
        mock_svc = self._attach_mock_hunyuanimage(agent, return_value=b"hunyuan-img")
        monkeypatch.setattr(
            "app.agents.storyboard_agent.OUTPUT_DIR", tmp_path, raising=True
        )
        sample_scene.prompt = "existing prompt"
        sample_scene.negative_prompt = "existing negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["scene_id"] == 1
        assert "/static/storyboard/" in response.data["image_url"]
        # 提示词现在总是经 LLM 重写
        assert response.data["prompt_used"] == "rewritten prompt"
        # HunyuanImage 被调用 1 次
        assert mock_svc.generate_one.await_count == 1
        # 校验 prompt 注入了 POSITIVE_SUFFIX
        call_kwargs = mock_svc.generate_one.call_args.kwargs
        assert "cinematic" in call_kwargs["prompt"]
        assert "low quality" in call_kwargs["negative_prompt"]

    async def test_flux_pulid_backend_success(
        self, agent, sample_scene, mock_call_llm, monkeypatch, tmp_path
    ):
        """image_backend='flux_pulid' → 走 FLUX+PuLID 主路径。"""
        monkeypatch.setattr(settings, "image_backend", "flux_pulid")
        mock_svc = self._attach_mock_flux_pulid(agent, return_value=b"flux-img")
        monkeypatch.setattr(
            "app.agents.storyboard_agent.OUTPUT_DIR", tmp_path, raising=True
        )
        sample_scene.prompt = "test prompt"
        sample_scene.negative_prompt = "test negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert "/static/storyboard/" in response.data["image_url"]
        assert mock_svc.generate_one.await_count == 1

    async def test_hunyuanimage_failure_fallback_to_sdxl(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
        tmp_path,
    ):
        """HunyuanImage 抛异常 → 自动回退到 ComfyUI SDXL 路径。"""
        monkeypatch.setattr(settings, "image_backend", "hunyuanimage")
        self._attach_mock_hunyuanimage(
            agent, side_effect=RuntimeError("HunyuanImage OOM")
        )
        monkeypatch.setattr(
            "app.agents.storyboard_agent.OUTPUT_DIR", tmp_path, raising=True
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "fallback.png", "subfolder": "", "type": "output"}]}
        }
        sample_scene.prompt = "test prompt"
        sample_scene.negative_prompt = "test negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        # 走 SDXL 回退 → URL 含 /view?
        assert "/view?" in response.data["image_url"]

    async def test_hunyuanimage_and_sdxl_both_fail(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
        tmp_path,
    ):
        """主后端和 SDXL 回退都失败 → 返回 error。"""
        monkeypatch.setattr(settings, "image_backend", "hunyuanimage")
        self._attach_mock_hunyuanimage(
            agent, side_effect=RuntimeError("HunyuanImage OOM")
        )
        monkeypatch.setattr(
            "app.agents.storyboard_agent.OUTPUT_DIR", tmp_path, raising=True
        )
        # SDXL 回退也失败：ComfyUI 返回空
        mock_get_comfyui_result.return_value = {"7": {}}
        sample_scene.prompt = "test prompt"
        sample_scene.negative_prompt = "test negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is False
        assert "SDXL 回退也失败" in response.error or "未找到生成的图片" in response.error

    async def test_sdxl_backend_skips_service(
        self, agent, sample_scene, mock_call_llm, mock_call_comfyui,
        mock_get_comfyui_result, monkeypatch
    ):
        """image_backend='sdxl'（conftest 默认）→ 不调用 HunyuanImage/FLUX+PuLID。"""
        mock_h = self._attach_mock_hunyuanimage(agent)
        mock_f = self._attach_mock_flux_pulid(agent)
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
        }
        sample_scene.prompt = "test"
        sample_scene.negative_prompt = "neg"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert mock_h.generate_one.await_count == 0
        assert mock_f.generate_one.await_count == 0


# ============================================================================
# P4.3: LTX-Video 分镜预览钩子测试
# ============================================================================


class TestStoryboardLTXPreview:
    """P4.3: LTX-Video 分镜预览钩子测试。

    预览失败不影响主流程；预览成功时填充 preview_video_url。
    """

    def _attach_mock_ltx(self, agent, return_value=None, side_effect=None):
        mock_svc = MagicMock()
        mock_svc.generate_preview = AsyncMock(
            return_value=return_value or {"video_url": "http://mock/preview.mp4"},
            side_effect=side_effect,
        )
        mock_svc.is_enabled = MagicMock(return_value=True)
        agent._ltx_video = mock_svc
        return mock_svc

    async def test_ltx_preview_success(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """LTX 预览成功 → preview_video_url 填充。"""
        monkeypatch.setattr(settings, "ltx_video_enabled", True)
        mock_ltx = self._attach_mock_ltx(
            agent, return_value={"video_url": "http://mock/preview.mp4"}
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }
        sample_scene.prompt = "test"
        sample_scene.negative_prompt = "neg"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["preview_video_url"] == "http://mock/preview.mp4"
        # LTX 被调用 1 次
        assert mock_ltx.generate_preview.await_count == 1
        # 校验 prompt 包含运镜和角色动作
        call_kwargs = mock_ltx.generate_preview.call_args.kwargs
        assert "static" in call_kwargs["prompt"]  # camera_movement

    async def test_ltx_preview_failure_does_not_break_flow(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """LTX 预览抛异常 → 不影响主流程，preview_video_url 为空。"""
        monkeypatch.setattr(settings, "ltx_video_enabled", True)
        self._attach_mock_ltx(agent, side_effect=RuntimeError("LTX OOM"))
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }
        sample_scene.prompt = "test"
        sample_scene.negative_prompt = "neg"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        # 分镜图生成成功，但预览失败 → preview_video_url 为空
        assert response.data["preview_video_url"] == ""
        assert "/view?" in response.data["image_url"]

    async def test_ltx_disabled_skips_preview(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        """ltx_video_enabled=False → 不调用 LTX 预览。"""
        monkeypatch.setattr(settings, "ltx_video_enabled", False)
        mock_ltx = MagicMock()
        mock_ltx.generate_preview = AsyncMock()
        mock_ltx.is_enabled = MagicMock(return_value=False)
        agent._ltx_video = mock_ltx
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }
        sample_scene.prompt = "test"
        sample_scene.negative_prompt = "neg"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )

        request = StoryboardRequest(scene=sample_scene)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["preview_video_url"] == ""
        assert mock_ltx.generate_preview.await_count == 0


class TestStoryboardAgentRAGEnhance:
    async def test_rag_enhances_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        sample_scene.prompt = "test prompt"
        sample_scene.negative_prompt = "test negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        with patch(
            "app.agents.storyboard_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={
                "optimized_positive": "rag positive",
                "optimized_negative": "rag negative",
            },
        ):
            response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        assert response.data["prompt_used"] == "rag positive"

    async def test_rag_failure_keeps_llm_prompt(
        self,
        agent,
        sample_scene,
        mock_call_llm,
        mock_call_comfyui,
        mock_get_comfyui_result,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        sample_scene.prompt = "test prompt"
        sample_scene.negative_prompt = "test negative"
        mock_call_llm.return_value = json.dumps(
            {
                "prompt": "rewritten prompt",
                "negative_prompt": "rewritten negative",
            }
        )
        mock_get_comfyui_result.return_value = {
            "7": {"images": [{"filename": "sb.png", "subfolder": "", "type": "output"}]}
        }

        with patch(
            "app.agents.storyboard_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("RAG 失败"),
        ):
            response = await agent.execute(StoryboardRequest(scene=sample_scene))

        assert response.success is True
        assert response.data["prompt_used"] == "rewritten prompt"
