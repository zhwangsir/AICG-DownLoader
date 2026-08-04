"""平台配置 — 复用 AICG-DownLoader 的 config.json，避免重复配置。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DownloaderConfig(BaseModel):
    """AICG-DownLoader 的 config.json 结构（部分字段）。"""

    comfy_root: str = ""
    comfy_url: str = "http://127.0.0.1:8188"
    torch_index: str = "cu130"
    download_root: str = ""
    hf_mirror: bool = True


class Settings(BaseSettings):
    """平台后端配置，从环境变量加载，同时读取 AICG-DownLoader 配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ====================================================================
    # LLM 四层流水线（2026-07-24 项目管家最终配置）
    # 全部 OpenAI 兼容接口，模型路由按场景分流
    # EXO thinking 实测：chat_template_kwargs/prompt 抑制均无效
    #   唯一可行方案：L2/L3 max_tokens 放大 5-6x 补偿 reasoning_tokens 占用
    # ====================================================================

    # --- L1 初稿生成（实时交互, 1-3s/句, 无 thinking）---
    # 用途: 剧本框架、初稿、批量对话、实时创作交互
    llm_l1_endpoint: str = "http://192.168.71.127:8000/v1/chat/completions"
    llm_l1_model: str = "qwen3.6-uncensored"
    llm_l1_max_tokens: int = 2000
    llm_l1_temperature: float = 0.8
    llm_l1_timeout: float = 30.0  # 超时后 fallback 到 L2

    # --- L2 主力剧本润色（关键场景, 6.6s/句, thinking 占 ~76%）---
    # 用途: 关键场景打磨、情感戏、转折点
    # EXO Kimi-K2.7-Code-4bit，reasoning_tokens 占 76-92%，max_tokens 必须放大 5-6x
    llm_l2_endpoint: str = "http://192.168.71.109:52415/v1/chat/completions"
    llm_l2_model: str = "mlx-community/Kimi-K2.7-Code-4bit"
    llm_l2_max_tokens: int = 12000  # 预期 content ~2000，放大 6x 补偿 reasoning
    llm_l2_temperature: float = 0.7
    llm_l2_timeout: float = 120.0
    llm_l2_thinking_amp_factor: int = 6  # reasoning 放大系数（仅文档用，逻辑层使用）

    # --- L3 终稿深度精修（异步批量, 115s/句, thinking 占 ~98%）---
    # 用途: 终稿质量提升、高难度剧情、异步批量处理
    # EXO GLM-5.2-fp8, Context 1024K, reasoning_tokens 占 80%+
    llm_l3_endpoint: str = "http://192.168.71.109:52415/v1/chat/completions"
    llm_l3_model: str = "mlx-community/GLM-5.2-fp8"
    llm_l3_max_tokens: int = 24000  # 预期 content ~4000，放大 6x 补偿 reasoning
    llm_l3_temperature: float = 0.6
    llm_l3_timeout: float = 600.0  # 115s/句 × 多句批量
    llm_l3_thinking_amp_factor: int = 6

    # --- L4 NSFW/成人内容（90s/300token, 无 thinking）---
    # 用途: NSFW 场景、亲密戏、大尺度描写
    # Spark01+02 vLLM TP=2 Ray, Euryale 70B（不开启 thinking，纯 content 产出）
    llm_l4_endpoint: str = "http://192.168.71.82:8000/v1/chat/completions"
    llm_l4_model: str = "euryale-70b"
    llm_l4_max_tokens: int = 3000
    llm_l4_temperature: float = 0.9
    llm_l4_timeout: float = 180.0  # 90s/300 token，3000 token 约 9 分钟

    # 向后兼容字段（保留给老代码引用，剧本/角色/分镜等交互 Agent 走 L1 初稿层）
    # 2026-07-27 修正：workstation :8000 已切换为 Nemotron-3-Nano-Omni-30B（vLLM），
    # 仅服务 qwen3.6-uncensored（~63tok/s）；GLM-5.2 在 EXO 实测 ~7tok/s 交互不可用，
    # GLM-5.2/Kimi-K2.7 精修走 llm_l2/l3 配置（EXO 109:52415）
    exo_base_url: str = "http://192.168.71.127:8000/v1"
    exo_api_key: str = "not-needed"
    exo_model_glm52: str = "qwen3.6-uncensored"
    exo_model_kimi: str = "qwen3.6-uncensored"

    # ====================================================================
    # 视觉质检模型
    # 2026-07-27 修正：GPU3 已切换为 Nemotron-3-Nano-Omni-30B（全模态 vLLM :8000），
    # 原 qwen3-vl-30b-thinking :8200 服务已下线，视觉质检改走 Nemotron alias
    # ====================================================================
    visual_model_url: str = "http://192.168.71.127:8000/v1"
    visual_model_name: str = "qwen3.6-uncensored"

    # ====================================================================
    # ComfyUI 集群（LB 入口 8188）
    # 5 后端轮询：本地 GPU0/1/2 (8189-8191) + pc01(:8188 v0.28.0) + pc02(:8193)；GPU3 已让给 Nemotron-3-Nano-Omni-30B
    # AGENTS.md 硬规则: 禁止直连单卡 8189-8192
    # ====================================================================
    comfyui_image_hq: str = "http://192.168.71.127:8188"
    comfyui_image_fast: str = "http://192.168.71.127:8188"
    comfyui_video_a: str = "http://192.168.71.127:8188"
    comfyui_video_b: str = "http://192.168.71.127:8188"

    # 视频生成并发度上限（与视频 worker 数对齐）
    video_max_concurrency: int = 2

    # ====================================================================
    # TTS 配音（ToIV 项目 IndexTTS2 共用）
    # workstation:9200, GPU0 (cuda:0), systemd 托管（2026-07-27 变更）
    # 备注: root 路径返回 404 正常, 需查实际 API 路径
    # ====================================================================
    tts_backend: str = "indextts"  # 'indextts' (ToIV 共用) / 'cosyvoice' / 'edge'
    # IndexTTS-2 服务（workstation:9200, ToIV 共用）
    # 2026-07-27 修正：真实契约为 POST /tts (multipart) 返回 WAV，非 OpenAI /v1/audio/speech
    indextts_endpoint: str = "http://192.168.71.127:9200"
    indextts_model: str = "IndexTTS-2"
    indextts_timeout: float = 60.0
    # 备选: CosyVoice 2-0.5B zero-shot 克隆（待部署到独立端口）
    cosyvoice_endpoint: str = "http://192.168.71.127:9201/v1"
    cosyvoice_model: str = "CosyVoice2-0.5B"
    cosyvoice_timeout: float = 60.0
    # 回退: edge-tts（无需部署）
    whisper_model: str = "tiny"  # faster-whisper 回退模型

    # ====================================================================
    # ASR 字幕（已部署到 workstation GPU1）
    # 2026-07-24 部署：qwenllm/qwen3-asr 官方镜像，vllm 后端，served_model_name=Qwen/Qwen3-ASR-1.7B
    # 调研结论: Qwen3-ASR-1.7B (阿里 2026-01 开源) 中文超越 Whisper-large-v3
    # ====================================================================
    asr_backend: str = "qwen3_asr"  # 'qwen3_asr' / 'ai_omni' / 'firered' / 'whisper'
    qwen3_asr_endpoint: str = "http://192.168.71.127:9880/v1"
    qwen3_asr_model: str = "Qwen/Qwen3-ASR-1.7B"
    qwen3_asr_timeout: float = 120.0
    firered_asr_endpoint: str = "http://192.168.71.127:8300/v1"  # 回退
    firered_asr_model: str = "FireRedTeam/FireRedASR-AED-L"
    firered_asr_timeout: float = 120.0
    # AI-Omni ASR（faster-whisper large-v3，Workstation GPU2，OpenAI 兼容端点）
    ai_omni_asr_endpoint: str = "http://192.168.71.127:9210"
    ai_omni_asr_timeout: float = 180.0

    # ====================================================================
    # EXO 图像生成（Mac Studio 集群, 可选）
    # FLUX.1-schnell/dev, FLUX.1-Kontext-dev (图像编辑), Qwen-Image
    # OpenAI 兼容 /v1/images/generations
    # ====================================================================
    exo_image_endpoint: str = "http://192.168.71.109:52415/v1/images/generations"
    exo_image_flux_schnell: str = "exolabs/FLUX.1-schnell"  # 快速
    exo_image_flux_dev: str = "exolabs/FLUX.1-dev"  # 高质量
    exo_image_flux_kontext: str = "exolabs/FLUX.1-Kontext-dev"  # 图像编辑
    exo_image_qwen: str = "exolabs/Qwen-Image"

    # ====================================================================
    # P4.1 视频生成（待管家批准 xDiT + HunyuanVideo-I2V 部署）
    # 调研结论: HunyuanVideo-I2V 720P 原生 + 超分 1080P = 开源 SOTA
    # xDiT 4 卡并行方案因 GPU0/2 已满无法部署, 改单卡 GPU3
    # ====================================================================
    # xDiT 推理引擎（workstation GPU3 单卡, HunyuanVideo-I2V 14GB FP8）
    xdit_endpoint: str = "http://192.168.71.127:8288"
    video_backend: str = "xdit"  # 'xdit' (主) / 'comfyui' (回退)
    xdit_model: str = "hunyuanvideo-i2v"
    xdit_num_frames: int = 97  # 原生 97 帧 (~4s @ 24fps), RIFLEx 扩展更长
    xdit_resolution: str = "720p"  # 720p 原生, 后期 RealBasicVSR 超分 1080p
    # 单卡模式：禁用 4 卡并行策略（GPU0/2 已满, 只用 GPU3）
    xdit_cfg_parallel: int = 1
    xdit_ulysses_degree: int = 1
    xdit_pipefusion_parallel: int = 1
    xdit_steps: int = 20
    xdit_cfg: float = 6.0
    xdit_seed: int = 0
    xdit_request_timeout: float = 1800.0
    xdit_poll_interval: float = 3.0

    # ====================================================================
    # P4.3 图像生成（待管家批准部署）
    # 调研结论: HunyuanImage 3.0 80B = 开源 SOTA, 但需 TP2 占 GPU0+1
    # 务实方案: HunyuanImage 2.1 FP8 (17B, 24GB 单卡 GPU3)
    # FLUX 2 + PuLID: 角色 ID 一致性 SOTA
    # LTX-Video 2B: 分镜预览加速
    # ====================================================================
    image_backend: str = "hunyuanimage"  # 'hunyuanimage' / 'flux_pulid' / 'sdxl' / 'exo_flux'
    hunyuanimage_endpoint: str = "http://192.168.71.127:8600/v1"
    hunyuanimage_model: str = "HunyuanImage-2.1"  # 17B FP8, 中文 prompt 最强
    hunyuanimage_timeout: float = 180.0
    hunyuanimage_default_resolution: str = "1024x1024"
    hunyuanimage_default_num_images: int = 1
    # FLUX+PuLID（角色 ID 一致性, 待部署到 GPU1 余 44GB）
    flux_pulid_endpoint: str = "http://192.168.71.127:8601/v1"
    flux_pulid_model: str = "flux.1-dev-pulid"
    flux_pulid_timeout: float = 180.0
    flux_pulid_default_resolution: str = "1024x1024"
    # LTX-Video 分镜预览（pc01, 8GB 显存, 5s 视频 ~20s 生成）
    ltx_video_enabled: bool = False
    ltx_video_endpoint: str = "http://192.168.71.115:8700/v1"
    ltx_video_model: str = "ltx-video-2b"
    ltx_video_timeout: float = 60.0
    ltx_video_default_num_frames: int = 65
    ltx_video_default_resolution: str = "512x320"

    # ====================================================================
    # P4.4 唇形同步 + 后处理（待管家批准部署）
    # 调研结论: LatentSync 1.6 = 开源唇形同步 SOTA (扩散模型, 512 分辨率)
    # RealBasicVSR x4 = 视频 4K 超分 SOTA (CVPR 2022 后无超越)
    # RIFE v4.6 = 插帧 SOTA, ProPainter = 视频修复 SOTA (ICCV 2023)
    # DeepFilterNet3 = 音频降噪 SOTA (Rust Apple Silicon 原生, Mac 集群)
    # Mac VideoToolbox H.265 硬件编码 = 远超 NVENC
    # ====================================================================
    # 唇形同步（部署后开启: workstation GPU1, 端口 8289, ~18GB 显存）
    lip_sync_enabled: bool = True
    latentsync_endpoint: str = "http://192.168.71.127:8289/v1"
    latentsync_model: str = "LatentSync-1.6"
    latentsync_timeout: float = 300.0
    latentsync_resolution: int = 512
    latentsync_seed: int = 0

    # 后处理编排总开关（已部署: video-enhance @ GPU1:8290, 三模型串行峰值 ~15GB）
    postprocess_enabled: bool = True
    # 步骤 1: 超分 (RealBasicVSR x4, 1080p→4K, FP16 6GB)
    postprocess_super_resolution_enabled: bool = True
    postprocess_endpoint: str = "http://192.168.71.127:8290/v1"
    realbasicvsr_model: str = "RealBasicVSR-x4"
    realbasicvsr_scale: int = 4
    realbasicvsr_timeout: float = 600.0
    # 步骤 2: 插帧 (RIFE v4.6, 24fps→60fps, FP16 4GB)
    postprocess_frame_interpolation_enabled: bool = True
    rife_model: str = "rife-v4.6"
    rife_target_fps: int = 60
    rife_timeout: float = 300.0
    # 步骤 3: 视频修复 (ProPainter, 去水印/去穿帮, ICCV 2023, FP16 8-10GB)
    postprocess_inpainting_enabled: bool = False
    propainter_model: str = "ProPainter"
    propainter_timeout: float = 600.0
    # 步骤 4: 音频降噪 (DeepFilterNet3, Mac studio01 CPU 实时)
    postprocess_audio_denoise_enabled: bool = True
    deepfilternet_endpoint: str = "http://192.168.71.109:8301/v1"  # Mac studio01
    deepfilternet_model: str = "deepfilternet3"
    deepfilternet_timeout: float = 60.0
    # 步骤 5: 最终编码 (Mac VideoToolbox H.265 硬件编码, 4K)
    postprocess_final_encode_enabled: bool = True
    postprocess_final_codec: str = "hevc_videotoolbox"
    postprocess_final_crf: int = 20
    postprocess_final_preset: str = "medium"
    postprocess_final_resolution: str = "3840x2160"

    # AICG-DownLoader 配置路径（相对于项目根目录）
    downloader_config_path: str = "config.json"

    # ====================================================================
    # 内置 RAG 提示词优化
    # ====================================================================
    rag_optimize_enabled: bool = True
    rag_embed_model: str = "BAAI/bge-small-zh-v1.5"
    rag_top_k: int = 5

    # 后端服务
    backend_host: str = "0.0.0.0"
    backend_port: int = 8100
    cors_origins: str = "http://localhost:3501,http://localhost:3508,http://localhost:3509,http://localhost:8085,http://localhost:5173,http://localhost:1420,http://192.168.71.47:3501"

    # 运行时填充
    downloader_config: DownloaderConfig | None = None

    def load_downloader_config(self) -> DownloaderConfig:
        """读取 AICG-DownLoader 的 config.json，实现配置共享。"""
        config_path = Path(self.downloader_config_path)
        if not config_path.is_absolute():
            project_root = Path(__file__).resolve().parents[3]
            config_path = project_root / config_path
        if config_path.exists():
            data: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
            self.downloader_config = DownloaderConfig(**data)
        else:
            self.downloader_config = DownloaderConfig()
        return self.downloader_config

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
settings.load_downloader_config()
