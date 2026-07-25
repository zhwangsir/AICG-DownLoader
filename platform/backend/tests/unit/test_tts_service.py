"""TTS Service 单元测试。

使用 httpx.MockTransport 模拟 CosyVoice 2 与 IndexTTS-2 服务，覆盖：
- CosyVoiceService.synthesize: 主路径正常调用、参数传递、空音频错误、HTTP 错误
- IndexTTSService.synthesize: 主路径正常调用、emotion 参数、空音频错误、HTTP 错误
- emotion_from_scene: 场景情感标签到 IndexTTS 情感标签映射
- 回退路径：服务返回空字节 / 5xx 时由 with_retry 重试
"""

from __future__ import annotations

import httpx
import pytest

from app.services.tts_service import (
    CosyVoiceService,
    IndexTTSService,
    TTSServiceError,
    emotion_from_scene,
    EMOTION_MAP,
)


def _make_cosyvoice(handler) -> CosyVoiceService:
    """构造使用 MockTransport 的 CosyVoiceService 实例。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return CosyVoiceService(http_client=client)


def _make_indextts(handler) -> IndexTTSService:
    """构造使用 MockTransport 的 IndexTTSService 实例。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return IndexTTSService(http_client=client)


def _bytes_resp(content: bytes, status_code: int = 200) -> httpx.Response:
    """构造二进制响应（模拟音频流）。"""
    return httpx.Response(status_code, content=content)


def _json_resp(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=json_data or {})


# ============================================================================
# CosyVoice 2 Service 测试
# ============================================================================


