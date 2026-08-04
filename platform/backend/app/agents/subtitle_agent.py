"""字幕 Agent — 音频 → ASR 转写 → AI 修正 → SRT 字幕。

P4.2 升级：FireRedASR-AED-L 1.1B 为主（CER <1%），faster-whisper 为回退（CER 8-9%）。

后端选择由 settings.asr_backend 控制：
- 'firered' (默认): FireRedASR-AED-L 1.1B，CER <1%，workstation GPU
- 'whisper': faster-whisper tiny，CPU 可跑，作为回退

FireRedASR 主路径失败时自动回退到 faster-whisper。

流程：
1. 从 audio_url 下载音频文件（支持本地静态资源或远程 URL）
2. ASR 语音识别（FireRedASR 为主，faster-whisper 回退）
3. AI 优化：用 LLM 修正字幕错别字和语法问题（保持时间轴不变）
4. 生成 SRT 格式字幕并返回
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.agents.ai_optimizer import optimize_content
from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    SubtitleRequest,
    SubtitleResult,
    SubtitleSegment,
)
from app.services.asr_service import ASRService

logger = logging.getLogger(__name__)

# faster-whisper 回退模型（保持原 tiny 默认）
DEFAULT_MODEL = "tiny"

# 输出目录
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "subtitle"


def _format_timestamp(seconds: float) -> str:
    """将秒数格式化为 SRT 时间戳 HH:MM:SS,mmm。"""
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _build_srt(segments: list) -> str:
    """将 segments 转换为 SRT 字幕文本。

    兼容两种数据结构：
    - faster-whisper Segment 对象（有 .start/.end/.text 属性）
    - FireRedASR 字典（有 start/end/text 键）
    """
    lines = []
    for i, seg in enumerate(segments, 1):
        start = seg.start if hasattr(seg, "start") else seg["start"]
        end = seg.end if hasattr(seg, "end") else seg["end"]
        text = (seg.text if hasattr(seg, "text") else seg["text"]).strip()
        if not text:
            continue
        lines.append(f"{i}")
        lines.append(f"{_format_timestamp(start)} --> {_format_timestamp(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()


class SubtitleAgent(BaseAgent):
    """字幕 Agent：音频 → ASR → SRT 字幕。

    后端选择由 settings.asr_backend 控制：
    - 'firered' (默认): FireRedASR-AED-L 1.1B，CER <1%
    - 'whisper': faster-whisper tiny（回退）

    FireRedASR 失败时自动回退到 faster-whisper。
    """

    def __init__(self):
        super().__init__("subtitle_agent")
        self._model = None  # faster-whisper 懒加载
        self._asr: ASRService | None = None  # FireRedASR 懒加载

    @property
    def asr_service(self) -> ASRService:
        """懒加载 ASRService，复用 BaseAgent 的 httpx 客户端。"""
        if self._asr is None:
            self._asr = ASRService(http_client=self.http)
        return self._asr

    async def execute(self, request: SubtitleRequest) -> AgentResponse:
        start = time.time()
        backend = settings.asr_backend.lower()
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            # 下载音频到本地路径（FireRedASR 和 faster-whisper 都需要）
            audio_path = await self._download_audio(request.audio_url)

            # ASR 转写：按后端派发
            if backend == "ai_omni":
                segments_data, language = await self._transcribe_via_ai_omni(
                    audio_path, request.language
                )
            elif backend == "firered":
                try:
                    segments_data, language = await self._transcribe_via_firered(
                        audio_path, request.language
                    )
                except Exception as firered_err:
                    logger.warning(
                        "FireRedASR 失败，回退 faster-whisper: %s", firered_err,
                    )
                    segments_data, language = await self._transcribe_via_whisper(
                        audio_path, request.language
                    )
            else:
                segments_data, language = await self._transcribe_via_whisper(
                    audio_path, request.language
                )

            # 构建 SRT
            srt_content = _build_srt(segments_data)

            # AI 优化：用 LLM 修正字幕错别字和语法问题
            if segments_data:
                try:
                    original_texts = "\n".join(
                        f"{i+1}. {seg.text.strip() if hasattr(seg, 'text') else seg['text'].strip()}"
                        for i, seg in enumerate(segments_data)
                    )
                    optimized_texts = await optimize_content(
                        original_texts,
                        task_type="subtitle",
                        extra_instruction="逐行修正错别字和语法，保持行号对应，每行一条字幕，不要合并或拆分行",
                    )
                    # 解析优化后的文本，按行号更新
                    optimized_lines = optimized_texts.strip().split("\n")
                    for line in optimized_lines:
                        if ". " in line:
                            parts = line.split(". ", 1)
                            try:
                                idx = int(parts[0]) - 1
                                if 0 <= idx < len(segments_data) and parts[1].strip():
                                    seg = segments_data[idx]
                                    if hasattr(seg, "text"):
                                        seg.text = parts[1].strip()
                                    else:
                                        seg["text"] = parts[1].strip()
                            except (ValueError, IndexError):
                                pass
                    # 重新构建 SRT
                    srt_content = "\n".join(
                        f"{i+1}\n{_format_timestamp(seg.start if hasattr(seg, 'start') else seg['start'])} --> "
                        f"{_format_timestamp(seg.end if hasattr(seg, 'end') else seg['end'])}\n"
                        f"{(seg.text if hasattr(seg, 'text') else seg['text']).strip()}\n"
                        for i, seg in enumerate(segments_data)
                    )
                    logger.info("字幕 AI 优化完成: %d 段", len(segments_data))
                except Exception as e:
                    logger.warning("字幕 AI 优化失败，使用原始结果: %s", e)

            # 保存 SRT 文件
            srt_filename = f"subtitle_scene_{request.scene_id}.srt"
            srt_filepath = OUTPUT_DIR / srt_filename
            srt_filepath.write_text(srt_content, encoding="utf-8")

            base_url = f"http://localhost:{settings.backend_port}"
            return AgentResponse(
                success=True,
                data=SubtitleResult(
                    scene_id=request.scene_id,
                    srt_content=srt_content,
                    segments=[
                        SubtitleSegment(
                            start=seg.start if hasattr(seg, "start") else seg["start"],
                            end=seg.end if hasattr(seg, "end") else seg["end"],
                            text=(seg.text if hasattr(seg, "text") else seg["text"]).strip(),
                        )
                        for seg in segments_data
                        if (seg.text if hasattr(seg, "text") else seg["text"]).strip()
                    ],
                    language=language,
                ).model_dump()
                | {"srt_url": f"{base_url}/static/subtitle/{srt_filename}"},
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"字幕生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def _transcribe_via_ai_omni(
        self, audio_path: str, language: str
    ) -> tuple[list[dict], str]:
        """AI-Omni ASR 路径：Workstation :9210（faster-whisper large-v3）。

        OpenAI 兼容端点 /v1/audio/transcriptions，verbose_json 返回含
        segments 时间轴（2026-08-04 服务端已补回 segments 字段）。
        返回 (segments, language)，segments 为 [{"start","end","text"}]。
        """
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        resp = await self.http.post(
            f"{settings.ai_omni_asr_endpoint}/v1/audio/transcriptions",
            files={"file": (Path(audio_path).name, audio_bytes, "audio/mpeg")},
            data={
                "response_format": "verbose_json",
                "language": language if language != "auto" else "zh",
            },
            timeout=settings.ai_omni_asr_timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        segments = data.get("segments") or []
        if not segments and data.get("text"):
            # 服务端未返回时间轴时整段兜底，保证 SRT 可构建
            segments = [
                {
                    "start": 0.0,
                    "end": float(data.get("duration", 0.0)),
                    "text": data["text"],
                }
            ]
        logger.info("AI-Omni ASR 转写: %d 段", len(segments))
        return segments, data.get("language", language)

    async def _transcribe_via_firered(
        self, audio_path: str, language: str
    ) -> tuple[list[dict], str]:
        """FireRedASR 路径：读取音频字节 → 调用服务 → 返回 (segments, language)。

        返回的 segments 是字典列表 [{"start","end","text"}]，与 _build_srt 兼容。
        """
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        result = await self.asr_service.transcribe(
            audio_bytes,
            filename=Path(audio_path).name,
            language=language if language != "auto" else "zh",
        )
        segments = result.get("segments", [])
        detected_lang = result.get("language", language)
        logger.info("FireRedASR 转写: %d 段", len(segments))
        return segments, detected_lang

    async def _transcribe_via_whisper(
        self, audio_path: str, language: str
    ) -> tuple[list, str]:
        """faster-whisper 回退路径（原 _transcribe 逻辑）。"""
        segments, info = await asyncio.to_thread(
            self._transcribe, audio_path, language
        )
        return list(segments), info.language if info else language

    async def _download_audio(self, audio_url: str) -> str:
        """下载音频文件到临时路径。如果是本地静态资源（localhost），直接读取。"""
        parsed = urlparse(audio_url)
        # 本地静态资源：直接从文件系统读取
        if parsed.hostname in ("localhost", "127.0.0.1") and "/static/audio/" in audio_url:
            local_path = OUTPUT_DIR.parent / "audio" / Path(parsed.path).name
            if local_path.exists():
                return str(local_path)

        # 远程 URL：下载到临时文件
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("GET", audio_url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        tmp.write(chunk)
            tmp.close()
            return tmp.name
        except Exception:
            tmp.close()
            raise

    def _get_model(self):
        """懒加载 Whisper 模型（避免启动时加载）。"""
        if self._model is None:
            from faster_whisper import WhisperModel

            model_size = settings.whisper_model or DEFAULT_MODEL
            # CPU 推理（开发机无 GPU），int8 量化降低内存占用
            self._model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
            )
        return self._model

    def _transcribe(self, audio_path: str, language: str):
        """调用 faster-whisper 进行转写。"""
        model = self._get_model()
        return model.transcribe(
            audio_path,
            language=None if language == "auto" else language,
            beam_size=5,
            vad_filter=True,
        )


subtitle_agent = SubtitleAgent()
