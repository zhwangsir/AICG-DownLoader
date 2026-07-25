"""外部服务客户端封装层。

按 AGENTS.md 规范：业务逻辑统一放入 services/，路由层只做参数校验和调用。
本包存放对接外部推理服务（xDiT / FireRedASR / CosyVoice / IndexTTS / HunyuanImage /
FLUX+PuLID / LTX-Video / LatentSync / RealBasicVSR / RIFE / ProPainter / DeepFilterNet3 等）
的客户端。
"""

from app.services.asr_service import ASRService
from app.services.image_service import (
    FluxPuLIDService,
    HunyuanImageService,
    ImageServiceError,
)
from app.services.latentsync_service import LatentSyncService, LatentSyncServiceError
from app.services.ltx_video_service import LTXVideoService, LTXVideoServiceError
from app.services.postprocess_service import (
    DeepFilterNetService,
    PostprocessService,
    PostprocessServiceError,
)
from app.services.tts_service import CosyVoiceService, IndexTTSService, TTSServiceError
from app.services.xdit_service import XDiTService

__all__ = [
    "ASRService",
    "CosyVoiceService",
    "DeepFilterNetService",
    "FluxPuLIDService",
    "HunyuanImageService",
    "ImageServiceError",
    "IndexTTSService",
    "LatentSyncService",
    "LatentSyncServiceError",
    "LTXVideoService",
    "LTXVideoServiceError",
    "PostprocessService",
    "PostprocessServiceError",
    "TTSServiceError",
    "XDiTService",
]
