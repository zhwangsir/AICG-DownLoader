"""xDiT Service 单元测试。

使用 httpx.MockTransport 模拟 xDiT FastAPI 服务（OpenAI 风格契约），覆盖：
- upload_image: 下载图片 + POST /v1/upload，返回 image_url
- submit_task: POST /v1/videos/generations?async_mode=true，返回 task_id
- poll_status: GET /v1/tasks/{id} 轮询 pending → running → succeeded/failed/timeout
- get_result: 从 /v1/tasks/{id} 的 result 字段提取 video_url
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
        """下载图片字节并上传到 xDiT，返回服务端 image_url。"""
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "GET" and "source-image" in url:
                calls.append(("get", url))
                return _bytes_resp(b"fake-image-bytes")
            if request.method == "POST" and url.endswith("/v1/upload"):
                calls.append(("post", url))
                return _json_resp(200, {
                    "image_url": "http://xdit/files/upload/abc.png",
                    "filename": "abc.png",
                    "size": 16,
                })
            return _json_resp(404, {"detail": "not found"})

        svc = _make_xdit(handler)
        image_url = await svc.upload_image("http://mock/source-image.png")

        assert image_url == "http://xdit/files/upload/abc.png"
        assert ("get", "http://mock/source-image.png") in calls
        assert any("upload" in u for _, u in calls)

    async def test_missing_image_url_raises(self):
        """xDiT 返回未含 image_url 时抛 XDiTServiceError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"img")
            return _json_resp(200, {})

        svc = _make_xdit(handler)
        with pytest.raises(XDiTServiceError, match="未返回 image_url"):
            await svc.upload_image("http://mock/img.png")

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
            captured["query"] = str(request.url.params)
            return _json_resp(202, {"task_id": "task-abc", "status": "pending"})

        svc = _make_xdit(handler)
        task_id = await svc.submit_task(
            image_url="http://xdit/files/upload/abc.png",
            prompt="cinematic",
            negative_prompt="blurry",
            scene_id=42,
            duration_seconds=4,
        )

        assert task_id == "task-abc"
        assert "async_mode=true" in captured["query"]
        # 验证请求体包含关键参数（远程契约字段）
        import json

        body = json.loads(captured["body"])
        assert body["image_url"] == "http://xdit/files/upload/abc.png"
        assert body["prompt"] == "cinematic"
        assert body["model"] == "hunyuanvideo-i2v"
        assert body["size"] == "720p"
        # scene_id / negative_prompt 不发送到远程
        assert "scene_id" not in body
        assert "negative_prompt" not in body

    async def test_missing_task_id_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(202, {})

        svc = _make_xdit(handler)
        with pytest.raises(XDiTServiceError, match="未返回 task_id"):
            await svc.submit_task("http://xdit/img.png", "p")

    async def test_duration_override_changes_num_frames(self):
        """duration_seconds 影响 num_frames 计算。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(202, {"task_id": "t", "status": "pending"})

        svc = _make_xdit(handler)
        # 3 秒视频：3*24+1=73，对齐到 4k+1 → 73
        await svc.submit_task("http://xdit/img.png", "p", duration_seconds=3)

        import json

        body = json.loads(captured["body"])
        assert body["num_frames"] == 73


class TestPollStatus:
    async def test_succeeded(self, monkeypatch):
        """轮询到 succeeded 后返回最终任务数据（含 result）。"""
        states = iter([
            {"task_id": "task-1", "status": "pending"},
            {"task_id": "task-1", "status": "running"},
            {
                "task_id": "task-1",
                "status": "succeeded",
                "result": {"url": "http://mock/out.mp4", "num_frames": 97},
            },
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
        assert result["result"]["url"] == "http://mock/out.mp4"
        # 无 started_at 时进度恒为 25，去重后只回调一次
        assert [p for p, _ in progress_calls] == [25]

    async def test_running_estimates_progress_from_started_at(self, monkeypatch):
        """running 状态按 started_at 已用时长估算进度（25 → 90 递增）。"""
        import time as time_mod

        states = iter([
            {"task_id": "t", "status": "running", "started_at": time_mod.time() - 60},
            {"task_id": "t", "status": "running", "started_at": time_mod.time() - 180},
            {"task_id": "t", "status": "succeeded", "result": {"url": "u"}},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        svc = _make_xdit(handler)
        import app.services.xdit_service as xdit_mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(xdit_mod.asyncio, "sleep", _no_sleep)

        progresses: list[int] = []
        await svc.poll_status("t", progress_callback=lambda p, m: progresses.append(p))
        # 60s/240s → 25+16=41；180s/240s → 25+48=73；递增且不超过 90
        assert progresses == sorted(progresses)
        assert progresses[0] > 25
        assert progresses[-1] <= 90

    async def test_failed_raises(self, monkeypatch):
        """轮询到 failed 抛 XDiTServiceError，消息含远程 error。"""
        states = iter([
            {"task_id": "task-1", "status": "running"},
            {"task_id": "task-1", "status": "failed", "error": "OOM"},
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

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"task_id": "task-1", "status": "running"})

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
        """succeeded 任务从 result 字段提取 video_url，时长由帧数推算。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {
                "task_id": "task-1",
                "status": "succeeded",
                "result": {
                    "url": "http://mock/out.mp4",
                    "elapsed": 123.4,
                    "num_frames": 97,
                    "size": "1280x720",
                    "seed": 42,
                },
            })

        svc = _make_xdit(handler)
        result = await svc.get_result("task-1")
        assert result["video_url"] == "http://mock/out.mp4"
        # 97 帧 / 24fps ≈ 4s
        assert result["duration_seconds"] == 4
        assert result["task_id"] == "task-1"

    async def test_missing_video_url_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"task_id": "task-1", "status": "succeeded"})

        svc = _make_xdit(handler)
        with pytest.raises(XDiTServiceError, match="缺少 video_url"):
            await svc.get_result("task-1")


class TestGenerateVideoE2E:
    """端到端编排：upload → generations → tasks 轮询 → result。"""

    async def test_full_pipeline_success(self, monkeypatch):
        """完整流程成功，进度回调 5 → 15 → 25 → 95 → 100。"""
        state = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "source-img" in url:
                return _bytes_resp(b"img-bytes")
            if method == "POST" and url.endswith("/v1/upload"):
                return _json_resp(200, {
                    "image_url": "http://xdit/files/upload/in.png",
                    "filename": "in.png",
                })
            if method == "POST" and "/v1/videos/generations" in url:
                return _json_resp(202, {"task_id": "task-x", "status": "pending"})
            if method == "GET" and "/v1/tasks/" in url:
                state["count"] += 1
                if state["count"] == 1:
                    return _json_resp(200, {"task_id": "task-x", "status": "running"})
                return _json_resp(200, {
                    "task_id": "task-x",
                    "status": "succeeded",
                    "result": {"url": "http://mock/r.mp4", "num_frames": 97},
                })
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
            if method == "POST" and url.endswith("/v1/upload"):
                return _json_resp(200, {"image_url": "http://xdit/in.png"})
            if method == "POST" and "/v1/videos/generations" in url:
                return _json_resp(500, {"detail": "xDiT internal error"})
            return _json_resp(404, {})

        svc = _make_xdit(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.generate_video(
                image_url="http://mock/source-img.png",
                prompt="p",
                scene_id=1,
            )
