"""LipSyncAgent 单元测试 — P4.4 双后端 + 降级覆盖。

覆盖：
- lip_sync_enabled=False 总开关关闭：跳过，返回原视频
- lip_sync_enabled=True 主后端成功：返回新 video_url，synced=True
- LatentSyncServiceError 主后端失败：降级返回原视频，synced=False，success=True
- TimeoutError 主后端超时：降级返回原视频
- 通用异常主后端崩溃：降级返回原视频
- 进度回调透传
- 路由层契约：AgentResponse.data 为 LipSyncResult.model_dump()
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.lip_sync_agent import LipSyncAgent
from app.config import settings
from app.models.schemas import LipSyncRequest
from app.services.latentsync_service import LatentSyncServiceError


@pytest.fixture
def agent():
    return LipSyncAgent()


def _attach_mock_latentsync(agent, return_value=None, side_effect=None):
    """注入 mock LatentSyncService 到 agent._latentsync，绕过懒加载 property。"""
    mock_svc = MagicMock()
    mock_svc.sync_lip = AsyncMock()
    if side_effect is not None:
        mock_svc.sync_lip.side_effect = side_effect
    else:
        mock_svc.sync_lip.return_value = return_value or {
            "video_url": "http://synced/out.mp4",
            "duration_seconds": 4.5,
            "task_id": "ls-task-1",
        }
    agent._latentsync = mock_svc
    return mock_svc


class TestLipSyncDisabled:
    """lip_sync_enabled=False（conftest 默认）→ 跳过，返回原视频。"""

    async def test_disabled_skips_lip_sync(self, agent):
        """总开关关闭时直接返回原视频，synced=False。"""
        request = LipSyncRequest(
            scene_id=1,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["scene_id"] == 1
        assert resp.data["video_url"] == "http://mock/v.mp4"
        assert resp.data["original_video_url"] == "http://mock/v.mp4"
        assert resp.data["synced"] is False
        assert resp.error is None

    async def test_disabled_does_not_call_service(self, agent):
        """关闭时不应调用 LatentSyncService。"""
        mock_svc = MagicMock()
        mock_svc.sync_lip = AsyncMock()
        agent._latentsync = mock_svc

        request = LipSyncRequest(
            scene_id=2,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        await agent.execute(request)

        mock_svc.sync_lip.assert_not_awaited()


class TestLipSyncEnabledSuccess:
    """lip_sync_enabled=True → 主后端成功路径。"""

    async def test_success_returns_synced_video(self, agent, monkeypatch):
        monkeypatch.setattr(settings, "lip_sync_enabled", True)
        _attach_mock_latentsync(agent)

        request = LipSyncRequest(
            scene_id=3,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["scene_id"] == 3
        assert resp.data["video_url"] == "http://synced/out.mp4"
        assert resp.data["original_video_url"] == "http://mock/v.mp4"
        assert resp.data["synced"] is True
        # LatentSync 被调用一次，参数透传
        agent._latentsync.sync_lip.assert_awaited_once()
        call_kwargs = agent._latentsync.sync_lip.await_args.kwargs
        assert call_kwargs["video_url"] == "http://mock/v.mp4"
        assert call_kwargs["audio_url"] == "http://mock/a.mp3"
        assert call_kwargs["scene_id"] == 3

    async def test_reference_image_url_propagated(self, agent, monkeypatch):
        """reference_image_url 透传到 LatentSyncService.sync_lip。"""
        monkeypatch.setattr(settings, "lip_sync_enabled", True)
        _attach_mock_latentsync(agent)

        request = LipSyncRequest(
            scene_id=4,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
            reference_image_url="http://mock/ref.png",
        )
        await agent.execute(request)

        call_kwargs = agent._latentsync.sync_lip.await_args.kwargs
        assert call_kwargs["reference_image_url"] == "http://mock/ref.png"


class TestLipSyncFallback:
    """主后端失败 → 自动降级返回原视频（success=True, synced=False）。"""

    async def test_latentsync_service_error_fallback(self, agent, monkeypatch):
        monkeypatch.setattr(settings, "lip_sync_enabled", True)
        _attach_mock_latentsync(
            agent,
            side_effect=LatentSyncServiceError("LatentSync OOM"),
        )

        request = LipSyncRequest(
            scene_id=5,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        resp = await agent.execute(request)

        # 降级视为成功（不阻断主流程）
        assert resp.success is True
        assert resp.data["synced"] is False
        assert resp.data["video_url"] == "http://mock/v.mp4"
        assert resp.data["original_video_url"] == "http://mock/v.mp4"

    async def test_timeout_fallback(self, agent, monkeypatch):
        """LatentSync 超时 → 降级返回原视频。"""
        monkeypatch.setattr(settings, "lip_sync_enabled", True)
        _attach_mock_latentsync(
            agent,
            side_effect=TimeoutError("LatentSync timeout 300s"),
        )

        request = LipSyncRequest(
            scene_id=6,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["synced"] is False
        assert resp.data["video_url"] == "http://mock/v.mp4"

    async def test_generic_exception_fallback(self, agent, monkeypatch):
        """通用异常（如 RuntimeError）→ 仍降级返回原视频。"""
        monkeypatch.setattr(settings, "lip_sync_enabled", True)
        _attach_mock_latentsync(
            agent,
            side_effect=RuntimeError("unexpected crash"),
        )

        request = LipSyncRequest(
            scene_id=7,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        resp = await agent.execute(request)

        # 通用异常也被 except (..., Exception) 捕获，触发降级
        assert resp.success is True
        assert resp.data["synced"] is False


class TestProgressCallback:
    async def test_progress_callback_invoked(self, agent, monkeypatch):
        """progress_callback 被透传并触发关键阶段。"""
        monkeypatch.setattr(settings, "lip_sync_enabled", True)
        _attach_mock_latentsync(agent)

        # 模拟 LatentSyncService.sync_lip 内部调用 progress_callback
        async def fake_sync_lip(*args, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                cb(5, "上传视频")
                cb(100, "完成")
            return {
                "video_url": "http://synced/out.mp4",
                "duration_seconds": 4.0,
                "task_id": "t-1",
            }

        agent._latentsync.sync_lip = AsyncMock(side_effect=fake_sync_lip)

        received: list[tuple[int, str]] = []

        def cb(p: int, m: str) -> None:
            received.append((p, m))

        request = LipSyncRequest(
            scene_id=8,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        await agent.execute(request, progress_callback=cb)

        # 至少触发了 5/100
        progresses = [p for p, _ in received]
        assert 5 in progresses
        assert 100 in progresses

    async def test_progress_callback_when_disabled(self, agent):
        """关闭时回调被触发一次（100, "唇形同步已关闭，跳过"）。"""
        received: list[tuple[int, str]] = []

        def cb(p: int, m: str) -> None:
            received.append((p, m))

        request = LipSyncRequest(
            scene_id=9,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
        )
        await agent.execute(request, progress_callback=cb)

        assert received == [(100, "唇形同步已关闭，跳过")]
