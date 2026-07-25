"""后处理服务单元测试 — P4.4 RealBasicVSR / RIFE / ProPainter / DeepFilterNet3。

使用 httpx.MockTransport 模拟 ComfyUI 包装的后处理服务（端口 8290）和
Mac 集群 DeepFilterNet3 服务（端口 8301），覆盖：
- PostprocessService:
  - upload_video: 下载视频 + 上传，返回服务端文件名
  - submit_super_resolution / submit_frame_interpolation / submit_inpainting
  - poll_status: succeeded / failed / timeout
  - get_result: 缺 video_url 抛错
  - run_super_resolution / run_frame_interpolation / run_inpainting 端到端
- DeepFilterNetService:
  - denoise: 直接字节返回 / b64_json 包装 / 扩展名推断 / 空数据 / HTTP 错误
"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.services.postprocess_service import (
    DeepFilterNetService,
    PostprocessService,
    PostprocessServiceError,
)


# ============================================================================
# 工具函数
# ============================================================================


def _make_postprocess(handler) -> PostprocessService:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return PostprocessService(http_client=client)


def _make_deepfilternet(handler) -> DeepFilterNetService:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return DeepFilterNetService(http_client=client)


def _json_resp(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=json_data or {})


def _bytes_resp(
    content: bytes, status_code: int = 200, headers: dict | None = None
) -> httpx.Response:
    return httpx.Response(status_code, content=content, headers=headers or {})


# ============================================================================
# PostprocessService.upload_video
# ============================================================================


class TestUploadVideo:
    async def test_success_returns_filename(self):
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "GET" and "source-video" in url:
                calls.append(("get", url))
                return _bytes_resp(b"fake-video-bytes")
            if request.method == "POST" and url.endswith("/v1/video/upload"):
                calls.append(("post", url))
                return _json_resp(200, {"filename": "scene_001.mp4"})
            return _json_resp(404)

        svc = _make_postprocess(handler)
        filename = await svc.upload_video("http://mock/source-video.mp4")

        assert filename == "scene_001.mp4"
        assert ("get", "http://mock/source-video.mp4") in calls

    async def test_missing_filename_defaults_to_input_mp4(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"v")
            return _json_resp(200, {})

        svc = _make_postprocess(handler)
        filename = await svc.upload_video("http://mock/v.mp4")
        assert filename == "input.mp4"

    async def test_download_failure_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _bytes_resp(b"", status_code=500)

        svc = _make_postprocess(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.upload_video("http://mock/v.mp4")


# ============================================================================
# PostprocessService.submit_*
# ============================================================================


class TestSubmitSuperResolution:
    async def test_success_returns_task_id(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            captured["url"] = str(request.url)
            return _json_resp(200, {"task_id": "sr-task-1"})

        svc = _make_postprocess(handler)
        task_id = await svc.submit_super_resolution("v.mp4", scene_id=5)

        assert task_id == "sr-task-1"
        assert captured["url"].endswith("/v1/postprocess/super_res")
        import json

        body = json.loads(captured["body"])
        assert body["video"] == "v.mp4"
        assert body["scene_id"] == 5

    async def test_scale_override(self):
        """scale 参数覆盖 settings.realbasicvsr_scale。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "t"})

        svc = _make_postprocess(handler)
        await svc.submit_super_resolution("v.mp4", scale=2)
        import json

        body = json.loads(captured["body"])
        assert body["scale"] == 2

    async def test_missing_task_id_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {})

        svc = _make_postprocess(handler)
        with pytest.raises(PostprocessServiceError, match="未返回 task_id"):
            await svc.submit_super_resolution("v.mp4")


class TestSubmitFrameInterpolation:
    async def test_success_returns_task_id(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            captured["url"] = str(request.url)
            return _json_resp(200, {"task_id": "rife-task-1"})

        svc = _make_postprocess(handler)
        task_id = await svc.submit_frame_interpolation("v.mp4", scene_id=3)

        assert task_id == "rife-task-1"
        assert captured["url"].endswith("/v1/postprocess/interp")
        import json

        body = json.loads(captured["body"])
        assert body["video"] == "v.mp4"
        assert body["target_fps"] == 60  # settings.rife_target_fps

    async def test_target_fps_override(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "t"})

        svc = _make_postprocess(handler)
        await svc.submit_frame_interpolation("v.mp4", target_fps=30)
        import json

        body = json.loads(captured["body"])
        assert body["target_fps"] == 30


