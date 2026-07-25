"""ASR 推理服务客户端 — FireRedASR-AED-L 1.1B。

FireRedASR 是小红书 FireRed 团队开源的中文 ASR 模型，AISHELL-1 CER 0.57-1%
（vs faster-whisper large-v3 8-9%），是 P4.2 升级的核心模型。

本模块封装 FireRedASR 服务的 HTTP 调用：
- POST /v1/asr/transcribe   上传音频并转写，返回带时间戳的分段结果

FireRedASR 服务 API 契约（由 Workstation 部署的 FastAPI wrapper 提供）：
- POST /v1/asr/transcribe
    请求: multipart/form-data，字段 audio=二进制音频文件，language=zh/en（可选）
    响应: {
        "text": str,                           # 完整文本
        "segments": [                           # 分段时间戳
            {"start": float, "end": float, "text": str}
        ],
        "language": str,
        "duration": float,                      # 音频总时长（秒）
    }

P4.2 设计目标：
- 替换 faster-whisper tiny（CER 8-9%）→ FireRedASR-AED-L 1.1B（CER <1%）
- 保持 segments 数据结构兼容（start/end/text），上层 SubtitleAgent 无需改动
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.core.retry import with_retry

logger = logging.getLogger(__name__)


class ASRServiceError(RuntimeError):
    """ASR 服务调用异常。"""


class ASRService:
    """FireRedASR 推理服务客户端。

    使用 BaseAgent 注入的 httpx.AsyncClient（trust_env=False 避免 macOS 代理拦截 IPv6）。
    所有外呼经 with_retry 装饰，对瞬时网络错误自动重试。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.firered_asr_endpoint
        # trust_env=False 同 base.py：避免 macOS 系统 HTTP 代理拦截 IPv6 请求
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.firered_asr_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.mp3",
        language: str = "zh",
    ) -> dict[str, Any]:
        """上传音频字节到 FireRedASR 服务并转写。

        返回 {"text": str, "segments": [{"start","end","text"}], "language": str, "duration": float}
        """
        resp = await self.http.post(
            f"{self.endpoint}/asr/transcribe",
            files={"audio": (filename, audio_bytes, "audio/mpeg")},
            data={"language": language} if language else None,
        )
        resp.raise_for_status()
        data = resp.json()

        # 必要字段校验
        if "segments" not in data:
            raise ASRServiceError(f"FireRedASR 返回缺少 segments: {data}")
        if not isinstance(data["segments"], list):
            raise ASRServiceError(f"FireRedASR segments 格式错误: {type(data['segments'])}")

        logger.info(
            "FireRedASR 转写完成: segments=%d duration=%.2fs",
            len(data["segments"]),
            float(data.get("duration", 0)),
        )
        return data

    async def transcribe_url(self, audio_url: str, language: str = "zh") -> dict[str, Any]:
        """从 URL 下载音频并转写（端到端编排）。

        1. 下载音频字节
        2. 调用 transcribe 转写
        """
        # 1. 下载音频
        audio_resp = await self.http.get(audio_url)
        audio_resp.raise_for_status()
        audio_bytes = audio_resp.content

        # 2. 从 URL 推断文件名
        from urllib.parse import urlparse
        parsed = urlparse(audio_url)
        filename = parsed.path.rsplit("/", 1)[-1] or "audio.mp3"

        # 3. 转写
        return await self.transcribe(audio_bytes, filename, language)
