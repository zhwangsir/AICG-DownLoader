"""xDiT Service 单元测试。

使用 httpx.MockTransport 模拟 xDiT FastAPI 服务，覆盖：
- upload_image: 下载图片 + 上传到 xDiT
- submit_task: 提交生成任务
- poll_status: 轮询 pending → running → succeeded/failed/timeout
- get_result: 获取最终视频 URL
- generate_video: 端到端流程编排
"""

from __future__ import annotations

import httpx
import pytest

from app.services.xdit_service import XDiTService, XDiTServiceError


def _make_xdit(handler) -> XDiTService:
    """构造使用 MockTransport 的 XDiTService 实例，避免真实网络调用。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return XDiTService(http_client=client)


def _json_resp(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=json_data or {})


def _bytes_resp(content: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=content)


class TestUploadImage:
    async def test_success(self):
        """下载图片字节并上传到 xDiT，返回服务端文件名。"""
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "GET" and "source-image" in url:
                calls.append(("get", url))
                return _bytes_resp(b"fake-image-bytes")
            if request.method == "POST" and url.endswith("/v1/video/upload"):
                calls.append(("post", url))
                return _json_resp(200, {"filename": "scene_001.png"})
            return _json_resp(404, {"detail": "not found"})

        svc = _make_xdit(handler)
        filename = await svc.upload_image("http://mock/source-image.png")

        assert filename == "scene_001.png"
        assert ("get", "http://mock/source-image.png") in calls
        assert any("upload" in u for _, u in calls)

    async def test_missing_filename_defaults_to_input_png(self):
        """xDiT 返回未含 filename 时回退到 input.png。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"img")
            return _json_resp(200, {})

        svc = _make_xdit(handler)
        filename = await svc.upload_image("http://mock/img.png")
        assert filename == "input.png"

    async def test_http_error_raises(self):
        """图片下载失败时 raise_for_status 触发异常。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return _bytes_resp(b"", status_code=500)

        svc = _make_xdit(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.upload_image("http://mock/img.png")


class TestSubmitTask:
    async def test_success_returns_task_id(self):
        """提交任务成功，返回 task_id。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "task-abc"})

        svc = _make_xdit(handler)
        task_id = await svc.submit_task(
            image_filename="input.png",
            prompt="cinematic",
            negative_prompt="blurry",
            scene_id=42,
            duration_seconds=4,
        )

        assert task_id == "task-abc"
        # 验证请求体包含关键参数
        import json

        body = json.loads(captured["body"])
        assert body["image"] == "input.png"
        assert body["prompt"] == "cinematic"
        assert body["scene_id"] == 42
        assert body["model"] == "hunyuanvideo-i2v"

    async def test_missing_task_id_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {})

        svc = _make_xdit(handler)
        with pytest.raises(XDiTServiceError, match="未返回 task_id"):
            await svc.submit_task("img.png", "p")

    async def test_duration_override_changes_num_frames(self, monkeypatch):
        """duration_seconds 影响 num_frames 计算。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "t"})

        svc = _make_xdit(handler)
        # 3 秒视频：3*24+1=73，对齐到 4k+1 → 73
        await svc.submit_task("img.png", "p", duration_seconds=3)

        import json

        body = json.loads(captured["body"])
        assert body["num_frames"] == 73


class TestPollStatus:
    async def test_succeeded(self, monkeypatch):
        """轮询到 succeeded 后返回最终状态数据。"""
        states = iter([
            {"status": "pending", "progress": 0, "message": "queued"},
            {"status": "running", "progress": 50, "message": "denoising"},
            {"status": "succeeded", "progress": 100, "message": "done"},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        svc = _make_xdit(handler)
        # 加速测试：跳过 sleep
        import app.services.xdit_service as xdit_mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(xdit_mod.asyncio, "sleep", _no_sleep)

        progress_calls: list[tuple[int, str]] = []
        result = await svc.poll_status(
            "task-1",
            progress_callback=lambda p, m: progress_calls.append((p, m)),
        )
        assert result["status"] == "succeeded"
        # 进度回调去重：0 → 50 → 100
        assert [p for p, _ in progress_calls] == [0, 50, 100]

    async def test_failed_raises(self, monkeypatch):
        """轮询到 failed 抛 XDiTServiceError。"""
        states = iter([
            {"status": "running", "progress": 30, "message": "denoising"},
            {"status": "failed", "error": "OOM", "progress": 30, "message": ""},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        svc = _make_xdit(handler)
        import app.services.xdit_service as xdit_mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(xdit_mod.asyncio, "sleep", _no_sleep)

        with pytest.raises(XDiTServiceError, match="OOM"):
            await svc.poll_status("task-1")

    async def test_timeout_raises_timeout_error(self, monkeypatch):
        """超过 deadline 抛 TimeoutError。"""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _json_resp(200, {"status": "running", "progress": 50})

        svc = _make_xdit(handler)
        # 立即让 deadline 过期：timeout=0.001 + 不 sleep
        import app.services.xdit_service as xdit_mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(xdit_mod.asyncio, "sleep", _no_sleep)
        with pytest.raises(TimeoutError, match="超时"):
            await svc.poll_status("task-1", timeout=0.001)


class TestGetResult:
    async def test_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(
                200,
                {"video_url": "http://mock/out.mp4", "duration_seconds": 4},
            )

        svc = _make_xdit(handler)
        result = await svc.get_result("task-1")
        assert result["video_url"] == "http://mock/out.mp4"
        assert result["duration_seconds"] == 4
        assert result["task_id"] == "task-1"

    async def test_missing_video_url_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {})

        svc = _make_xdit(handler)
        with pytest.raises(XDiTServiceError, match="缺少 video_url"):
            await svc.get_result("task-1")


class TestGenerateVideoE2E:
    """端到端编排：upload → submit → poll → result。"""

    async def test_full_pipeline_success(self, monkeypatch):
        """完整流程成功，进度回调 0→5→15→25→100。"""
        # 状态机：upload → generate → status×N → result
        state = {"phase": "upload"}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "source-img" in url:
                return _bytes_resp(b"img-bytes")
            if method == "POST" and url.endswith("/v1/video/upload"):
                return _json_resp(200, {"filename": "in.png"})
            if method == "POST" and url.endswith("/v1/video/generate"):
                return _json_resp(200, {"task_id": "task-x"})
            if method == "GET" and "/v1/video/status/" in url:
                state["phase"] = state.get("phase", "upload")
                # 第一次 running，第二次 succeeded
                if state.get("count", 0) == 0:
                    state["count"] = 1
                    return _json_resp(
                        200, {"status": "running", "progress": 50, "message": "mid"}
                    )
                return _json_resp(
                    200, {"status": "succeeded", "progress": 100, "message": "done"}
                )
            if method == "GET" and "/v1/video/result/" in url:
                return _json_resp(
                    200,
                    {"video_url": "http://mock/r.mp4", "duration_seconds": 4},
                )
            return _json_resp(404, {"detail": "unknown"})

        svc = _make_xdit(handler)
        import app.services.xdit_service as xdit_mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(xdit_mod.asyncio, "sleep", _no_sleep)

        progresses: list[tuple[int, str]] = []
        result = await svc.generate_video(
            image_url="http://mock/source-img.png",
            prompt="cinematic",
            negative_prompt="blurry",
            scene_id=7,
            progress_callback=lambda p, m: progresses.append((p, m)),
        )

        assert result["video_url"] == "http://mock/r.mp4"
        assert result["task_id"] == "task-x"
        # 进度回调至少覆盖起点与终点
        assert progresses[0][0] == 5
        assert progresses[-1][0] == 100

    async def test_pipeline_failure_propagates(self, monkeypatch):
        """submit_task 失败时异常上抛（由上层 video_agent 决定回退策略）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method
            if method == "GET" and "source-img" in url:
                return _bytes_resp(b"img")
            if method == "POST" and url.endswith("/v1/video/upload"):
                return _json_resp(200, {"filename": "in.png"})
            if method == "POST" and url.endswith("/v1/video/generate"):
                return _json_resp(500, {"detail": "xDiT internal error"})
            return _json_resp(404, {})

        svc = _make_xdit(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.generate_video(
                image_url="http://mock/source-img.png",
                prompt="p",
                scene_id=1,
            )
