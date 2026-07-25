"""LTX-Video 分镜预览服务单元测试。

P4.3: 覆盖 LTXVideoService 客户端：
- submit_preview: 提交预览任务返回 task_id
- poll_status: pending → running → succeeded/failed/timeout
- get_result: 获取最终 video_url
- generate_preview: 端到端流程编排
- is_enabled: 受 settings.ltx_video_enabled 控制
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.services.ltx_video_service import LTXVideoService, LTXVideoServiceError


def _make_ltx(handler) -> LTXVideoService:
    """构造使用 MockTransport 的 LTXVideoService 实例。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return LTXVideoService(http_client=client)


# ============================================================================
# submit_preview
# ============================================================================


class TestSubmitPreview:
    async def test_success_returns_task_id(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert str(request.url).endswith("/v1/video/preview")
            captured["payload"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"task_id": "ltx-task-001"})

        svc = _make_ltx(handler)
        task_id = await svc.submit_preview(
            image_url="http://mock/img.png",
            prompt="camera pan",
            negative_prompt="blurry",
        )

        assert task_id == "ltx-task-001"
        assert captured["payload"]["image"] == "http://mock/img.png"
        assert captured["payload"]["prompt"] == "camera pan"
        assert captured["payload"]["negative_prompt"] == "blurry"

    async def test_missing_task_id_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        svc = _make_ltx(handler)
        with pytest.raises(LTXVideoServiceError, match="未返回 task_id"):
            await svc.submit_preview(image_url="http://mock/img.png")

    async def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        svc = _make_ltx(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.submit_preview(image_url="http://mock/img.png")


# ============================================================================
# poll_status
# ============================================================================


class TestPollStatus:
    async def test_succeeded(self):
        """pending → running → succeeded 轮询序列。"""
        states = [
            {"status": "pending", "progress": 0},
            {"status": "running", "progress": 50},
            {"status": "succeeded", "progress": 100},
        ]
        idx = {"i": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            data = states[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=data)

        svc = _make_ltx(handler)
        result = await svc.poll_status("task-1", timeout=5.0)

        assert result["status"] == "succeeded"
        assert idx["i"] == 3

    async def test_failed_raises(self):
        states = [
            {"status": "running", "progress": 30},
            {"status": "failed", "progress": 30, "error": "OOM"},
        ]
        idx = {"i": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            data = states[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=data)

        svc = _make_ltx(handler)
        with pytest.raises(LTXVideoServiceError, match="OOM"):
            await svc.poll_status("task-1", timeout=5.0)

    async def test_progress_callback_invoked(self):
        """progress_callback 在 progress 变化时被调用。"""
        states = [
            {"status": "pending", "progress": 0},
            {"status": "running", "progress": 50},
            {"status": "succeeded", "progress": 100},
        ]
        idx = {"i": 0}
        callbacks: list[tuple[int, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            data = states[idx["i"]]
            idx["i"] += 1
            return httpx.Response(200, json=data)

        def cb(progress: int, message: str):
            callbacks.append((progress, message))

        svc = _make_ltx(handler)
        await svc.poll_status("task-1", progress_callback=cb, timeout=5.0)

        assert (0, "") in callbacks
        assert (50, "") in callbacks
        assert (100, "") in callbacks


# ============================================================================
# get_result
# ============================================================================


class TestGetResult:
    async def test_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert str(request.url).endswith("/v1/video/result/task-001")
            return httpx.Response(200, json={
                "video_url": "http://mock/preview.mp4",
                "duration_seconds": 2.7,
            })

        svc = _make_ltx(handler)
        result = await svc.get_result("task-001")

        assert result["video_url"] == "http://mock/preview.mp4"
        assert result["duration_seconds"] == 2.7
        assert result["task_id"] == "task-001"

    async def test_missing_video_url_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"duration_seconds": 1.0})

        svc = _make_ltx(handler)
        with pytest.raises(LTXVideoServiceError, match="缺少 video_url"):
            await svc.get_result("task-1")


# ============================================================================
# generate_preview
# ============================================================================


class TestGeneratePreview:
    async def test_end_to_end(self):
        """提交 → 轮询 → 获取结果 端到端流程。"""
        call_log: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "POST" and url.endswith("/v1/video/preview"):
                call_log.append("submit")
                return httpx.Response(200, json={"task_id": "ep-001"})
            if "status" in url:
                call_log.append("poll")
                return httpx.Response(200, json={
                    "status": "succeeded", "progress": 100
                })
            if "result" in url:
                call_log.append("result")
                return httpx.Response(200, json={
                    "video_url": "http://mock/preview.mp4",
                    "duration_seconds": 2.7,
                })
            return httpx.Response(404)

        svc = _make_ltx(handler)
        result = await svc.generate_preview(
            image_url="http://mock/img.png",
            prompt="pan",
        )

        assert result["video_url"] == "http://mock/preview.mp4"
        assert "submit" in call_log
        assert "poll" in call_log
        assert "result" in call_log

    async def test_progress_callback_called(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "POST":
                return httpx.Response(200, json={"task_id": "t"})
            if "status" in url:
                return httpx.Response(200, json={
                    "status": "succeeded", "progress": 100
                })
            if "result" in url:
                return httpx.Response(200, json={
                    "video_url": "u", "duration_seconds": 1.0,
                })
            return httpx.Response(404)

        callbacks: list[tuple[int, str]] = []

        def cb(progress: int, message: str):
            callbacks.append((progress, message))

        svc = _make_ltx(handler)
        await svc.generate_preview(
            image_url="http://mock/img.png",
            progress_callback=cb,
        )

        # 应至少触发提交、推理、获取、完成四个阶段
        progresses = [p for p, _ in callbacks]
        assert 10 in progresses  # 提交
        assert 30 in progresses  # 推理
        assert 95 in progresses  # 获取
        assert 100 in progresses  # 完成


# ============================================================================
# is_enabled
# ============================================================================


class TestIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "ltx_video_enabled", False)
        svc = LTXVideoService()
        assert svc.is_enabled() is False

    def test_enabled_when_flag_true(self, monkeypatch):
        monkeypatch.setattr(settings, "ltx_video_enabled", True)
        svc = LTXVideoService()
        assert svc.is_enabled() is True
