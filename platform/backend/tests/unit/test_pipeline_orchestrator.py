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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import (
    AgentResponse,
    Character,
    CharacterAsset,
    PipelineRunRequest,
    Scene,
    VideoBatchRequest,
)
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
        patch("app.services.pipeline_orchestrator.visual_quality_agent") as m_visual,
        patch("app.services.pipeline_orchestrator.character_library") as m_charlib,
    ):
        # 默认角色资产库无登记资产 → 视频请求不携带参考图（走 fl2va）
        m_charlib.get = MagicMock(return_value=None)
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
        m_visual.execute = AsyncMock(
            side_effect=lambda req: AgentResponse(
                success=True,
                data={"score": 90, "drift_detected": False, "scene_id": req.scene_id},
            )
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


class TestCharacterReferenceInjection:
    """M10+: 视频步骤注入角色资产库三视图参考图（H3 ref2va 角色一致性）。

    Scene 无角色关联字段，故取剧本全部角色在资产库中的 reference_images，
    去重、保序、≤h3_ref_max_images 后合入每个 VideoRequest.reference_images。
    """

    @staticmethod
    def _asset(character_id: str, images: dict[str, str]) -> CharacterAsset:
        return CharacterAsset(
            character_id=character_id,
            name=character_id,
            reference_images=images,
        )

    async def test_video_requests_carry_reference_images(self, pipeline_req, mocks):
        """角色在资产库有参考图 → 每个 VideoRequest 携带按序合并的参考图列表。"""
        mocks["charlib"].get = MagicMock(
            side_effect=lambda cid: {
                "char_001": self._asset("char_001", {
                    "front": "http://x/c1_front.png",
                    "side": "http://x/c1_side.png",
                    "closeup": "http://x/c1_close.png",
                }),
                "char_002": self._asset("char_002", {
                    "front": "http://x/c2_front.png",
                    "side": "http://x/c2_side.png",
                }),
            }.get(cid)
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        assert isinstance(batch_req, VideoBatchRequest)
        expected = [
            "http://x/c1_front.png",
            "http://x/c1_side.png",
            "http://x/c1_close.png",
            "http://x/c2_front.png",
            "http://x/c2_side.png",
        ]
        assert len(batch_req.items) == 2
        for item in batch_req.items:
            assert item.reference_images == expected

    async def test_no_assets_sends_empty_reference_images(self, pipeline_req, mocks):
        """资产库无登记资产（fixture 默认 get→None）→ reference_images 为空（走 fl2va）。"""
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        for item in batch_req.items:
            assert item.reference_images == []

    async def test_dedupe_and_cap_at_max_images(self, pipeline_req, mocks):
        """跨角色重复 URL 去重；总量截断到 h3_ref_max_images（9）。"""
        from app.config import settings

        def _get(cid: str) -> CharacterAsset:
            # 每个角色 5 张唯一图 + 同一张共享图（跨角色重复）
            images = {f"v{i}": f"http://x/{cid}_{i}.png" for i in range(5)}
            images["shared"] = "http://x/dup.png"
            return self._asset(cid, images)

        mocks["charlib"].get = MagicMock(side_effect=_get)
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        refs = batch_req.items[0].reference_images
        # 两角色共 10 张唯一图 + 1 张重复共享图 → 去重后截断为 9
        assert len(refs) == settings.h3_ref_max_images
        assert len(refs) == len(set(refs))
        assert "http://x/dup.png" in refs

    async def test_empty_urls_filtered(self, pipeline_req, mocks):
        """资产中的空 URL 被过滤，不进入参考图列表。"""
        mocks["charlib"].get = MagicMock(
            side_effect=lambda cid: self._asset(cid, {
                "front": "http://x/only.png",
                "side": "",
            })
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        assert batch_req.items[0].reference_images == ["http://x/only.png"]


class TestVisualQualityStep:
    """M13: run_visual_check=True 时对每个视频场景做角色漂移对照检测。

    参考图来自角色资产库（_collect_character_reference_images 与视频步骤同规则）；
    无参考图或视觉 Agent 单场景失败均不阻断流水线。
    """

    @staticmethod
    def _asset(character_id: str, images: dict[str, str]) -> CharacterAsset:
        return CharacterAsset(
            character_id=character_id,
            name=character_id,
            reference_images=images,
        )

    @staticmethod
    def _with_refs(mocks) -> list[str]:
        urls = ["http://x/c1_front.png", "http://x/c1_side.png"]
        mocks["charlib"].get = MagicMock(
            side_effect=lambda cid: TestVisualQualityStep._asset(
                cid, {"front": urls[0], "side": urls[1]}
            ) if cid == "char_001" else None
        )
        return urls

    async def test_visual_check_disabled_by_default(self, pipeline_req, mocks):
        """run_visual_check 缺省 False → 不执行视觉质检步骤。"""
        assert pipeline_req.run_visual_check is False
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        assert "visual_quality" not in record.result["steps"]
        mocks["visual"].execute.assert_not_called()

    async def test_visual_check_drift_scenes_collected(self, pipeline_req, mocks):
        """场景 2 检出漂移 → drift_scenes=[2]，参考图 URL 透传到每个 QualityVisualRequest。"""
        pipeline_req.run_visual_check = True
        urls = self._with_refs(mocks)

        async def visual_side_effect(req):
            return AgentResponse(
                success=True,
                data={
                    "score": 60 if req.scene_id == 2 else 90,
                    "drift_detected": req.scene_id == 2,
                },
            )

        mocks["visual"].execute = AsyncMock(side_effect=visual_side_effect)
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        step = record.result["steps"]["visual_quality"]
        assert step["checked"] == 2
        assert step["failed_scenes"] == []
        assert step["drift_scenes"] == [2]
        # 每个请求都携带资产库参考图
        assert mocks["visual"].execute.call_count == 2
        for call in mocks["visual"].execute.call_args_list:
            assert call.args[0].reference_image_urls == urls

    async def test_visual_check_skipped_without_refs(self, pipeline_req, mocks):
        """资产库无参考图（fixture 默认 get→None）→ skipped，不调用视觉 Agent。"""
        pipeline_req.run_visual_check = True
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        step = record.result["steps"]["visual_quality"]
        assert step["skipped"] is True
        assert step["reason"] == "no character reference images"
        mocks["visual"].execute.assert_not_called()

    async def test_visual_check_scene_failure_nonfatal(self, pipeline_req, mocks):
        """单场景视觉质检失败记入 failed_scenes，流水线整体仍 completed。"""
        pipeline_req.run_visual_check = True
        self._with_refs(mocks)

        async def visual_side_effect(req):
            if req.scene_id == 1:
                return AgentResponse(success=False, error="VLM 超时")
            return AgentResponse(
                success=True, data={"score": 90, "drift_detected": False}
            )

        mocks["visual"].execute = AsyncMock(side_effect=visual_side_effect)
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        step = record.result["steps"]["visual_quality"]
        assert step["checked"] == 1
        assert step["failed_scenes"] == [1]
        assert step["drift_scenes"] == []


class TestVideoMultishotWiring:
    """M11: _step_video H3 多镜联合生成接线。

    settings.video_backend=='h3' 且 h3_multishot_enabled 时先分组：
    ≥2 场景的组调 execute_multi_shot，单场景走原逐场景 execute；
    开关关闭或非 h3 后端时全部走原 batch_execute 路径。
    """

    @staticmethod
    def _ok(scene_id: int) -> AgentResponse:
        return AgentResponse(
            success=True,
            data={
                "scene_id": scene_id,
                "video_url": f"/static/video/video_scene_{scene_id}.mp4",
                "duration_seconds": 3,
            },
        )

    async def test_same_episode_group_calls_execute_multi_shot(
        self, pipeline_req, mocks, monkeypatch
    ):
        """同集相邻 2 场景 → 一次 execute_multi_shot；batch_execute/单场景 execute 不调用。"""
        from app.config import settings

        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_multishot_enabled", True)
        mocks["video"].execute_multi_shot = AsyncMock(
            return_value=[self._ok(1), self._ok(2)]
        )
        mocks["video"].execute = AsyncMock(
            side_effect=lambda req, **kw: self._ok(req.scene_id)
        )

        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        mocks["video"].execute_multi_shot.assert_awaited_once()
        group = mocks["video"].execute_multi_shot.call_args.args[0]
        assert [r.scene_id for r in group] == [1, 2]
        mocks["video"].batch_execute.assert_not_awaited()
        mocks["video"].execute.assert_not_awaited()
        assert record.result["steps"]["video"]["count"] == 2
        # 多镜产物 video_url 进入剪辑片段
        edit_req = mocks["edit"].execute.call_args.args[0]
        assert {s.video_url for s in edit_req.segments} == {
            "/static/video/video_scene_1.mp4",
            "/static/video/video_scene_2.mp4",
        }

    async def test_cross_episode_singles_bypass_multishot(
        self, pipeline_req, mocks, monkeypatch
    ):
        """跨集场景不成组 → 各自走单场景 execute，execute_multi_shot 不调用。"""
        from app.config import settings

        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_multishot_enabled", True)
        data = _script_data()
        data["scenes"][1]["episode"] = 2
        mocks["script"].execute = AsyncMock(
            return_value=AgentResponse(success=True, data=data)
        )
        mocks["video"].execute_multi_shot = AsyncMock()
        mocks["video"].execute = AsyncMock(
            side_effect=lambda req, **kw: self._ok(req.scene_id)
        )

        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        mocks["video"].execute_multi_shot.assert_not_awaited()
        assert mocks["video"].execute.await_count == 2
        mocks["video"].batch_execute.assert_not_awaited()
        assert record.result["steps"]["video"]["count"] == 2

    async def test_multishot_disabled_uses_batch_path(
        self, pipeline_req, mocks, monkeypatch
    ):
        """h3 后端但开关关闭 → 全部走原 batch_execute 路径。"""
        from app.config import settings

        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_multishot_enabled", False)
        mocks["video"].execute_multi_shot = AsyncMock()

        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        mocks["video"].batch_execute.assert_awaited_once()
        mocks["video"].execute_multi_shot.assert_not_awaited()

    async def test_non_h3_backend_uses_batch_path(self, pipeline_req, mocks):
        """非 h3 后端（conftest 默认 comfyui）→ 全部走原 batch_execute 路径。"""
        mocks["video"].execute_multi_shot = AsyncMock()

        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        from app.core.progress import progress_tracker

        record = progress_tracker.get(task_id)
        assert record.status == "completed"
        mocks["video"].batch_execute.assert_awaited_once()
        mocks["video"].execute_multi_shot.assert_not_awaited()


class TestVideoStyleAnchoring:
    """M15.1: _step_video 给每个 VideoRequest 注入画风锚定尾与冲突画风负面词。

    与剧本/角色/分镜保持同一 StyleAnchor：prompt 末尾强制追加
    style_positive_tail，negative_prompt 注入 style_negative_tail + 通用质量负面词。
    """

    async def test_default_realistic_style_tail(self, pipeline_req, mocks):
        """默认画风「写实电影感」→ prompt 带写实风格尾，负面词排斥 anime/卡通。"""
        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        assert len(batch_req.items) == 2
        for item in batch_req.items:
            assert item.prompt.endswith(
                ", cinematic realistic, photorealistic, professional photography"
            )
            assert "anime" in item.negative_prompt
            assert "blurry" in item.negative_prompt

    async def test_non_realistic_style_tail_and_negative(self, mocks):
        """画风「国漫」→ prompt 带国漫风格尾（无 photorealistic 画质尾），负面词排斥写实。"""
        req = PipelineRunRequest(
            premise="深夜便利店偶遇",
            scenes_per_episode=2,
            generate_character_refs=False,
            run_quality_check=False,
            style="国漫",
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        for item in batch_req.items:
            assert item.prompt.endswith(", Chinese anime guoman style")
            assert "photorealistic" not in item.prompt
            assert "photorealistic" in item.negative_prompt
            assert "blurry" in item.negative_prompt

    async def test_empty_scene_prompt_stays_empty(self, pipeline_req, mocks):
        """场景无 prompt 时 video prompt 保持空串（不强行塞风格尾）。"""
        data = _script_data()
        data["scenes"][0]["prompt"] = ""
        mocks["script"].execute = AsyncMock(
            return_value=AgentResponse(success=True, data=data)
        )

        orch = PipelineOrchestrator()
        task_id = orch.start(pipeline_req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        item1 = next(i for i in batch_req.items if i.scene_id == 1)
        item2 = next(i for i in batch_req.items if i.scene_id == 2)
        assert item1.prompt == ""
        assert item2.prompt.endswith(
            ", cinematic realistic, photorealistic, professional photography"
        )

    async def test_style_propagated_to_video_request(self, mocks):
        """M18.4: request.style 透传到每个 VideoRequest.style。

        style 是 H3 画风锚定清洗（约束层）与画风 QC（检测层）的判定基准；
        漏传则两者 fail-open 静默跳过（M18.5 联合 E2E 实测发现该缺口）。
        """
        req = PipelineRunRequest(
            premise="深夜便利店偶遇",
            scenes_per_episode=2,
            generate_character_refs=False,
            run_quality_check=False,
            style="国漫",
        )
        orch = PipelineOrchestrator()
        task_id = orch.start(req)
        await _wait_done(orch, task_id)

        batch_req = mocks["video"].batch_execute.call_args.args[0]
        assert len(batch_req.items) == 2
        for item in batch_req.items:
            assert item.style == "国漫"
