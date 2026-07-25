"""LatentSync 唇形同步服务单元测试 — P4.4。

使用 httpx.MockTransport 模拟 LatentSync FastAPI wrapper，覆盖：
- upload_media: 下载视频/音频/参考图 + 上传到服务端
- submit_task: 提交唇形同步任务（含参考图注入）
- poll_status: 轮询 pending → running → succeeded/failed/timeout
- get_result: 获取最终 video_url
- sync_lip: 端到端流程编排 + 进度回调四阶段
"""

from __future__ import annotations

import httpx
import pytest

from app.services.latentsync_service import (
    LatentSyncService,
    LatentSyncServiceError,
)


# ============================================================================
# 工具函数
# ============================================================================


def _make_latentsync(handler) -> LatentSyncService:
    """构造使用 MockTransport 的 LatentSyncService，避免真实网络调用。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return LatentSyncService(http_client=client)


def _json_resp(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=json_data or {})


def _bytes_resp(content: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=content)


# ============================================================================
# upload_media
# ============================================================================


class TestUploadMedia:
    async def test_video_upload_returns_filename(self):
        """下载视频字节并上传，返回服务端文件名。"""
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "GET" and "source-video" in url:
                calls.append(("get", url))
                return _bytes_resp(b"fake-video-bytes")
            if request.method == "POST" and url.endswith("/v1/video/upload"):
                calls.append(("post", url))
                return _json_resp(200, {"filename": "scene_001.mp4"})
            return _json_resp(404, {"detail": "not found"})

        svc = _make_latentsync(handler)
        filename = await svc.upload_media(
            "http://mock/source-video.mp4", media_type="video"
        )

        assert filename == "scene_001.mp4"
        assert ("get", "http://mock/source-video.mp4") in calls
        assert any("upload" in u for _, u in calls)

    async def test_audio_media_type_uses_mp3_ext(self):
        """media_type='audio' → 文件部分 content-type 为 audio/mpeg。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"fake-audio")
            # multipart 体内嵌的子 part 头部含 'Content-Type: audio/mpeg'
            captured["body"] = request.content.decode("latin-1", errors="ignore")
            return _json_resp(200, {"filename": "audio.mp3"})

        svc = _make_latentsync(handler)
        filename = await svc.upload_media(
            "http://mock/audio.mp3", media_type="audio"
        )

        assert filename == "audio.mp3"
        # multipart body 中嵌入文件 part 的 Content-Type: audio/mpeg
        assert "audio/mpeg" in captured["body"]
        assert "input.mp3" in captured["body"]

    async def test_reference_media_type_uses_png_ext(self):
        """media_type='reference' → 文件部分 content-type 为 image/png。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"fake-png")
            captured["body"] = request.content.decode("latin-1", errors="ignore")
            return _json_resp(200, {"filename": "ref.png"})

        svc = _make_latentsync(handler)
        filename = await svc.upload_media(
            "http://mock/ref.png", media_type="reference"
        )

        assert filename == "ref.png"
        assert "image/png" in captured["body"]
        assert "input.png" in captured["body"]

    async def test_missing_filename_defaults_to_input_ext(self):
        """LatentSync 未返回 filename 时回退到 input.<ext>。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"v")
            return _json_resp(200, {})

        svc = _make_latentsync(handler)
        filename = await svc.upload_media(
            "http://mock/v.mp4", media_type="video"
        )
        assert filename == "input.mp4"

    async def test_download_http_error_raises(self):
        """源媒体下载失败 → raise_for_status 触发 HTTPStatusError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return _bytes_resp(b"", status_code=500)

        svc = _make_latentsync(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.upload_media("http://mock/v.mp4")

    async def test_upload_http_error_raises(self):
        """上传请求返回 500 → 抛出 HTTPStatusError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"v")
            return _json_resp(500, {"detail": "server error"})

        svc = _make_latentsync(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.upload_media("http://mock/v.mp4")


# ============================================================================
# submit_task
# ============================================================================


class TestSubmitTask:
    async def test_success_returns_task_id(self):
        """提交任务成功，返回 task_id。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "lipsync-task-001"})

        svc = _make_latentsync(handler)
        task_id = await svc.submit_task(
            video_filename="scene_001.mp4",
            audio_filename="audio_001.mp3",
            scene_id=7,
        )

        assert task_id == "lipsync-task-001"
        import json

        body = json.loads(captured["body"])
        assert body["video"] == "scene_001.mp4"
        assert body["audio"] == "audio_001.mp3"
        assert body["scene_id"] == 7
        assert "reference_image" not in body

    async def test_reference_image_injected_when_provided(self):
        """传入 reference_image_filename 时，payload 包含 reference_image 字段。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "t"})

        svc = _make_latentsync(handler)
        await svc.submit_task(
            video_filename="v.mp4",
            audio_filename="a.mp3",
            scene_id=1,
            reference_image_filename="ref.png",
        )

        import json

        body = json.loads(captured["body"])
        assert body["reference_image"] == "ref.png"

    async def test_missing_task_id_raises(self):
        """响应未含 task_id → LatentSyncServiceError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {})

        svc = _make_latentsync(handler)
        with pytest.raises(LatentSyncServiceError, match="未返回 task_id"):
            await svc.submit_task("v.mp4", "a.mp3")

    async def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(500)

        svc = _make_latentsync(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.submit_task("v.mp4", "a.mp3")


# ============================================================================
# poll_status
# ============================================================================


class TestPollStatus:
    async def test_succeeded(self, monkeypatch):
        """轮询到 succeeded 后返回最终状态数据。"""
        states = iter([
            {"status": "pending", "progress": 0, "message": "queued"},
            {"status": "running", "progress": 50, "message": "syncing"},
            {"status": "succeeded", "progress": 100, "message": "done"},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        # 加速：跳过 sleep
        import app.services.latentsync_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_latentsync(handler)
        data = await svc.poll_status("task-1")

        assert data["status"] == "succeeded"
        assert data["progress"] == 100

    async def test_failed_raises(self, monkeypatch):
        """轮询到 failed → LatentSyncServiceError。"""
        states = iter([
            {"status": "running", "progress": 30, "message": "working"},
            {
                "status": "failed",
                "progress": 30,
                "message": "OOM",
                "error": "GPU OOM",
            },
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        import app.services.latentsync_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_latentsync(handler)
        with pytest.raises(LatentSyncServiceError, match="失败"):
            await svc.poll_status("task-2")

    async def test_timeout_raises(self, monkeypatch):
        """轮询超过 deadline → TimeoutError。"""
        # 始终返回 running，永远不完成
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"status": "running", "progress": 50})

        import app.services.latentsync_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
        # 设置极短超时，立即触发
        monkeypatch.setattr(mod.settings, "latentsync_timeout", 0.01)

        svc = _make_latentsync(handler)
        with pytest.raises(TimeoutError, match="超时"):
            await svc.poll_status("task-3")

    async def test_progress_callback_invoked_on_change(self, monkeypatch):
        """进度变化时回调被调用，且去重（同 progress 不重复回调）。"""
        states = iter([
            {"status": "running", "progress": 10, "message": "step1"},
            {"status": "running", "progress": 10, "message": "still"},  # 同进度，不回调
            {"status": "running", "progress": 50, "message": "step2"},
            {"status": "succeeded", "progress": 100, "message": "done"},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        import app.services.latentsync_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        received: list[tuple[int, str]] = []

        def cb(p: int, m: str) -> None:
            received.append((p, m))

        svc = _make_latentsync(handler)
        await svc.poll_status("task-4", progress_callback=cb)

        # 去重后应有 3 次（10/50/100）
        progresses = [p for p, _ in received]
        assert progresses == [10, 50, 100]


# ============================================================================
# get_result
# ============================================================================


class TestGetResult:
    async def test_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url).endswith("/v1/lipsync/result/task-9")
            return _json_resp(200, {
                "video_url": "http://latentsync/out.mp4",
                "duration_seconds": 5.0,
            })

        svc = _make_latentsync(handler)
        result = await svc.get_result("task-9")

        assert result["video_url"] == "http://latentsync/out.mp4"
        assert result["duration_seconds"] == 5.0
        assert result["task_id"] == "task-9"

    async def test_missing_video_url_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"duration_seconds": 3.0})

        svc = _make_latentsync(handler)
        with pytest.raises(LatentSyncServiceError, match="缺少 video_url"):
            await svc.get_result("task-x")


# ============================================================================
# sync_lip 端到端编排
# ============================================================================


class TestSyncLip:
    async def test_end_to_end_success(self, monkeypatch):
        """端到端：上传视频+音频 → 提交任务 → 轮询 → 获取结果。"""
        # 状态机：upload(2 次) + submit(1 次) + status(running, succeeded) + result
        upload_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                # 下载视频/音频字节
                return _bytes_resp(b"media-bytes")

            if method == "POST" and url.endswith("/v1/video/upload"):
                upload_count["n"] += 1
                return _json_resp(200, {"filename": f"file_{upload_count['n']}.bin"})

            if method == "POST" and url.endswith("/v1/lipsync/submit"):
                return _json_resp(200, {"task_id": "sync-task-1"})

            if method == "GET" and "/v1/lipsync/status/" in url:
                return _json_resp(200, {"status": "succeeded", "progress": 100})

            if method == "GET" and "/v1/lipsync/result/" in url:
                return _json_resp(200, {
                    "video_url": "http://synced/out.mp4",
                    "duration_seconds": 4.5,
                })

            return _json_resp(404)

        import app.services.latentsync_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_latentsync(handler)
        result = await svc.sync_lip(
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
            scene_id=11,
        )

        assert result["video_url"] == "http://synced/out.mp4"
        assert result["task_id"] == "sync-task-1"
        assert upload_count["n"] == 2  # 视频和音频各上传一次

    async def test_progress_callback_four_stages(self, monkeypatch):
        """sync_lip 触发进度回调覆盖关键阶段（5/15/30/100 等）。"""
        # 状态机：单次 succeeded + 一次轮询
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                return _bytes_resp(b"b")
            if method == "POST" and url.endswith("/v1/video/upload"):
                return _json_resp(200, {"filename": "f.bin"})
            if method == "POST" and url.endswith("/v1/lipsync/submit"):
                return _json_resp(200, {"task_id": "t"})
            if method == "GET" and "/status/" in url:
                return _json_resp(200, {"status": "succeeded", "progress": 100})
            if method == "GET" and "/result/" in url:
                return _json_resp(200, {"video_url": "http://x/o.mp4"})
            return _json_resp(404)

        import app.services.latentsync_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        received: list[int] = []

        def cb(p: int, _msg: str) -> None:
            received.append(p)

        svc = _make_latentsync(handler)
        await svc.sync_lip(
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
            progress_callback=cb,
        )

        # 至少触发了关键阶段（5/15/30/95/100），含 100
        assert 100 in received
        assert received[0] == 5  # 第一阶段

    async def test_with_reference_image(self, monkeypatch):
        """传入 reference_image_url 时上传 3 个媒体文件（视频+音频+参考图）。"""
        upload_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                return _bytes_resp(b"b")
            if method == "POST" and url.endswith("/v1/video/upload"):
                upload_count["n"] += 1
                return _json_resp(200, {"filename": f"f{upload_count['n']}.bin"})
            if method == "POST" and url.endswith("/v1/lipsync/submit"):
                return _json_resp(200, {"task_id": "t"})
            if method == "GET" and "/status/" in url:
                return _json_resp(200, {"status": "succeeded", "progress": 100})
            if method == "GET" and "/result/" in url:
                return _json_resp(200, {"video_url": "http://x/o.mp4"})
            return _json_resp(404)

        import app.services.latentsync_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_latentsync(handler)
        await svc.sync_lip(
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
            reference_image_url="http://mock/r.png",
        )

        # 视频 + 音频 + 参考图 = 3 次上传
        assert upload_count["n"] == 3

    async def test_upload_failure_propagates(self):
        """视频上传失败 → 异常向上传播（sync_lip 不吞错误，由 Agent 降级）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            # 下载阶段 500
            return _bytes_resp(b"", status_code=500)

        svc = _make_latentsync(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.sync_lip(
                video_url="http://mock/v.mp4",
                audio_url="http://mock/a.mp3",
            )
