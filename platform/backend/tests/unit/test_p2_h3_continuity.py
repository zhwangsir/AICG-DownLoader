"""P2: H3 last-frame chain default + character bible → Ref2VA + empty → FL2VA not Wan.

覆盖：
- h3_last_frame_chain_enabled 默认 True，顺序镜头 bootstrap last_frame_url
- 上一镜解码末帧写入下一镜 last_frame（顺序 execute）
- FL2VA 末帧失败重试一次后降级首帧-only
- 角色圣经（三视图 + face still + voice）非空 → MiniMaxH3ReferenceToVideo
- 空镜/无角色：SFW/NSFW 均走 H3 FL2VA，不回退 Wan；LTX 仅在 :8198 健康时
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.video_agent import (
    VideoAgent,
    apply_last_frame_chain,
    engine_fallback_chain,
    has_character_refs,
    route_video_engine,
)
from app.config import settings
from app.models.schemas import (
    Character,
    CharacterAsset,
    PipelineRunRequest,
    Scene,
    Script,
    VideoRequest,
)
from app.services.pipeline_orchestrator import PipelineOrchestrator


def _req(**kw) -> VideoRequest:
    defaults = dict(
        scene_id=1,
        image_url="http://x/i.png",
        prompt="cinematic",
        duration_seconds=3,
        episode=1,
    )
    defaults.update(kw)
    return VideoRequest(**defaults)


@pytest.fixture
def agent():
    return VideoAgent()


class TestLastFrameChainDefault:
    def test_flag_defaults_true(self):
        from app.config import Settings

        assert Settings().h3_last_frame_chain_enabled is True
        assert settings.h3_last_frame_chain_enabled is True

    def test_apply_chain_sets_next_keyframe_on_same_episode(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_last_frame_chain_enabled", True)
        items = [
            _req(scene_id=1, image_url="http://x/a.png", episode=1),
            _req(scene_id=2, image_url="http://x/b.png", episode=1),
            _req(scene_id=3, image_url="http://x/c.png", episode=2),
        ]
        apply_last_frame_chain(items)
        assert items[0].last_frame_url == "http://x/b.png"
        assert items[1].last_frame_url == ""
        assert items[2].last_frame_url == ""

    def test_apply_chain_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_last_frame_chain_enabled", False)
        items = [
            _req(scene_id=1, image_url="http://x/a.png"),
            _req(scene_id=2, image_url="http://x/b.png"),
        ]
        apply_last_frame_chain(items)
        assert items[0].last_frame_url == ""

    async def test_sequential_decoded_last_frame_attached(self, agent, monkeypatch):
        monkeypatch.setattr(settings, "h3_last_frame_chain_enabled", True)
        monkeypatch.setattr(settings, "video_backend", "h3")
        seen: list[str] = []

        async def fake_execute(req, progress_callback=None, worker_url=None):
            seen.append(req.last_frame_url)
            from app.models.schemas import AgentResponse

            return AgentResponse(
                success=True,
                data={
                    "scene_id": req.scene_id,
                    "video_url": f"http://x/v{req.scene_id}.mp4",
                    "duration_seconds": 3,
                },
            )

        agent.execute = fake_execute  # type: ignore[method-assign]
        agent.publish_last_frame_url = AsyncMock(return_value="http://x/decoded_1.png")
        reqs = [
            _req(scene_id=1, image_url="http://x/a.png"),
            _req(scene_id=2, image_url="http://x/b.png"),
        ]
        await agent._execute_scenes_individually(reqs, None, 0.0)
        assert seen == ["", "http://x/decoded_1.png"]
        agent.publish_last_frame_url.assert_awaited_once()


class TestFl2vaRetryDegrade:
    async def test_last_frame_retries_once_then_degrades(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_last_frame_chain_enabled", True)
        mock_call_comfyui.side_effect = [
            RuntimeError("last-frame oom"),
            RuntimeError("last-frame oom"),
            {"prompt_id": "ok"},
        ]
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        resp = await agent.execute(
            _req(last_frame_url="http://x/end.png", prompt="cinematic")
        )
        assert resp.success is True
        assert mock_call_comfyui.await_count == 3
        workflows = [c.args[1] for c in mock_call_comfyui.call_args_list]
        assert "last_frame" in workflows[0]["20"]["inputs"]
        assert "last_frame" in workflows[1]["20"]["inputs"]
        assert "last_frame" not in workflows[2]["20"]["inputs"]
        assert workflows[2]["20"]["class_type"] == "MiniMaxH3ImageToVideo"


class TestRefsRouteR2V:
    async def test_character_bible_refs_use_reference_to_video(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
        mock_upload_media=None,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "r2v.mp4", "subfolder": "", "type": "output"}]}
        }
        with patch(
            "app.agents.base.BaseAgent.upload_media_to_comfyui",
            new_callable=AsyncMock,
            side_effect=lambda worker_url, url, fallback_name="input.bin": url.rsplit("/", 1)[-1],
        ):
            resp = await agent.execute(
                _req(
                    reference_images=[
                        "http://x/front.png",
                        "http://x/side.png",
                        "http://x/face.png",
                    ],
                    reference_audios=["http://x/voice.wav"],
                    prompt="character walks",
                )
            )
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert "WanImageToVideo" not in {
            n["class_type"] for n in workflow.values()
        }

    def test_bible_collector_attaches_face_and_voice(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_ref_max_images", 9)
        monkeypatch.setattr(settings, "h3_ref_max_audios", 3)
        script = Script(
            project_id="p1",
            characters=[
                Character(character_id="c1", name="A"),
                Character(character_id="c2", name="B"),
            ],
            scenes=[Scene(scene_id=1)],
        )
        assets = {
            "c1": CharacterAsset(
                character_id="c1",
                name="A",
                source_script_id="p1",
                reference_images={
                    "front": "http://x/a_front.png",
                    "side": "http://x/a_side.png",
                    "closeup": "http://x/a_close.png",
                },
                face_still="http://x/a_face.png",
                voice_sample="http://x/a_voice.wav",
            ),
            "c2": CharacterAsset(
                character_id="c2",
                name="B",
                source_script_id="p1",
                reference_images={"front": "http://x/b_front.png"},
                voice_sample="http://x/b_voice.wav",
            ),
        }
        with patch(
            "app.services.pipeline_orchestrator.character_library"
        ) as lib:
            lib.get = MagicMock(side_effect=lambda cid: assets.get(cid))
            images, videos, audios = PipelineOrchestrator._collect_character_bible(
                script
            )
        assert images[0] == "http://x/a_front.png"
        assert "http://x/a_face.png" in images
        assert "http://x/a_close.png" in images
        assert "http://x/b_front.png" in images
        assert audios == ["http://x/a_voice.wav", "http://x/b_voice.wav"]
        assert videos == []
        assert len(images) <= 9

    def test_bible_caps_images_and_audios(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_ref_max_images", 9)
        monkeypatch.setattr(settings, "h3_ref_max_audios", 3)
        chars = [Character(character_id=f"c{i}", name=str(i)) for i in range(6)]
        script = Script(project_id="p1", characters=chars, scenes=[Scene(scene_id=1)])

        def _get(cid: str) -> CharacterAsset:
            return CharacterAsset(
                character_id=cid,
                name=cid,
                source_script_id="p1",
                reference_images={
                    "front": f"http://x/{cid}_f.png",
                    "side": f"http://x/{cid}_s.png",
                    "back": f"http://x/{cid}_b.png",
                },
                voice_sample=f"http://x/{cid}.wav",
            )

        with patch(
            "app.services.pipeline_orchestrator.character_library"
        ) as lib:
            lib.get = MagicMock(side_effect=_get)
            images, _videos, audios = PipelineOrchestrator._collect_character_bible(
                script
            )
        assert len(images) == 9
        assert len(audios) == 3


class TestEmptyStaysFl2vaNotWan:
    def test_empty_fallback_chain_is_h3_only(self):
        chain = engine_fallback_chain("h3", _req())
        assert chain == ("h3",)
        assert "comfyui" not in chain
        assert has_character_refs(_req()) is False

    def test_refs_keep_wan_fallback(self):
        chain = engine_fallback_chain(
            "h3", _req(reference_images=["http://x/c.png"])
        )
        assert chain == ("h3", "comfyui")

    async def test_empty_shot_uses_h3_image_to_video_not_wan(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "fl2va.mp4", "subfolder": "", "type": "output"}]}
        }
        resp = await agent.execute(_req(prompt="empty street at night"))
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        class_types = {n["class_type"] for n in workflow.values()}
        assert "WanImageToVideo" not in class_types
        assert "MiniMaxH3ReferenceToVideo" not in class_types

    async def test_empty_h3_failure_does_not_fall_back_to_wan(
        self, agent, monkeypatch, mock_upload_image
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_upload_image.side_effect = RuntimeError("h3 down")
        resp = await agent.execute(_req(prompt="empty hallway"))
        assert resp.success is False
        assert "h3=" in resp.error
        assert "comfyui=" not in resp.error
        assert mock_upload_image.await_count == 1

    async def test_empty_nsfw_stays_h3_fl2va(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "nsfw.mp4", "subfolder": "", "type": "output"}]}
        }
        with patch(
            "app.services.settings_service.settings_service"
        ) as ss:
            ss.nsfw_status.return_value = {"nsfw_enabled": True}
            resp = await agent.execute(_req(prompt="empty room"))
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert workflow["1"]["inputs"]["unet_name"] == settings.h3_nsfw_unet_name
        assert "WanImageToVideo" not in {
            n["class_type"] for n in workflow.values()
        }

    async def test_empty_does_not_use_ltx_when_down(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", True)
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "h3.mp4", "subfolder": "", "type": "output"}]}
        }
        with patch.object(
            agent, "_ltx_or_h3", new_callable=AsyncMock, return_value="h3"
        ):
            # auto-route would pick ltx for long duration; execute must stay H3
            resp = await agent.execute(_req(duration_seconds=20, prompt="aerial drone"))
        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert mock_call_comfyui.call_args[0][0] == settings.h3_comfyui_url

    def test_route_empty_motion_stays_h3_when_ltx_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", False)
        engine = route_video_engine(
            _req(prompt="aerial drone shot over the city, camera pans"),
            settings,
        )
        assert engine == "h3"


class TestOrchestratorLastFrameAndBible:
    async def test_h3_pipeline_applies_last_frame_chain_and_voice(
        self, monkeypatch
    ):
        from app.models.schemas import AgentResponse
        from tests.unit.test_pipeline_orchestrator import (
            _script_data,
            _wait_done,
        )

        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_multishot_enabled", False)
        monkeypatch.setattr(settings, "h3_last_frame_chain_enabled", True)

        req = PipelineRunRequest(
            premise="深夜便利店偶遇",
            scenes_per_episode=2,
            generate_character_refs=False,
            run_quality_check=False,
            reference_videos=[],
            reference_audios=[],
        )
        asset = CharacterAsset(
            character_id="char_001",
            name="林远",
            source_script_id="",
            reference_images={
                "front": "http://x/c1_front.png",
                "side": "http://x/c1_side.png",
                "closeup": "http://x/c1_close.png",
            },
            face_still="http://x/c1_face.png",
            voice_sample="http://x/c1_voice.wav",
        )
        with (
            patch("app.services.pipeline_orchestrator.script_agent") as m_script,
            patch("app.services.pipeline_orchestrator.character_agent"),
            patch("app.services.pipeline_orchestrator.storyboard_agent") as m_sb,
            patch("app.services.pipeline_orchestrator.video_agent") as m_video,
            patch("app.services.pipeline_orchestrator.voice_agent") as m_voice,
            patch("app.services.pipeline_orchestrator.subtitle_agent") as m_sub,
            patch("app.services.pipeline_orchestrator.edit_agent") as m_edit,
            patch("app.services.pipeline_orchestrator.quality_agent"),
            patch("app.services.pipeline_orchestrator.visual_quality_agent"),
            patch("app.services.pipeline_orchestrator.character_library") as m_lib,
        ):
            m_lib.get = MagicMock(
                side_effect=lambda cid: asset if cid == "char_001" else None
            )
            m_script.execute = AsyncMock(
                return_value=AgentResponse(success=True, data=_script_data())
            )
            m_sb.batch_execute = AsyncMock(
                return_value=AgentResponse(
                    success=True,
                    data={
                        "results": [
                            {
                                "scene_id": 1,
                                "image_url": "/static/storyboard/scene_1.png",
                            },
                            {
                                "scene_id": 2,
                                "image_url": "/static/storyboard/scene_2.png",
                            },
                        ],
                        "failed_scenes": [],
                    },
                )
            )
            m_video.batch_execute = AsyncMock(
                return_value=AgentResponse(
                    success=True,
                    data={
                        "results": [
                            {
                                "scene_id": 1,
                                "video_url": "/static/video/scene_1.mp4",
                                "duration_seconds": 3,
                            },
                            {
                                "scene_id": 2,
                                "video_url": "/static/video/scene_2.mp4",
                                "duration_seconds": 3,
                            },
                        ],
                        "failed_scenes": [],
                    },
                )
            )
            m_voice.execute = AsyncMock(
                side_effect=lambda r: AgentResponse(
                    success=True,
                    data={
                        "scene_id": r.scene_id,
                        "audio_urls": [
                            {"audio_url": f"/static/audio/scene_{r.scene_id}.mp3"}
                        ],
                        "total_lines": 1,
                    },
                )
            )
            m_sub.execute = AsyncMock(
                side_effect=lambda r: AgentResponse(
                    success=True,
                    data={
                        "scene_id": r.scene_id,
                        "srt_url": f"/static/sub/scene_{r.scene_id}.srt",
                        "segments": [],
                    },
                )
            )
            m_edit.execute = AsyncMock(
                return_value=AgentResponse(
                    success=True,
                    data={
                        "project_id": "x",
                        "title": "t",
                        "final_video_url": "/static/final.mp4",
                        "duration_seconds": 6,
                        "segments_count": 2,
                    },
                )
            )
            orch = PipelineOrchestrator()
            task_id = orch.start(req)
            await _wait_done(orch, task_id)

        batch = m_video.batch_execute.call_args.args[0]
        assert batch.items[0].last_frame_url == "/static/storyboard/scene_2.png"
        assert batch.items[1].last_frame_url == ""
        for item in batch.items:
            assert "http://x/c1_front.png" in item.reference_images
            assert "http://x/c1_face.png" in item.reference_images
            assert item.reference_audios == ["http://x/c1_voice.wav"]
