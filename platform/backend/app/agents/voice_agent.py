"""配音 Agent — 剧本台词 → 角色语音音频。

P4.2 升级：CosyVoice 2 / IndexTTS-2 为主，edge-tts 为回退。

后端选择由 settings.tts_backend 控制：
- 'cosyvoice' (默认): CosyVoice 2-0.5B，zero-shot 音色克隆，150ms 流式
- 'indextts': IndexTTS-2，情感/音色解耦，中文 WER 0.821 领先
- 'edge': edge-tts（回退），预设中文声音，无需部署

主后端失败时自动回退到 edge-tts。

流程：
1. 根据角色性别/年龄/性格选择合适的音色（cosyvoice/indextts 使用克隆模式或预设；
   edge-tts 使用预设声音映射）
2. 调用对应 TTS 后端合成语音
3. 保存音频文件并返回 URL
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import edge_tts

from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    VoiceRequest,
    VoiceResult,
)
from app.services.tts_service import CosyVoiceService, IndexTTSService, emotion_from_scene

logger = logging.getLogger(__name__)

# edge-tts 回退声音映射（中文声音）
VOICE_MAP = {
    "male_young": "zh-CN-YunxiNeural",
    "male_middle": "zh-CN-YunyangNeural",
    "male_tough": "zh-CN-YunjianNeural",
    "female_young": "zh-CN-XiaoxiaoNeural",
    "female_lively": "zh-CN-XiaoyiNeural",
    "female_gentle": "zh-CN-XiaomoNeural",
    "narrator": "zh-CN-YunfengNeural",
}

DEFAULT_VOICES = [
    "zh-CN-YunxiNeural",
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-XiaoyiNeural",
]

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "audio"


def select_voice(character_name: str, character_role: str, character_age: int | None, index: int) -> str:
    """根据角色信息选择合适的 edge-tts 声音。

    cosyvoice/indextts 后端使用各自的预设音色或克隆模式，
    此函数仅用于 edge-tts 回退路径。
    """
    role_lower = character_role.lower() if character_role else ""
    if "女" in character_role or "female" in role_lower:
        return VOICE_MAP["female_young"]
    if "主角" in character_role or "protagonist" in role_lower:
        if character_age and character_age > 35:
            return VOICE_MAP["male_middle"]
        return VOICE_MAP["male_young"]
    if "反派" in character_role or "antagonist" in role_lower:
        return VOICE_MAP["male_tough"]
    return DEFAULT_VOICES[index % len(DEFAULT_VOICES)]


def _parse_rate(rate: str) -> float:
    """将 edge-tts 的 '+10%' / '-10%' 格式转换为 speed float（0.5-2.0）。"""
    if not rate or rate == "+0%":
        return 1.0
    try:
        # 解析 '+10%' → 1.1, '-10%' → 0.9
        sign = 1.0 if rate.startswith("+") else -1.0
        pct = float(rate.rstrip("%").lstrip("+-"))
        return max(0.5, min(2.0, 1.0 + sign * pct / 100.0))
    except (ValueError, AttributeError):
        return 1.0


class VoiceAgent(BaseAgent):
    """配音 Agent：台词 → TTS → 音频文件。

    后端选择由 settings.tts_backend 控制：
    - 'cosyvoice' (默认): CosyVoice 2-0.5B，zero-shot 克隆
    - 'indextts': IndexTTS-2，情感解耦
    - 'edge': edge-tts（回退）

    主后端失败时自动回退到 edge-tts。
    """

    def __init__(self):
        super().__init__("voice_agent")
        self._cosyvoice: CosyVoiceService | None = None
        self._indextts: IndexTTSService | None = None

    @property
    def cosyvoice_service(self) -> CosyVoiceService:
        if self._cosyvoice is None:
            self._cosyvoice = CosyVoiceService(http_client=self.http)
        return self._cosyvoice

    @property
    def indextts_service(self) -> IndexTTSService:
        if self._indextts is None:
            self._indextts = IndexTTSService(http_client=self.http)
        return self._indextts

    async def execute(self, request: VoiceRequest) -> AgentResponse:
        start = time.time()
        backend = settings.tts_backend.lower()
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            tasks = []
            for i, line in enumerate(request.dialogues):
                voice = select_voice(
                    line.character_name,
                    line.character_role,
                    line.character_age,
                    i,
                )
                text = line.text
                rate = line.rate
                filename = f"voice_scene_{request.scene_id}_{i:02d}_{voice}.mp3"
                filepath = OUTPUT_DIR / filename
                tasks.append(
                    self._generate_one(text, voice, rate, filepath, filename, backend, line)
                )

            results = await asyncio.gather(*tasks)

            return AgentResponse(
                success=True,
                data=VoiceResult(
                    scene_id=request.scene_id,
                    audio_urls=results,
                    total_lines=len(results),
                ).model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"配音生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def _generate_one(
        self,
        text: str,
        voice: str,
        rate: str,
        filepath: Path,
        filename: str,
        backend: str,
        line=None,
    ) -> dict:
        """生成单条语音，按 backend 派发到对应 TTS 服务。

        主后端失败时自动回退到 edge-tts。
        """
        speed = _parse_rate(rate)
        emotion = emotion_from_scene(getattr(line, "emotion", "neutral")) if line else "neutral"

        try:
            if backend == "cosyvoice":
                audio_bytes = await self.cosyvoice_service.synthesize(
                    text=text,
                    voice=voice,
                    speed=speed,
                )
            elif backend == "indextts":
                audio_bytes = await self.indextts_service.synthesize(
                    text=text,
                    voice=voice,
                    emotion=emotion,
                    speed=speed,
                )
            else:
                # edge-tts 直接走原路径
                return await self._generate_via_edge(text, voice, rate, filepath, filename)
        except Exception as tts_err:
            logger.warning(
                "TTS 后端 %s 失败，回退 edge-tts: voice=%s err=%s",
                backend, voice, tts_err,
            )
            # 回退到 edge-tts
            return await self._generate_via_edge(text, voice, rate, filepath, filename)

        # 保存 cosyvoice/indextts 返回的音频字节
        filepath.write_bytes(audio_bytes)
        base_url = f"http://localhost:{settings.backend_port}"
        return {
            "filename": filename,
            "voice": voice,
            "backend": backend,
            "text": text[:50] + "..." if len(text) > 50 else text,
            "audio_url": f"{base_url}/static/audio/{filename}",
        }

    async def _generate_via_edge(
        self,
        text: str,
        voice: str,
        rate: str,
        filepath: Path,
        filename: str,
    ) -> dict:
        """使用 edge-tts 生成单条语音（回退路径）。"""
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
        await communicate.save(str(filepath))

        base_url = f"http://localhost:{settings.backend_port}"
        return {
            "filename": filename,
            "voice": voice,
            "backend": "edge",
            "text": text[:50] + "..." if len(text) > 50 else text,
            "audio_url": f"{base_url}/static/audio/{filename}",
        }


voice_agent = VoiceAgent()
