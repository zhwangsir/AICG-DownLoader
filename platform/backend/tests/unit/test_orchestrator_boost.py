"""pipeline_orchestrator 覆盖率补充测试（boost）。

针对既有套件（全流程驱动）未覆盖的步骤级分支，采用直接调用 _step_* 方法
+ 局部 mock 各 Agent 的方式补齐：
- _step_characters：空目标跳过 / 单角色定妆失败记录
- _step_storyboard：批量失败 / 全场景失败 → RuntimeError
- _step_video：batch_execute 失败 → RuntimeError；_step_video_long 规划零块 → RuntimeError
- _run_video_multishot：单场景失败计入 failed_scenes
- update_shot_result：快照缺失 / 镜头缺失 → False
- _step_voice：无台词场景跳过；_step_subtitle：无配音跳过 / 空 audio_urls / 字幕失败
- _step_edit：空音轨跳过片段、无可合成片段与剪辑失败 → RuntimeError
- _step_quality / _step_visual_quality：失败与异常 fail-open 记录
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.schemas import (
    AgentResponse,
    Character,
    PipelineRunRequest,
    Scene,
    Script,
    VideoRequest,
)
from app.services.pipeline_orchestrator import PipelineOrchestrator


@pytest.fixture
def orch():
    return PipelineOrchestrator()


def _script(characters=None, scenes=None) -> Script:
    if characters is None:
        characters = [Character(character_id="char_001", name="林远", role="主角")]
    if scenes is None:
        scenes = [
            Scene(
                scene_id=1,
                episode=1,
                description="深夜便利店",
                prompt="convenience store at night",
                dialogue="最后一杯咖啡了。",
            )
        ]
    return Script(
        project_id="p",
        title="便利店之夜",
        genre="都市悬疑",
        total_episodes=1,
        characters=characters,
        scenes=scenes,
    )


def _request(**kw) -> PipelineRunRequest:
    defaults = dict(premise="深夜便利店偶遇", generate_character_refs=True)
    defaults.update(kw)
    return PipelineRunRequest(**defaults)


class TestStepCharactersBranches:
    """_step_characters：空目标短路 + 单角色失败不阻断。"""

    async def test_no_targets_marks_skipped(self, orch):
        report: dict = {"steps": {}}
        await orch._step_characters("t-char-skip", _script(characters=[]), _request(), report)

        assert report["steps"]["character"] == {"skipped": True, "reason": "no characters"}

    async def test_character_failure_recorded_not_raised(self, orch):
        with patch("app.services.pipeline_orchestrator.character_agent") as m_char:
            m_char.execute = AsyncMock(
                return_value=AgentResponse(success=False, error="图像后端超时")
            )
            report: dict = {"steps": {}}
            await orch._step_characters("t-char-fail", _script(), _request(), report)

        results = report["steps"]["character"]["results"]
        assert len(results) == 1
        assert results[0]["success"] is False
        assert results[0]["error"] == "图像后端超时"
        assert results[0]["character_id"] == "char_001"
        assert results[0]["name"] == "林远"


class TestStepStoryboardFailures:
    """_step_storyboard：批量失败与全场景失败均抛 RuntimeError。"""

    async def test_batch_failure_raises(self, orch):
        with patch("app.services.pipeline_orchestrator.storyboard_agent") as m_sb:
            m_sb.batch_execute = AsyncMock(
                return_value=AgentResponse(success=False, error="图像后端不可用")
            )
            with pytest.raises(RuntimeError, match="分镜生成失败: 图像后端不可用"):
                await orch._step_storyboard("t-sb-fail", _script(), _request(), {"steps": {}})

    async def test_all_scenes_failed_raises(self, orch):
        with patch("app.services.pipeline_orchestrator.storyboard_agent") as m_sb:
            m_sb.batch_execute = AsyncMock(
                return_value=AgentResponse(
                    success=True, data={"results": [], "failed_scenes": [1]}
                )
            )
            with pytest.raises(RuntimeError, match="分镜生成失败: 全部场景失败"):
                await orch._step_storyboard("t-sb-empty", _script(), _request(), {"steps": {}})


class TestStepVideoFailures:
    """_step_video：批量整体失败抛 RuntimeError（成功响应但 results 为空已由既有套件覆盖）。"""

    async def test_batch_execute_failure_raises(self, orch):
        with patch("app.services.pipeline_orchestrator.video_agent") as m_video:
            m_video.batch_execute = AsyncMock(
                return_value=AgentResponse(success=False, error="全部 worker 离线")
            )
            storyboards = [{"scene_id": 1, "image_url": "/static/storyboard/scene_1.png"}]
            # characters=[] → 不触碰真实角色资产库
            with pytest.raises(RuntimeError, match="视频生成失败: 全部 worker 离线"):
                await orch._step_video(
                    "t-video-fail", _script(characters=[]), storyboards,
                    _request(), {"steps": {}},
                )


class TestStepVideoLongNoChunks:
    """_step_video_long：规划成功但零块 → RuntimeError。"""

    async def test_empty_plan_chunks_raises(self, orch, monkeypatch):
        monkeypatch.setattr(settings, "long_video_enabled", True)
        fake_plan = MagicMock()
        fake_plan.chunks = []
        with patch("app.services.pipeline_orchestrator.long_video_planner") as m_planner:
            m_planner.plan = MagicMock(return_value=fake_plan)
            storyboards = [{"scene_id": 1, "image_url": "/static/storyboard/scene_1.png"}]
            with pytest.raises(RuntimeError, match="长视频规划失败: 未产出任何块"):
                await orch._step_video_long(
                    "t-long-empty", _script(characters=[]), storyboards,
                    _request(video_mode="long"), {"steps": {}},
                )
        m_planner.plan.assert_called_once()


class TestRunVideoMultishotFailures:
    """_run_video_multishot：组内单场景失败计入 failed_scenes，成功场景保留。"""

    async def test_failed_scene_collected(self):
        ok = AgentResponse(
            success=True,
            data={"scene_id": 1, "video_url": "/static/video/s1.mp4", "duration_seconds": 3},
        )
        bad = AgentResponse(success=False, error="H3 OOM")
        items = [
            VideoRequest(scene_id=1, episode=1, image_url="http://x/1.png", prompt="a"),
            VideoRequest(scene_id=2, episode=1, image_url="http://x/2.png", prompt="b"),
        ]
        with patch("app.services.pipeline_orchestrator.video_agent") as m_video:
            m_video.execute_multi_shot = AsyncMock(return_value=[ok, bad])
            videos, failed = await PipelineOrchestrator._run_video_multishot(items)

        # 同集相邻 2 场景成一组，一次多镜调用
        m_video.execute_multi_shot.assert_awaited_once()
        assert [v["scene_id"] for v in videos] == [1]
        assert failed == [2]


class TestUpdateShotResultGuards:
    """update_shot_result：快照缺失 / 镜头缺失两个防御分支。"""

    def test_missing_snapshot_returns_false(self):
        assert (
            PipelineOrchestrator.update_shot_result(
                "ghost-project", 1, video_url="http://x/v.mp4", status="success"
            )
            is False
        )

    def test_missing_scene_returns_false(self, orch):
        orch._save_shot_params(
            "boost-shot", [VideoRequest(scene_id=1, image_url="http://x/1.png", prompt="p")]
        )
        assert (
            PipelineOrchestrator.update_shot_result(
                "boost-shot", 999, video_url="http://x/v.mp4", status="success"
            )
            is False
        )


class TestStepVoiceSkip:
    """_step_voice：全部场景无台词且无描述 → 跳过。"""

    async def test_no_dialogue_scenes_marks_skipped(self, orch):
        script = _script(scenes=[Scene(scene_id=1, dialogue="", description="")])
        report: dict = {"steps": {}}
        voices = await orch._step_voice("t-voice-skip", script, report)

        assert voices == []
        assert report["steps"]["voice"] == {"skipped": True, "reason": "no dialogue"}


class TestStepSubtitleBranches:
    """_step_subtitle：无配音跳过 / 空 audio_urls / 字幕 Agent 失败。"""

    async def test_no_voices_marks_skipped(self, orch):
        report: dict = {"steps": {}}
        subtitles = await orch._step_subtitle("t-sub-skip", [], report)

        assert subtitles == []
        assert report["steps"]["subtitle"] == {"skipped": True, "reason": "no voices"}

    async def test_voice_without_audio_urls_counted_failed(self, orch):
        report: dict = {"steps": {}}
        voices = [{"scene_id": 1, "audio_urls": []}]
        subtitles = await orch._step_subtitle("t-sub-noaudio", voices, report)

        assert subtitles == []
        assert report["steps"]["subtitle"]["count"] == 0
        assert report["steps"]["subtitle"]["failed"] == 1

    async def test_subtitle_agent_failure_skipped(self, orch):
        with patch("app.services.pipeline_orchestrator.subtitle_agent") as m_sub:
            m_sub.execute = AsyncMock(
                return_value=AgentResponse(success=False, error="ASR 服务不可用")
            )
            report: dict = {"steps": {}}
            voices = [{"scene_id": 1, "audio_urls": [{"audio_url": "/static/audio/s1.mp3"}]}]
            subtitles = await orch._step_subtitle("t-sub-fail", voices, report)

        assert subtitles == []
        assert report["steps"]["subtitle"]["count"] == 0
        assert report["steps"]["subtitle"]["failed"] == 1


class TestStepEditBranches:
    """_step_edit：空音轨场景跳过片段（全部跳过时抛错）；剪辑 Agent 失败抛错。"""

    async def test_empty_audio_urls_segment_skipped_then_raises(self, orch):
        """场景三件套齐备但音轨为空 → 片段被跳过 → 无片段可合成抛 RuntimeError。"""
        videos = [{"scene_id": 1, "video_url": "/static/video/s1.mp4", "duration_seconds": 3}]
        voices = [{"scene_id": 1, "audio_urls": []}]
        subtitles = [{"scene_id": 1, "srt_url": "/static/subtitle/s1.srt"}]
        with pytest.raises(RuntimeError, match="没有可合成的片段"):
            await orch._step_edit(
                "t-edit-empty", "p1", _script(), videos, voices, subtitles,
                _request(), {"steps": {}},
            )

    async def test_edit_agent_failure_raises(self, orch):
        with patch("app.services.pipeline_orchestrator.edit_agent") as m_edit:
            m_edit.execute = AsyncMock(
                return_value=AgentResponse(success=False, error="ffmpeg 崩溃")
            )
            videos = [{"scene_id": 1, "video_url": "/static/video/s1.mp4", "duration_seconds": 3}]
            voices = [{"scene_id": 1, "audio_urls": [{"audio_url": "/static/audio/s1.mp3"}]}]
            subtitles = [{"scene_id": 1, "srt_url": "/static/subtitle/s1.srt"}]
            with pytest.raises(RuntimeError, match="剪辑合成失败: ffmpeg 崩溃"):
                await orch._step_edit(
                    "t-edit-fail", "p1", _script(), videos, voices, subtitles,
                    _request(), {"steps": {}},
                )


class TestStepQualityBranches:
    """_step_quality：质检失败/异常均 fail-open 记录 skipped，不阻断流水线。"""

    async def test_quality_failure_recorded(self, orch):
        with patch("app.services.pipeline_orchestrator.quality_agent") as m_quality:
            m_quality.execute = AsyncMock(
                return_value=AgentResponse(success=False, error="LLM 超时")
            )
            report: dict = {"steps": {}}
            await orch._step_quality("t-qc-fail", "p1", _script(), [], report)

        step = report["steps"]["quality"]
        assert step["skipped"] is True
        assert step["reason"] == "LLM 超时"

    async def test_quality_exception_recorded(self, orch):
        with patch("app.services.pipeline_orchestrator.quality_agent") as m_quality:
            m_quality.execute = AsyncMock(side_effect=RuntimeError("质检服务崩溃"))
            report: dict = {"steps": {}}
            await orch._step_quality("t-qc-exc", "p1", _script(), [], report)

        step = report["steps"]["quality"]
        assert step["skipped"] is True
        assert "质检服务崩溃" in step["reason"]


class TestStepVisualQualityBranches:
    """_step_visual_quality：无视频跳过；检测过程异常 fail-open 记录 skipped。"""

    async def test_no_videos_marks_skipped(self, orch):
        report: dict = {"steps": {}}
        await orch._step_visual_quality("t-vqc-skip", "p1", _script(), [], report)

        assert report["steps"]["visual_quality"] == {
            "skipped": True,
            "reason": "no videos",
        }

    async def test_gather_exception_recorded(self, orch):
        with (
            patch.object(
                PipelineOrchestrator, "_collect_character_reference_images",
                return_value=["http://x/char_front.png"],
            ),
            patch("app.services.pipeline_orchestrator.visual_quality_agent") as m_visual,
        ):
            m_visual.execute = AsyncMock(side_effect=RuntimeError("VLM 集群不可达"))
            report: dict = {"steps": {}}
            videos = [{"scene_id": 1, "video_url": "/static/video/s1.mp4"}]
            await orch._step_visual_quality("t-vqc-exc", "p1", _script(), videos, report)

        step = report["steps"]["visual_quality"]
        assert step["skipped"] is True
        assert "VLM 集群不可达" in step["reason"]
