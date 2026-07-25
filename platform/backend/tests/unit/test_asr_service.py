"""ASR Service 单元测试。

使用 httpx.MockTransport 模拟 FireRedASR 服务，覆盖：
- transcribe: 上传音频字节并返回 segments
- transcribe_url: 从 URL 下载并转写（端到端）
- 错误处理：HTTP 错误、字段缺失
"""

from __future__ import annotations

import httpx
import pytest

from app.services.asr_service import ASRService, ASRServiceError


def _make_asr(handler) -> ASRService:
    """构造使用 MockTransport 的 ASRService 实例。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return ASRService(http_client=client)


def _json_resp(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=json_data or {})


def _bytes_resp(content: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=content)


class TestTranscribe:
    async def test_success(self):
        """上传音频字节并返回带时间戳的 segments。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["content_type"] = request.headers.get("content-type", "")
            return _json_resp(
                200,
                {
                    "text": "你好 世界",
                    "segments": [
                        {"start": 0.0, "end": 1.0, "text": "你好"},
                        {"start": 1.0, "end": 2.0, "text": "世界"},
                    ],
                    "language": "zh",
                    "duration": 2.0,
                },
            )

        svc = _make_asr(handler)
        result = await svc.transcribe(b"fake-audio", "test.mp3", "zh")

        assert result["text"] == "你好 世界"
        assert len(result["segments"]) == 2
        assert result["segments"][0]["start"] == 0.0
        assert result["language"] == "zh"
        assert captured["method"] == "POST"
        assert "/asr/transcribe" in captured["url"]
        # 验证 multipart/form-data
        assert "multipart/form-data" in captured["content_type"]

    async def test_missing_segments_raises(self):
        """返回缺少 segments 字段时抛 ASRServiceError。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"text": "仅文本"})

        svc = _make_asr(handler)
        with pytest.raises(ASRServiceError, match="缺少 segments"):
            await svc.transcribe(b"audio")

    async def test_invalid_segments_format_raises(self):
        """segments 不是列表时抛 ASRServiceError。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(200, {"segments": "not a list"})

        svc = _make_asr(handler)
        with pytest.raises(ASRServiceError, match="segments 格式错误"):
            await svc.transcribe(b"audio")

    async def test_http_error_raises(self):
        """HTTP 500 错误时抛 HTTPStatusError。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _json_resp(500, {"detail": "ASR service down"})

        svc = _make_asr(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.transcribe(b"audio")


class TestTranscribeUrl:
    async def test_e2e_success(self):
        """从 URL 下载音频并转写（端到端）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "GET" and "audio.mp3" in url:
                return _bytes_resp(b"fake-audio-bytes")
            if request.method == "POST" and "/asr/transcribe" in url:
                return _json_resp(
                    200,
                    {
                        "text": "测试字幕",
                        "segments": [{"start": 0.0, "end": 1.0, "text": "测试字幕"}],
                        "language": "zh",
                        "duration": 1.0,
                    },
                )
            return _json_resp(404, {"detail": "not found"})

        svc = _make_asr(handler)
        result = await svc.transcribe_url("http://mock/audio.mp3", "zh")

        assert result["text"] == "测试字幕"
        assert len(result["segments"]) == 1
        assert result["segments"][0]["text"] == "测试字幕"

    async def test_download_failure_propagates(self):
        """音频下载失败时异常上抛。"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"", status_code=404)
            return _json_resp(500)

        svc = _make_asr(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.transcribe_url("http://mock/missing.mp3", "zh")

    async def test_filename_inferred_from_url(self):
        """从 URL 路径推断音频文件名。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return _bytes_resp(b"audio")
            if request.method == "POST":
                # 提取 multipart 中的 filename
                content = request.read().decode("utf-8", errors="ignore")
                captured["body_contains_voice_wav"] = "voice.wav" in content
                return _json_resp(
                    200,
                    {"text": "ok", "segments": [{"start": 0, "end": 1, "text": "ok"}],
                     "language": "zh", "duration": 1.0},
                )
            return _json_resp(404)

        svc = _make_asr(handler)
        await svc.transcribe_url("http://mock/path/voice.wav", "zh")
        assert captured["body_contains_voice_wav"] is True
