"""TTS 推理服务客户端 — CosyVoice 2 + IndexTTS-2 双 TTS。

P4.2 升级引入两个 TTS 服务，按场景路由：
- CosyVoice 2-0.5B（阿里）：zero-shot 音色克隆 + 150ms 流式，适合主角/重要角色
- IndexTTS-2（B 站）：情感/音色解耦，中文 WER 0.821 领先，适合情感戏

两个服务 API 契约基本一致（均采用 OpenAI 兼容的 /audio/speech 接口）：
- POST /v1/audio/speech
    请求 JSON: {
        "model": str,                    # 模型名
        "input": str,                     # 合成文本
        "voice": str,                     # 音色 ID 或参考音频 URL
        "response_format": "mp3",         # 输出格式
        "speed": float,                   # 语速 0.5-2.0
        "emotion": str (仅 IndexTTS),     # 情感标签
    }
    响应: 音频二进制流（Content-Type: audio/mpeg）

edge-tts 作为本地回退路径，无需部署服务，由 VoiceAgent 直接调用。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.core.retry import with_retry

logger = logging.getLogger(__name__)


class TTSServiceError(RuntimeError):
    """TTS 服务调用异常。"""


class CosyVoiceService:
    """CosyVoice 2 推理服务客户端。

    适合 zero-shot 音色克隆场景：传入参考音频 URL 即可克隆音色，
    无需训练 LoRA。150ms 首包流式输出。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.cosyvoice_endpoint
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.cosyvoice_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def synthesize(
        self,
        text: str,
        voice: str,
        reference_audio_url: str | None = None,
        speed: float = 1.0,
    ) -> bytes:
        """调用 CosyVoice 2 合成语音。

        参数:
            text: 合成文本
            voice: 音色 ID（预设音色）或 "clone" 表示走 reference_audio_url 克隆
            reference_audio_url: 克隆模式的参考音频 URL（voice=='clone' 时必填）
            speed: 语速 0.5-2.0，默认 1.0

        返回: MP3 音频字节
        """
        payload: dict[str, Any] = {
            "model": settings.cosyvoice_model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }
        if reference_audio_url:
            payload["reference_audio"] = reference_audio_url

        resp = await self.http.post(
            f"{self.endpoint}/audio/speech",
            json=payload,
        )
        resp.raise_for_status()
        audio_bytes = resp.content
        if not audio_bytes:
            raise TTSServiceError("CosyVoice 返回空音频")
        logger.debug(
            "CosyVoice 合成: voice=%s text=%s... bytes=%d",
            voice, text[:30], len(audio_bytes),
        )
        return audio_bytes


class IndexTTSService:
    """IndexTTS-2 推理服务客户端。

    支持情感/音色解耦，中文 WER 0.821 领先。
    适合情感戏、爆发戏等需要情感控制的场景。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.indextts_endpoint
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.indextts_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def synthesize(
        self,
        text: str,
        voice: str,
        emotion: str = "neutral",
        speed: float = 1.0,
    ) -> bytes:
        """调用 IndexTTS-2 合成语音。

        参数:
            text: 合成文本
            voice: 音色 ID
            emotion: 情感标签（neutral/happy/sad/angry/surprised）
            speed: 语速 0.5-2.0

        返回: MP3 音频字节
        """
        payload: dict[str, Any] = {
            "model": settings.indextts_model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
            "emotion": emotion,
        }

        resp = await self.http.post(
            f"{self.endpoint}/audio/speech",
            json=payload,
        )
        resp.raise_for_status()
        audio_bytes = resp.content
        if not audio_bytes:
            raise TTSServiceError("IndexTTS 返回空音频")
        logger.debug(
            "IndexTTS 合成: voice=%s emotion=%s text=%s... bytes=%d",
            voice, emotion, text[:30], len(audio_bytes),
        )
        return audio_bytes


# 情感标签映射（IndexTTS-2 支持）
EMOTION_MAP = {
    "neutral": "neutral",
    "tension": "angry",
    "romantic": "happy",
    "happy": "happy",
    "sad": "sad",
    "mysterious": "neutral",
}


def emotion_from_scene(emotion: str) -> str:
    """从剧本场景情感标签映射到 IndexTTS 情感标签。"""
    return EMOTION_MAP.get(emotion, "neutral")
