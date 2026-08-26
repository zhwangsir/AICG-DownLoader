"""外部服务客户端封装层。

按 AGENTS.md 规范：业务逻辑统一放入 services/，路由层只做参数校验和调用。
本包存放对接外部推理服务（FireRedASR / AI-Omni ASR / CosyVoice / IndexTTS 等）
的客户端。图像（SDXL）/视频（H3/Wan）生成经 ComfyUI 工作流直调，无独立 service。
"""

from app.services.asr_service import ASRService
from app.services.tts_service import CosyVoiceService, IndexTTSService, TTSServiceError

__all__ = [
    "ASRService",
    "CosyVoiceService",
    "IndexTTSService",
    "TTSServiceError",
]
