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

import asyncio
import logging
import shutil
from typing import Any

import httpx

from app.config import settings
from app.core.retry import with_retry

logger = logging.getLogger(__name__)


def _looks_like_audio(data: bytes) -> bool:
    """校验字节流是否为有效音频（MP3/WAV），防止错误文本被当音频保存。

    2026-07-27 教训：IndexTTS 服务契约不匹配时返回 12 字节文本 "cloned-voice"，
    旧客户端未校验直接落盘为 .mp3，导致剪辑合成阶段 ffmpeg 才报错。
    """
    if len(data) < 12:
        return False
    # WAV: RIFF....WAVE
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True
    # MP3: ID3 标签或帧同步 0xFFEx
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return True
    return False


async def _wav_to_mp3(wav_bytes: bytes) -> bytes:
    """用 ffmpeg 将 WAV 字节转码为 MP3；ffmpeg 不可用或失败时回退原始字节。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg 不可用，IndexTTS WAV 字节原样返回")
        return wav_bytes
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0", "-f", "mp3", "-codec:a", "libmp3lame", "-q:a", "4", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=wav_bytes), timeout=60.0
        )
        if proc.returncode != 0 or not stdout:
            logger.warning("ffmpeg WAV→MP3 转码失败(rc=%s): %s", proc.returncode, stderr[:200])
            return wav_bytes
        return stdout
    except Exception as e:
        logger.warning("ffmpeg WAV→MP3 转码异常: %s", e)
        return wav_bytes


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
        if not _looks_like_audio(audio_bytes):
            raise TTSServiceError(
                f"CosyVoice 返回非音频内容 ({len(audio_bytes)} 字节): "
                f"{audio_bytes[:80]!r}"
            )
        logger.debug(
            "CosyVoice 合成: voice=%s text=%s... bytes=%d",
            voice, text[:30], len(audio_bytes),
        )
        return audio_bytes


class IndexTTSService:
    """IndexTTS-2 推理服务客户端（ToIV 共享服务，workstation:9200, GPU0）。

    2026-07-27 修正：真实 API 契约为 ToIV 自定义接口（非 OpenAI 兼容）：
    - POST {endpoint}/tts  multipart/form-data
      字段: text(必填) / emo_text(情感描述) / emo_alpha / language / ref_audio(克隆参考音频)
    - 响应: WAV 音频字节流（RIFF 头），本客户端转码为 MP3 返回
    旧实现假设的 OpenAI /v1/audio/speech 契约不存在（404），且未校验响应
    内容导致占位文本被当 MP3 落盘。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = (endpoint or settings.indextts_endpoint).rstrip("/")
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
            voice: 音色 ID（当前服务端使用默认音色，参数保留兼容调用方）
            emotion: 情感标签（neutral/happy/sad/angry/surprised），映射为 emo_text
            speed: 语速 0.5-2.0（服务端不支持时忽略）

        返回: MP3 音频字节（服务端返回 WAV，本方法用 ffmpeg 转码；
              ffmpeg 不可用时返回原始 WAV 字节，下游 ffmpeg 管线可自动嗅探）
        """
        form: dict[str, str] = {"text": text, "language": "zh"}
        if emotion and emotion != "neutral":
            form["emo_text"] = emotion

        # httpx data=dict 会发 application/x-www-form-urlencoded；
        # 真实契约为 multipart/form-data，用 files={k: (None, v)} 发送纯表单字段
        multipart = {k: (None, v) for k, v in form.items()}
        resp = await self.http.post(f"{self.endpoint}/tts", files=multipart)
        resp.raise_for_status()
        audio_bytes = resp.content
        if not audio_bytes:
            raise TTSServiceError("IndexTTS 返回空音频")
        if not _looks_like_audio(audio_bytes):
            raise TTSServiceError(
                f"IndexTTS 返回非音频内容 ({len(audio_bytes)} 字节): "
                f"{audio_bytes[:80]!r}"
            )
        logger.debug(
            "IndexTTS 合成: emotion=%s text=%s... bytes=%d",
            emotion, text[:30], len(audio_bytes),
        )
        # WAV → MP3 转码（保证 .mp3 文件名与内容一致，浏览器可直接预览）
        if audio_bytes[:4] == b"RIFF":
            audio_bytes = await _wav_to_mp3(audio_bytes)
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