class TestSubmitInpainting:
    async def test_success_without_mask(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "pp-task-1"})

        svc = _make_postprocess(handler)
        task_id = await svc.submit_inpainting("v.mp4", scene_id=9)

        assert task_id == "pp-task-1"
        import json

        body = json.loads(captured["body"])
        assert "mask" not in body
        assert body["video"] == "v.mp4"

    async def test_mask_url_injected(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read().decode()
            return _json_resp(200, {"task_id": "t"})

        svc = _make_postprocess(handler)
        await svc.submit_inpainting("v.mp4", mask_url="http://mock/m.png")
        import json

        body = json.loads(captured["body"])
        assert body["mask"] == "http://mock/m.png"


# ============================================================================
# PostprocessService.poll_status
# ============================================================================


class TestPollStatus:
    async def test_succeeded(self, monkeypatch):
        states = iter([
            {"status": "pending", "progress": 0, "message": "queued"},
            {"status": "running", "progress": 50, "message": "processing"},
            {"status": "succeeded", "progress": 100, "message": "done"},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        import app.services.postprocess_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_postprocess(handler)
        data = await svc.poll_status("sr-1", step_name="RealBasicVSR")

        assert data["status"] == "succeeded"
        assert data["progress"] == 100

    async def test_failed_raises(self, monkeypatch):
        states = iter([
            {"status": "running", "progress": 30, "message": "w"},
            {
                "status": "failed",
                "progress": 30,
                "message": "OOM",
                "error": "GPU OOM",
            },
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        import app.services.postprocess_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_postprocess(handler)
        with pytest.raises(PostprocessServiceError, match="失败"):
            await svc.poll_status("pp-1", step_name="ProPainter")

    async def test_timeout_raises(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"status": "running", "progress": 50})

        import app.services.postprocess_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_postprocess(handler)
        # 使用极短 timeout 立即触发
        with pytest.raises(TimeoutError, match="超时"):
            await svc.poll_status("t-1", timeout=0.01)

    async def test_progress_callback_message_prefixed_with_step(self, monkeypatch):
        """进度回调的 message 前缀包含 step_name。"""
        states = iter([
            {"status": "running", "progress": 10, "message": "step1"},
            {"status": "succeeded", "progress": 100, "message": "done"},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, next(states))

        import app.services.postprocess_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        received: list[str] = []

        def cb(_p: int, msg: str) -> None:
            received.append(msg)

        svc = _make_postprocess(handler)
        await svc.poll_status(
            "t-2", step_name="RIFE", progress_callback=cb
        )

        # 消息都应包含 "RIFE"
        assert all("RIFE" in m for m in received)


# ============================================================================
# PostprocessService.get_result
# ============================================================================


class TestGetResult:
    async def test_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url).endswith("/v1/postprocess/result/r-1")
            return _json_resp(200, {
                "video_url": "http://pp/out.mp4",
                "duration_seconds": 6.0,
            })

        svc = _make_postprocess(handler)
        result = await svc.get_result("r-1")

        assert result["video_url"] == "http://pp/out.mp4"
        assert result["task_id"] == "r-1"

    async def test_missing_video_url_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"duration": 1.0})

        svc = _make_postprocess(handler)
        with pytest.raises(PostprocessServiceError, match="缺少 video_url"):
            await svc.get_result("r-x")


# ============================================================================
# PostprocessService.run_*（端到端编排）
# ============================================================================


class TestRunSuperResolution:
    async def test_end_to_end(self, monkeypatch):
        """端到端：upload → submit → poll → result，返回新 video_url。"""
        upload_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                return _bytes_resp(b"video-bytes")
            if method == "POST" and url.endswith("/v1/video/upload"):
                upload_count["n"] += 1
                return _json_resp(200, {"filename": "v.mp4"})
            if method == "POST" and url.endswith("/v1/postprocess/super_res"):
                return _json_resp(200, {"task_id": "sr-1"})
            if method == "GET" and "/v1/postprocess/status/" in url:
                return _json_resp(200, {"status": "succeeded", "progress": 100})
            if method == "GET" and "/v1/postprocess/result/" in url:
                return _json_resp(200, {
                    "video_url": "http://4k/out.mp4",
                    "duration_seconds": 5.0,
                })
            return _json_resp(404)

        import app.services.postprocess_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_postprocess(handler)
        url = await svc.run_super_resolution(
            "http://mock/v.mp4", scene_id=1
        )

        assert url == "http://4k/out.mp4"
        assert upload_count["n"] == 1


class TestRunFrameInterpolation:
    async def test_end_to_end(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                return _bytes_resp(b"v")
            if method == "POST" and url.endswith("/v1/video/upload"):
                return _json_resp(200, {"filename": "v.mp4"})
            if method == "POST" and url.endswith("/v1/postprocess/interp"):
                return _json_resp(200, {"task_id": "rife-1"})
            if method == "GET" and "/status/" in url:
                return _json_resp(200, {"status": "succeeded", "progress": 100})
            if method == "GET" and "/result/" in url:
                return _json_resp(200, {"video_url": "http://60fps/out.mp4"})
            return _json_resp(404)

        import app.services.postprocess_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_postprocess(handler)
        url = await svc.run_frame_interpolation(
            "http://mock/v.mp4", scene_id=2
        )

        assert url == "http://60fps/out.mp4"


class TestRunInpainting:
    async def test_end_to_end(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                return _bytes_resp(b"v")
            if method == "POST" and url.endswith("/v1/video/upload"):
                return _json_resp(200, {"filename": "v.mp4"})
            if method == "POST" and url.endswith("/v1/postprocess/inpaint"):
                return _json_resp(200, {"task_id": "pp-1"})
            if method == "GET" and "/status/" in url:
                return _json_resp(200, {"status": "succeeded", "progress": 100})
            if method == "GET" and "/result/" in url:
                return _json_resp(200, {"video_url": "http://clean/out.mp4"})
            return _json_resp(404)

        import app.services.postprocess_service as mod

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

        svc = _make_postprocess(handler)
        url = await svc.run_inpainting("http://mock/v.mp4", scene_id=3)

        assert url == "http://clean/out.mp4"


# ============================================================================
# DeepFilterNetService.denoise
# ============================================================================


class TestDeepFilterNetDenoise:
    async def test_direct_bytes_response(self):
        """DeepFilterNet3 直接返回音频字节（无 JSON 包装）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                return _bytes_resp(b"raw-audio-bytes")
            if method == "POST" and url.endswith("/v1/audio/denoise"):
                # 直接返回音频字节
                return _bytes_resp(
                    b"denoised-audio",
                    headers={"content-type": "audio/mpeg"},
                )
            return _json_resp(404)

        svc = _make_deepfilternet(handler)
        result = await svc.denoise("http://mock/a.mp3")

        assert result == b"denoised-audio"

    async def test_b64_json_response(self):
        """响应为 JSON + audio_b64 字段 → 解码后返回字节。"""
        encoded = base64.b64encode(b"denoised-via-b64").decode("ascii")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            method = request.method

            if method == "GET" and "mock" in url:
                return _bytes_resp(b"raw")
            if method == "POST" and url.endswith("/v1/audio/denoise"):
                return _json_resp(
                    200,
                    {"audio_b64": encoded, "format": "mp3"},
                )
            return _json_resp(404)

        svc = _make_deepfilternet(handler)
        result = await svc.denoise("http://mock/a.mp3")

        assert result == b"denoised-via-b64"

    async def test_b64_missing_raises(self):
        """JSON 响应但缺 audio_b64 → PostprocessServiceError。"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"raw")
            return _json_resp(200, {"format": "mp3"})

        svc = _make_deepfilternet(handler)
        with pytest.raises(PostprocessServiceError, match="缺少 audio_b64"):
            await svc.denoise("http://mock/a.mp3")

    async def test_empty_bytes_response_raises(self):
        """返回空字节 → PostprocessServiceError。"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"raw")
            return _bytes_resp(
                b"",
                headers={"content-type": "audio/mpeg"},
            )

        svc = _make_deepfilternet(handler)
        with pytest.raises(PostprocessServiceError, match="空音频数据"):
            await svc.denoise("http://mock/a.mp3")

    async def test_wav_extension_inferred(self):
        """URL 以 .wav 结尾 → 文件部分 content-type 为 audio/wav。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"w")
            captured["body"] = request.content.decode("latin-1", errors="ignore")
            return _bytes_resp(
                b"denoised",
                headers={"content-type": "audio/wav"},
            )

        svc = _make_deepfilternet(handler)
        await svc.denoise("http://mock/a.wav")

        assert "audio/wav" in captured["body"]
        assert "input.wav" in captured["body"]

    async def test_m4a_extension_inferred(self):
        """URL 以 .m4a 结尾 → 文件部分 content-type 为 audio/mp4。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"w")
            captured["body"] = request.content.decode("latin-1", errors="ignore")
            return _bytes_resp(
                b"denoised",
                headers={"content-type": "audio/mp4"},
            )

        svc = _make_deepfilternet(handler)
        await svc.denoise("http://mock/a.m4a")

        assert "audio/mp4" in captured["body"]
        assert "input.m4a" in captured["body"]

    async def test_download_failure_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _bytes_resp(b"", status_code=500)

        svc = _make_deepfilternet(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.denoise("http://mock/a.mp3")

    async def test_denoise_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"raw")
            return _json_resp(500)

        svc = _make_deepfilternet(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.denoise("http://mock/a.mp3")