class TestCosyVoiceSynthesize:
    """CosyVoiceService.synthesize 方法测试。"""

    async def test_success_returns_audio_bytes(self):
        """正常调用返回 MP3 字节流。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["content_type"] = request.headers.get("content-type", "")
            # 解析 JSON body
            import json

            captured["body"] = json.loads(request.read().decode())
            return _bytes_resp(b"fake-mp3-bytes")

        svc = _make_cosyvoice(handler)
        audio = await svc.synthesize(
            text="你好世界", voice="zh-CN-YunxiNeural", speed=1.0
        )

        assert audio == b"fake-mp3-bytes"
        assert captured["method"] == "POST"
        assert "/audio/speech" in captured["url"]
        assert captured["body"]["input"] == "你好世界"
        assert captured["body"]["voice"] == "zh-CN-YunxiNeural"
        assert captured["body"]["response_format"] == "mp3"
        assert captured["body"]["speed"] == 1.0
        # model 字段来自 settings.cosyvoice_model
        assert "model" in captured["body"]

    async def test_speed_param_passed_through(self):
        """speed 参数透传到请求体。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(request.read().decode())
            return _bytes_resp(b"audio")

        svc = _make_cosyvoice(handler)
        await svc.synthesize(text="加速播放", voice="v1", speed=1.5)

        assert captured["body"]["speed"] == 1.5

    async def test_clone_mode_with_reference_audio(self):
        """voice='clone' 时传入 reference_audio_url 参数。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(request.read().decode())
            return _bytes_resp(b"cloned-voice")

        svc = _make_cosyvoice(handler)
        await svc.synthesize(
            text="克隆音色",
            voice="clone",
            reference_audio_url="http://mock/ref.wav",
            speed=1.0,
        )

        assert captured["body"]["voice"] == "clone"
        assert captured["body"]["reference_audio"] == "http://mock/ref.wav"

    async def test_empty_audio_raises(self):
        """服务返回空字节时抛 TTSServiceError。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _bytes_resp(b"")

        svc = _make_cosyvoice(handler)
        with pytest.raises(TTSServiceError, match="空音频"):
            await svc.synthesize(text="test", voice="v1")

    async def test_http_error_raises(self):
        """HTTP 500 错误时抛 HTTPStatusError（由 with_retry 重试后仍失败）。"""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _json_resp(500, {"detail": "CosyVoice service down"})

        svc = _make_cosyvoice(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.synthesize(text="test", voice="v1")
        # with_retry max_attempts=3 → 调用 3 次
        assert call_count == 3

    async def test_endpoint_path_correct(self):
        """请求 URL 为 {endpoint}/audio/speech（OpenAI 兼容路径）。"""
        captured_url: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_url.append(str(request.url))
            return _bytes_resp(b"audio")

        svc = _make_cosyvoice(handler)
        await svc.synthesize(text="t", voice="v")

        # conftest.py 中 cosyvoice_endpoint = http://localhost:8400/v1
        assert captured_url[0] == "http://localhost:8400/v1/audio/speech"


# ============================================================================
# IndexTTS-2 Service 测试
# ============================================================================


class TestIndexTTSSynthesize:
    """IndexTTSService.synthesize 方法测试。"""

    async def test_success_returns_audio_bytes(self):
        """正常调用返回 MP3 字节流。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            import json

            captured["body"] = json.loads(request.read().decode())
            return _bytes_resp(b"index-mp3-bytes")

        svc = _make_indextts(handler)
        audio = await svc.synthesize(
            text="情感台词", voice="narrator", emotion="happy", speed=0.9
        )

        assert audio == b"index-mp3-bytes"
        assert captured["method"] == "POST"
        assert "/audio/speech" in captured["url"]
        assert captured["body"]["input"] == "情感台词"
        assert captured["body"]["voice"] == "narrator"
        assert captured["body"]["emotion"] == "happy"
        assert captured["body"]["speed"] == 0.9
        assert captured["body"]["response_format"] == "mp3"

    async def test_default_emotion_neutral(self):
        """未传 emotion 时默认 'neutral'。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(request.read().decode())
            return _bytes_resp(b"audio")

        svc = _make_indextts(handler)
        await svc.synthesize(text="t", voice="v")

        assert captured["body"]["emotion"] == "neutral"

    async def test_emotion_param_passed_through(self):
        """emotion 参数透传。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(request.read().decode())
            return _bytes_resp(b"audio")

        svc = _make_indextts(handler)
        await svc.synthesize(text="t", voice="v", emotion="sad")

        assert captured["body"]["emotion"] == "sad"

    async def test_empty_audio_raises(self):
        """服务返回空字节时抛 TTSServiceError。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return _bytes_resp(b"")

        svc = _make_indextts(handler)
        with pytest.raises(TTSServiceError, match="空音频"):
            await svc.synthesize(text="t", voice="v")

    async def test_http_error_raises(self):
        """HTTP 500 错误重试 3 次后仍抛 HTTPStatusError。"""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _json_resp(503, {"detail": "IndexTTS unavailable"})

        svc = _make_indextts(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.synthesize(text="t", voice="v")
        assert call_count == 3

    async def test_endpoint_path_correct(self):
        """请求 URL 为 {endpoint}/audio/speech。"""
        captured_url: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_url.append(str(request.url))
            return _bytes_resp(b"audio")

        svc = _make_indextts(handler)
        await svc.synthesize(text="t", voice="v")

        # conftest.py 中 indextts_endpoint = http://localhost:8500/v1
        assert captured_url[0] == "http://localhost:8500/v1/audio/speech"


# ============================================================================
# 情感标签映射测试
# ============================================================================


class TestEmotionFromScene:
    """emotion_from_scene 函数测试。"""

    def test_known_emotions(self):
        """已知场景情感标签正确映射。"""
        assert emotion_from_scene("neutral") == "neutral"
        assert emotion_from_scene("tension") == "angry"
        assert emotion_from_scene("romantic") == "happy"
        assert emotion_from_scene("happy") == "happy"
        assert emotion_from_scene("sad") == "sad"
        assert emotion_from_scene("mysterious") == "neutral"

    def test_unknown_emotion_defaults_to_neutral(self):
        """未知情感标签回退到 'neutral'。"""
        assert emotion_from_scene("unknown") == "neutral"
        assert emotion_from_scene("") == "neutral"
        assert emotion_from_scene("anything-not-in-map") == "neutral"

    def test_emotion_map_completeness(self):
        """EMOTION_MAP 覆盖了剧本常用情感。"""
        expected_keys = {"neutral", "tension", "romantic", "happy", "sad", "mysterious"}
        assert expected_keys.issubset(EMOTION_MAP.keys())
        # 所有映射值必须是 IndexTTS 支持的情感标签
        valid_emotions = {"neutral", "happy", "sad", "angry", "surprised"}
        for v in EMOTION_MAP.values():
            assert v in valid_emotions


# ============================================================================
# 跨服务一致性测试
# ============================================================================


class TestTTSServiceConsistency:
    """两个 TTS 服务的 API 契约一致性验证。"""

    async def test_both_services_use_same_endpoint_path(self):
        """CosyVoice 与 IndexTTS 都使用 /audio/speech 路径（OpenAI 兼容）。"""
        urls: list[str] = []

        def cv_handler(request: httpx.Request) -> httpx.Response:
            urls.append(("cv", str(request.url)))
            return _bytes_resp(b"a")

        def it_handler(request: httpx.Request) -> httpx.Response:
            urls.append(("it", str(request.url)))
            return _bytes_resp(b"b")

        await _make_cosyvoice(cv_handler).synthesize(text="t", voice="v")
        await _make_indextts(it_handler).synthesize(text="t", voice="v")

        # 两者路径都是 /audio/speech（端点域名不同）
        cv_url = next(u for k, u in urls if k == "cv")
        it_url = next(u for k, u in urls if k == "it")
        assert cv_url.endswith("/audio/speech")
        assert it_url.endswith("/audio/speech")

    async def test_both_services_return_bytes(self):
        """两个服务都返回 bytes 类型（Content-Type: audio/mpeg）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"audio", headers={"content-type": "audio/mpeg"})

        cv_audio = await _make_cosyvoice(handler).synthesize(text="t", voice="v")
        it_audio = await _make_indextts(handler).synthesize(text="t", voice="v")

        assert isinstance(cv_audio, bytes)
        assert isinstance(it_audio, bytes)
        assert cv_audio == it_audio == b"audio"
