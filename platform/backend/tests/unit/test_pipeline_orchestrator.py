"""M7 全链路编排服务单元测试。

覆盖：
- 完整流水线 happy path（8 步骤全通，progress 达 100）
- 剧本失败 → 整体失败
- 配音单场景失败 → 该场景跳过，其余场景正常成片
- 取消标志 → 任务标记 cancelled
- _build_dialogues 台词解析
- 进度区间映射 _progress
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.models.schemas import AgentResponse, Character, PipelineRunRequest, Scene
from app.services.pipeline_orchestrator import (
    PipelineCancelledError,
    PipelineOrchestrator,
    _PROGRESS,
)


def _script_data() -> dict:
    return {
        "project_id": "",
        "title": "便利店之夜",
        "genre": "都市悬疑",
        "aspect_ratio": "9:16",
        "total_episodes": 1,
        "characters": [
            {"character_id": "char_001", "name": "林远", "role": "主角", "description": "疲惫程序员"},
            {"character_id": "char_002", "name": "苏晚", "role": "配角", "description": "神秘女顾客"},
        ],
        "scenes": [
            {
                "scene_id": 1,
                "episode": 1,
                "shot_type": "中景",
                "description": "深夜便利店，程序员盯着货架",
                "prompt": "convenience store at night",
                "dialogue": "最后一杯咖啡了。",
                "character_actions": "主角林远拿起咖啡",
            },
            {
                "scene_id": 2,
                "episode": 1,
                "shot_type": "特写",
                "description": "女顾客伸手同时拿住咖啡杯",
                "prompt": "two hands on one coffee cup",
                "dialogue": "这杯，能让给我吗？",
                "character_actions": "配角苏晚微笑",
            },
        ],
    }


@pytest.fixture
def pipeline_req() -> PipelineRunRequest:
    return PipelineRunRequest(
        premise="深夜便利店偶遇",
        scenes_per_episode=2,
        generate_character_refs=False,
        run_quality_check=True,
    )


@pytest.fixture
def mocks():
    """Mock 全部 Agent 与全局 orchestrator 实例的依赖。"""
    with (
        patch("app.services.pipeline_orchestrator.script_agent") as m_script,
        patch("app.services.pipeline_orchestrator.character_agent") as m_char,
        patch("app.services.pipeline_orchestrator.storyboard_agent") as m_sb,
        patch("app.services.pipeline_orchestrator.video_agent") as m_video,
        patch("app.services.pipeline_orchestrator.voice_agent") as m_voice,
        patch("app.services.pipeline_orchestrator.subtitle_agent") as m_sub,
        patch("app.services.pipeline_orchestrator.edit_agent") as m_edit,
        patch("app.services.pipeline_orchestrator.quality_agent") as m_quality,
    ):
        m_script.execute = AsyncMock(return_value=AgentResponse(success=True, data=_script_data()))
        m_char.execute = AsyncMock(
            return_value=AgentResponse(success=True, data={"character_id": "char_001", "name": "林远"})
        )
        m_sb.batch_execute = AsyncMock(
            return_value=AgentResponse(
                success=True,
                data={
                    "results": [
                        {"scene_id": 1, "image_url": "/static/storyboard/scene_1.png"},
                        {"scene_id": 2, "image_url": "/static/storyboard/scene_2.png"},
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
                        {"scene_id": 1, "video_url": "/static/video/scene_1.mp4", "duration_seconds": 3},
                        {"scene_id": 2, "video_url": "/static/video/scene_2.mp4", "duration_seconds": 3},
                    ],
                    "failed_scenes": [],
                },
            )
        )
        m_voice.execute = AsyncMock(
            side_effect=lambda req: AgentResponse(
                success=True,
                data={
                    "scene_id": req.scene_id,
                    "audio_urls": [{"audio_url": f"/static/audio/scene_{req.scene_id}.mp3"}],
                    "total_lines": 1,
                },
            )
        )
        m_sub.execute = AsyncMock(
            side_effect=lambda req: AgentResponse(
                success=True,
                data={
                    "scene_id": req.scene_id,
                    "srt_url": f"/static/subtitle/scene_{req.scene_id}.srt",
                    "segments": [{"start": 0, "end": 1, "text": "x"}],
                },
            )
        )
        m_edit.execute = AsyncMock(
            return_value=AgentResponse(
                success=True,
                data={
                    "project_id": "p",
                    "title": "便利店之夜",
                    "final_video_url": "/static/final/p.mp4",
                    "duration_seconds": 6.0,
                    "segments_count": 2,
                },
            )
        )
        m_quality.execute = AsyncMock(
            return_value=AgentResponse(success=True, data={"score": 88, "issues": []})
        )
        yield {
            "script": m_script,
            "character": m_char,
            "storyboard": m_sb,
            "video": m_video,
            "voice": m_voice,
            "subtitle": m_sub,
            "edit": m_edit,
            "quality": m_quality,
        }


async def _wait_done(orch: PipelineOrchestrator, task_id: str, timeout: float = 5.0) -> None:
    handle = orch._handles.get(task_id)
    assert handle is not None
    await asyncio.wait_for(handle, timeout=timeout)


class TestPipelineHappyPath:
    async def test_full_pipeline_completes(self, pipeline_req, mocks):
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record is not None
        assert record.status == "completed"
        assert record.percent == 100
        result = record.result
        assert result["passed"] is True
        assert result["steps"]["script"]["title"] == "便利店之夜"
        # M9 报告内嵌完整剧本数据，供前端「加载到画布」回填
        script_data = result["steps"]["script"]["data"]
        assert script_data["title"] == "便利店之夜"
        assert "project_id" in script_data
        assert isinstance(script_data["characters"], list) and len(script_data["characters"]) >= 1
        assert isinstance(script_data["scenes"], list) and len(script_data["scenes"]) == 2
        assert script_data["scenes"][0]["scene_id"] is not None
        assert result["steps"]["character"]["skipped"] is True
        assert result["steps"]["storyboard"]["count"] == 2
        assert result["steps"]["video"]["count"] == 2
        assert result["steps"]["voice"]["count"] == 2
        assert result["steps"]["subtitle"]["count"] == 2
        assert result["steps"]["edit"]["final_video_url"] == "/static/final/p.mp4"
        assert result["steps"]["quality"]["score"] == 88

    async def test_character_refs_generated_when_enabled(self, mocks):
        req = PipelineRunRequest(
            premise="深夜便利店偶遇",
            scenes_per_episode=2,
            generate_character_refs=True,
            max_character_refs=1,
            run_quality_check=False,
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        assert mocks["character"].execute.call_count == 1
        assert record.result["steps"]["character"]["results"][0]["success"] is True
        assert "quality" not in record.result["steps"]


class TestPipelineFailures:
    async def test_script_failure_fails_pipeline(self, pipeline_req, mocks):
        mocks["script"].execute = AsyncMock(
            return_value=AgentResponse(success=False, error="LLM 超时")
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "failed"
        assert "剧本生成失败" in record.error
        mocks["storyboard"].batch_execute.assert_not_called()

    async def test_voice_partial_failure_skips_scene(self, pipeline_req, mocks):
        async def voice_side_effect(req):
            if req.scene_id == 2:
                return AgentResponse(success=False, error="TTS 故障")
            return AgentResponse(
                success=True,
                data={
                    "scene_id": req.scene_id,
                    "audio_urls": [{"audio_url": f"/static/audio/scene_{req.scene_id}.mp3"}],
                    "total_lines": 1,
                },
            )

        mocks["voice"].execute = AsyncMock(side_effect=voice_side_effect)
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        assert record.result["steps"]["voice"]["count"] == 1
        assert record.result["steps"]["voice"]["failed"] == 1
        # 剪辑只合成场景 1
        edit_req = mocks["edit"].execute.call_args.args[0]
        assert [s.scene_id for s in edit_req.segments] == [1]

    async def test_all_videos_fail_fails_pipeline(self, pipeline_req, mocks):
        mocks["video"].batch_execute = AsyncMock(
            return_value=AgentResponse(success=True, data={"results": [], "failed_scenes": [1, 2]})
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "failed"
        assert "视频生成失败" in record.error


class TestPipelineCancel:
    async def test_cancel_after_script(self, pipeline_req, mocks):
        orch = PipelineOrchestrator()

        original_execute = mocks["storyboard"].batch_execute

        async def slow_storyboard(req):
            # 剧本完成后立即请求取消，分镜结束后应触发取消
            orch.cancel(task_id)
            return await original_execute(req)

        mocks["storyboard"].batch_execute = AsyncMock(side_effect=slow_storyboard)
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "failed"
        assert record.error == "cancelled by user"
        assert record.result["cancelled"] is True

    async def test_cancel_unknown_task_returns_false(self):
        orch = PipelineOrchestrator()
        assert orch.cancel("pipeline-nonexistent") is False


class TestHelpers:
    def test_build_dialogues_multiline(self):
        scene = Scene(
            scene_id=1,
            description="便利店",
            dialogue="第一句。\n\n第二句。",
            character_actions="主角林远",
        )
        dialogues = PipelineOrchestrator._build_dialogues(scene)
        assert len(dialogues) == 2
        assert dialogues[0].text == "第一句。"
        assert dialogues[0].character_role == "主角"

    def test_build_dialogues_fallback_narrator(self):
        scene = Scene(scene_id=1, description="空场景", dialogue="")
        dialogues = PipelineOrchestrator._build_dialogues(scene)
        assert len(dialogues) == 1
        assert dialogues[0].character_role == "narrator"
        assert dialogues[0].text == "空场景"

    def test_progress_mapping_monotonic(self):
        orch = PipelineOrchestrator()
        with patch("app.services.pipeline_orchestrator.progress_tracker") as m_tracker:
            orch._progress("t1", "script", 0.0, "a")
            p0 = m_tracker.update.call_args.kwargs["percent"]
            orch._progress("t1", "script", 1.0, "b")
            p1 = m_tracker.update.call_args.kwargs["percent"]
            orch._progress("t1", "video", 0.0, "c")
            p2 = m_tracker.update.call_args.kwargs["percent"]
            orch._progress("t1", "quality", 1.0, "d")
            p3 = m_tracker.update.call_args.kwargs["percent"]
        assert p0 == _PROGRESS["script"][0]
        assert p1 == _PROGRESS["script"][1]
        assert p2 == _PROGRESS["video"][0]
        assert p3 == 100
        assert p0 < p1 < p2 < p3

    def test_check_cancel_raises_when_set(self):
        event = asyncio.Event()
        event.set()
        with pytest.raises(PipelineCancelledError):
            PipelineOrchestrator._check_cancel(event)
