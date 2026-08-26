"""M21.3 LongVideoPlanner × pipeline_orchestrator 端到端集成测试。

覆盖矩阵（真实 Planner + Mock Agent/LongVideoService，全链路 _run 级）：

正常流程
- happy path：剧本 → 拆块规划 → 长视频生成 → 成片落报告，progress=100
- 数据格式契约：chunk.prompt/intent 序列原样透传 LongVideoService.generate，
  首帧取首个分镜关键帧，角色参考图/画风/负面词与标准模式同源

边界情况
- 单场景剧本 → 1 块（boundary=start，coherence=1.0）
- 超时长场景（30s > 14s 块长）→ 续写子块（continuation 前缀 + 边界标注）
- 跨集场景 → episode 强制边界
- max_chunks 截断 → warnings 记录 + 覆盖度下降

异常场景
- long_video_enabled=False → 明确报错「长视频模式未启用」
- LongVideoService 抛 LongVideoError → 流水线 failed，错误信息含「长视频生成失败」
- 空场景剧本 → Planner ValueError 映射为「长视频规划失败」
- 无分镜关键帧 → 「需要至少 1 个分镜关键帧」

向后兼容
- video_mode 缺省 = standard，标准路径不受影响
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.schemas import (
    AgentResponse,
    PipelineRunRequest,
    Script,
)
from app.services.long_video_service import LongVideoError, LongVideoResult
from app.services.pipeline_orchestrator import PipelineOrchestrator


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------
def _scene(
    scene_id: int,
    *,
    episode: int = 1,
    shot_type: str = "中景",
    description: str = "深夜便利店，货架前",
    prompt: str = "convenience store at night",
    emotion: str = "tension",
    duration: int = 5,
    camera: str = "static",
    beat: str = "hook",
) -> dict:
    return {
        "scene_id": scene_id,
        "episode": episode,
        "shot_type": shot_type,
        "description": description,
        "prompt": prompt,
        "dialogue": "",
        "character_actions": "",
        "emotion": emotion,
        "duration_seconds": duration,
        "camera_movement": camera,
        "narrative_beat": beat,
    }


def _script_data(scenes: list[dict]) -> dict:
    return {
        "project_id": "",
        "title": "长视频测试剧",
        "genre": "都市悬疑",
        "aspect_ratio": "9:16",
        "total_episodes": max((s["episode"] for s in scenes), default=1),
        "characters": [
            {"character_id": "char_001", "name": "林远", "role": "主角", "description": "疲惫程序员"},
        ],
        "scenes": scenes,
    }


def _long_result(tmp_path: Path, chunks: int = 2, duration: float = 10.0) -> LongVideoResult:
    final = tmp_path / "long_video.mp4"
    final.write_bytes(b"fake-long-video")
    return LongVideoResult(
        video_path=final,
        chunk_paths=[tmp_path / f"chunk_{i:02d}.mp4" for i in range(chunks)],
        chunks_completed=chunks,
        duration_seconds=duration,
        elapsed_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def long_req() -> PipelineRunRequest:
    return PipelineRunRequest(
        premise="深夜便利店偶遇",
        scenes_per_episode=3,
        generate_character_refs=False,
        run_quality_check=True,
        video_mode="long",
    )


@pytest.fixture
def mocks(tmp_path, monkeypatch):
    """Mock 全部 Agent + LongVideoService；Planner 用真实实现（纯确定性规则）。

    settings 用 monkeypatch 改 app.config 单例属性——orchestrator 与 planner
    共享同一 settings 实例，保证块长/上限/开关对两侧同时生效。
    """
    monkeypatch.setattr(settings, "long_video_enabled", True)
    monkeypatch.setattr(settings, "long_video_chunk_seconds", 14)
    monkeypatch.setattr(settings, "long_video_max_chunks", 10)
    with (
        patch("app.services.pipeline_orchestrator.script_agent") as m_script,
        patch("app.services.pipeline_orchestrator.character_agent") as m_char,
        patch("app.services.pipeline_orchestrator.storyboard_agent") as m_sb,
        patch("app.services.pipeline_orchestrator.video_agent") as m_video,
        patch("app.services.pipeline_orchestrator.voice_agent") as m_voice,
        patch("app.services.pipeline_orchestrator.subtitle_agent") as m_sub,
        patch("app.services.pipeline_orchestrator.edit_agent") as m_edit,
        patch("app.services.pipeline_orchestrator.quality_agent") as m_quality,
        patch("app.services.pipeline_orchestrator.visual_quality_agent") as m_visual,
        patch("app.services.pipeline_orchestrator.character_library") as m_charlib,
        patch("app.services.pipeline_orchestrator.LongVideoService") as m_lv_cls,
    ):
        m_charlib.get = MagicMock(return_value=None)
        m_script.execute = AsyncMock(
            return_value=AgentResponse(success=True, data=_script_data([
                _scene(1, description="深夜便利店，货架前", prompt="aisle browsing"),
                _scene(2, description="便利店门口，雨下大了", prompt="store entrance rain",
                       emotion="tension", beat="escalation"),
                _scene(3, description="天台上，两人对峙", prompt="rooftop confrontation",
                       shot_type="远景", emotion="determined", beat="cliffhanger"),
            ]))
        )
        m_sb.batch_execute = AsyncMock(
            return_value=AgentResponse(
                success=True,
                data={
                    "results": [
                        {"scene_id": 1, "image_url": "/static/storyboard/scene_1.png"},
                        {"scene_id": 2, "image_url": "/static/storyboard/scene_2.png"},
                        {"scene_id": 3, "image_url": "/static/storyboard/scene_3.png"},
                    ],
                    "failed_scenes": [],
                },
            )
        )
        m_lv = m_lv_cls.return_value
        m_lv.generate = AsyncMock(return_value=_long_result(tmp_path))
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
            "visual": m_visual,
            "charlib": m_charlib,
            "lv_cls": m_lv_cls,
            "lv": m_lv,
        }


async def _wait_done(orch: PipelineOrchestrator, task_id: str, timeout: float = 5.0) -> None:
    handle = orch._handles.get(task_id)
    assert handle is not None
    await asyncio.wait_for(handle, timeout=timeout)


def _record(task_id: str):
    from app.core.progress import progress_tracker

    rec = progress_tracker.get(task_id)
    assert rec is not None
    return rec


# ---------------------------------------------------------------------------
# 正常流程
# ---------------------------------------------------------------------------
class TestLongModeHappyPath:
    async def test_full_pipeline_completes(self, long_req, mocks):
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "completed"
        assert rec.percent == 100
        result = rec.result
        assert result["passed"] is True

        # 规划产物落报告（含块/切换点/覆盖度）
        plan = result["steps"]["long_video_plan"]
        assert plan["chunks"], "规划块为空"
        assert plan["scene_coverage"] == 1.0
        assert "shot_switches" in plan

        # 长视频步骤记录模式与时长
        video_step = result["steps"]["video"]
        assert video_step["mode"] == "long"
        assert video_step["chunks"] == 2

        # 长视频直接作成片；配音/字幕标注跳过
        assert result["steps"]["edit"]["mode"] == "long"
        assert result["steps"]["edit"]["final_video_url"].endswith("long_video.mp4")
        assert result["steps"]["voice"]["skipped"] is True
        assert result["steps"]["subtitle"]["skipped"] is True

        # 文本质检仍执行（空字幕入参，QoS 由 quality_agent mock 保障）
        assert result["steps"]["quality"]["score"] == 88

        # 标准逐场景路径的 Agent 未被触碰
        mocks["video"].batch_execute.assert_not_called()
        mocks["voice"].execute.assert_not_called()
        mocks["subtitle"].execute.assert_not_called()
        mocks["edit"].execute.assert_not_called()

    async def test_data_format_contract(self, long_req, mocks):
        """Planner chunk → LongVideoService.generate 的数据格式契约。"""
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)
        assert _record(task_id).status == "completed"

        mocks["lv_cls"].assert_called_once_with()
        kwargs = mocks["lv"].generate.call_args.kwargs
        # 首帧 = 首个分镜关键帧
        assert kwargs["first_frame_url"] == "/static/storyboard/scene_1.png"
        # chunk_prompts 与报告中的规划块一一对应（prompt 原样透传）
        plan_chunks = _record(task_id).result["steps"]["long_video_plan"]["chunks"]
        assert kwargs["chunk_prompts"] == [c["prompt"] for c in plan_chunks]
        assert all(isinstance(p, str) and p for p in kwargs["chunk_prompts"])
        # 画风与标准模式同源透传；负面词含质量兜底词
        assert kwargs["style"] == long_req.style
        assert "blurry" in kwargs["negative_prompt"]
        # 无角色资产 → 参考图为空列表（非 None，类型稳定）
        assert kwargs["reference_images"] == []

    async def test_progress_channel_stable(self, long_req, mocks, tmp_path):
        """长视频进度回调经 _progress 映射进 video 区间（40-70），不越界。"""
        def gen(**kwargs):
            cb = kwargs["progress_callback"]
            cb(0, "块 1/2 生成中")
            cb(50, "块 2/2 生成中")
            cb(100, "完成")
            return _long_result(tmp_path)

        mocks["lv"].generate = AsyncMock(side_effect=gen)
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)
        rec = _record(task_id)
        assert rec.status == "completed"
        assert rec.percent == 100


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------
class TestLongModeBoundaries:
    async def test_single_scene_single_chunk(self, long_req, mocks):
        mocks["script"].execute = AsyncMock(
            return_value=AgentResponse(success=True, data=_script_data([_scene(1)]))
        )
        mocks["storyboard"].batch_execute = AsyncMock(
            return_value=AgentResponse(
                success=True,
                data={"results": [{"scene_id": 1, "image_url": "/static/storyboard/scene_1.png"}],
                      "failed_scenes": []},
            )
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "completed"
        chunks = rec.result["steps"]["long_video_plan"]["chunks"]
        assert len(chunks) == 1
        assert chunks[0]["boundary_before"] == "start"
        assert chunks[0]["coherence_to_prev"] == 1.0
        assert chunks[0]["scene_ids"] == [1]

    async def test_oversized_scene_continuation(self, long_req, mocks, tmp_path, monkeypatch):
        """30s 单场景按 14s 块长拆 3 续写子块（14/14/2），后续块带 continuation。"""
        monkeypatch.setattr(settings, "long_video_chunk_seconds", 14)
        mocks["script"].execute = AsyncMock(
            return_value=AgentResponse(success=True, data=_script_data([
                _scene(1, duration=30, prompt="long tracking shot in store"),
            ]))
        )
        mocks["lv"].generate = AsyncMock(return_value=_long_result(tmp_path, chunks=3, duration=30.0))
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "completed"
        chunks = rec.result["steps"]["long_video_plan"]["chunks"]
        assert [c["estimated_seconds"] for c in chunks] == [14.0, 14.0, 2.0]
        assert chunks[1]["boundary_before"] == "continuation"
        assert chunks[1]["prompt"].startswith("The shot continues smoothly")
        assert chunks[2]["boundary_before"] == "continuation"
        # 透传到 LongVideoService 的块数一致
        assert len(mocks["lv"].generate.call_args.kwargs["chunk_prompts"]) == 3

    async def test_episode_forces_boundary(self, long_req, mocks):
        mocks["script"].execute = AsyncMock(
            return_value=AgentResponse(success=True, data=_script_data([
                _scene(1, episode=1),
                _scene(2, episode=2, description="天台", beat="escalation"),
            ]))
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "completed"
        chunks = rec.result["steps"]["long_video_plan"]["chunks"]
        assert len(chunks) == 2
        assert chunks[1]["boundary_before"] == "episode"

    async def test_max_chunks_truncation_warns(self, long_req, mocks, monkeypatch):
        """块数超 max_chunks 截断：warnings 记录 + 场景覆盖度 < 1。"""
        monkeypatch.setattr(settings, "long_video_max_chunks", 1)
        mocks["script"].execute = AsyncMock(
            return_value=AgentResponse(success=True, data=_script_data([
                _scene(i, description=f"便利店第{i}幕", beat="") for i in range(1, 5)
            ]))
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "completed"
        plan = rec.result["steps"]["long_video_plan"]
        assert len(plan["chunks"]) == 1
        assert plan["scene_coverage"] < 1.0
        assert any("截断" in w for w in plan["warnings"])


# ---------------------------------------------------------------------------
# 异常场景
# ---------------------------------------------------------------------------
class TestLongModeFailures:
    async def test_disabled_gate_fails_fast(self, long_req, mocks, monkeypatch):
        monkeypatch.setattr(settings, "long_video_enabled", False)
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "failed"
        assert "长视频模式未启用" in rec.error
        mocks["lv"].generate.assert_not_called()

    async def test_generation_error_fails_pipeline(self, long_req, mocks):
        mocks["lv"].generate = AsyncMock(side_effect=LongVideoError("块 2/3 生成失败: 超时"))
        orch = PipelineOrchestrator()
        task_id = orch.start(long_req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "failed"
        assert "长视频生成失败" in rec.error
        assert "块 2/3" in rec.error

    async def test_empty_script_planner_error(self, long_req, mocks):
        """空场景剧本：Planner ValueError 映射为 RuntimeError「长视频规划失败」。"""
        orch = PipelineOrchestrator()
        script = Script(**_script_data([]))
        req = PipelineRunRequest(premise="x", video_mode="long", generate_character_refs=False)
        with pytest.raises(RuntimeError, match="长视频规划失败"):
            await orch._step_video_long("t-empty", script, [{"scene_id": 1, "image_url": "u"}], req, {})

    async def test_no_storyboard_fails(self, long_req, mocks):
        orch = PipelineOrchestrator()
        script = Script(**_script_data([_scene(1)]))
        req = PipelineRunRequest(premise="x", video_mode="long", generate_character_refs=False)
        with pytest.raises(RuntimeError, match="需要至少 1 个分镜关键帧"):
            await orch._step_video_long("t-nosb", script, [], req, {})


# ---------------------------------------------------------------------------
# 向后兼容
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    def test_video_mode_defaults_standard(self):
        req = PipelineRunRequest(premise="x")
        assert req.video_mode == "standard"

    async def test_standard_mode_untouched_by_planner(self, mocks):
        """standard 模式：Planner/LongVideoService 完全不介入（长视频开关即使开启）。"""
        mocks["video"].batch_execute = AsyncMock(
            return_value=AgentResponse(
                success=True,
                data={
                    "results": [
                        {"scene_id": i, "video_url": f"/static/video/scene_{i}.mp4", "duration_seconds": 3}
                        for i in (1, 2, 3)
                    ],
                    "failed_scenes": [],
                },
            )
        )
        mocks["voice"].execute = AsyncMock(
            side_effect=lambda req: AgentResponse(
                success=True,
                data={"scene_id": req.scene_id,
                      "audio_urls": [{"audio_url": f"/static/audio/scene_{req.scene_id}.mp3"}],
                      "total_lines": 1},
            )
        )
        mocks["subtitle"].execute = AsyncMock(
            side_effect=lambda req: AgentResponse(
                success=True,
                data={"scene_id": req.scene_id,
                      "srt_url": f"/static/subtitle/scene_{req.scene_id}.srt",
                      "segments": [{"start": 0, "end": 1, "text": "x"}]},
            )
        )
        mocks["edit"].execute = AsyncMock(
            return_value=AgentResponse(
                success=True,
                data={"project_id": "p", "title": "长视频测试剧",
                      "final_video_url": "/static/final/p.mp4",
                      "duration_seconds": 9.0, "segments_count": 3},
            )
        )
        req = PipelineRunRequest(
            premise="深夜便利店偶遇", generate_character_refs=False, run_quality_check=True,
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(req)
        await _wait_done(orch, task_id)

        rec = _record(task_id)
        assert rec.status == "completed"
        assert "long_video_plan" not in rec.result["steps"]
        assert rec.result["steps"]["edit"]["final_video_url"] == "/static/final/p.mp4"
        mocks["lv_cls"].assert_not_called()
