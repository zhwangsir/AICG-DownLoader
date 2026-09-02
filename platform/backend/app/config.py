"""平台配置 — 复用仓库根目录 config.json（DOWNLOADER_CONFIG_PATH），避免重复配置。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DownloaderConfig(BaseModel):
    """仓库根目录 config.json 结构（部分字段）。"""

    comfy_root: str = ""
    comfy_url: str = "http://127.0.0.1:8188"
    torch_index: str = "cu130"
    download_root: str = ""
    hf_mirror: bool = True


class Settings(BaseSettings):
    """平台后端配置，从环境变量加载，同时读取仓库根目录下载器配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ====================================================================
    # LLM 入口（2026-08 架构：全部收敛 spark02 qwen3.6-uncensored）
    # ====================================================================
    # 历史四层流水线（llm_l1/l2/l3 → Nemotron/EXO，llm_l4 → spark01 Euryale-70B）
    # 已全部退役：Nemotron vLLM 停用于 2026-08-05，spark01 2026-08-08 起改为
    # Omni-Captioner 音乐反推（不再是 LLM）。剧本/角色/分镜等所有交互 Agent
    # 统一经 BaseAgent.llm_client -> exo_base_url 调用 spark02 :8000。

    # 主 LLM：spark02 :8000 qwen3.6-uncensored（Qwen3.8-27B-Uncensored-FP8）
    # （字段名沿用 exo_* 仅为兼容旧代码；实际指向 spark02，非 EXO 集群）
    exo_base_url: str = "http://192.168.71.84:8000/v1"
    exo_api_key: str = "not-needed"
    exo_model_glm52: str = "qwen3.6-uncensored"
    exo_model_kimi: str = "qwen3.6-uncensored"

    # ====================================================================
    # 视觉质检模型（spark01 VLM，Qwen3-VL-32B-Instruct-FP8）
    # 与 LLM 拆分：spark01 :8000 跑 Qwen3-VL-32B（别名 qwen3-vl-32b /
    # molmo2-8b / omni-captioner）；spark02 只做文本 LLM。
    # 与 .env.example / ToIV 对齐。无 .env 时也不得漂回 spark02。
    # ====================================================================
    visual_model_url: str = "http://192.168.71.82:8000/v1"
    visual_model_name: str = "qwen3-vl-32b"
    # M16.2 分镜拼贴检测：关键帧生成后校验出场角色外貌一致性，失真自动重试
    storyboard_appearance_check: bool = True
    # M18.2 三视图 VLM 质检：三视图生成后、角色卡入库前校验 front 合格
    # （单人/非素材参考表/外貌符合描述）+ side/closeup 与 front 同角色；
    # 不合格自动换 seed 重生成（最多 character_view_qc_max_retries 次），
    # 重试耗尽判失败废品不入库；VLM 未配置/异常/坏 JSON 一律 fail-open 放行
    character_view_qc_enabled: bool = True
    character_view_qc_max_retries: int = 2
    # M18.3 关键帧定妆照锚定：SDXL 分镜生成时将角色定妆照 front 作为 IPAdapter
    # 图像参考注入工作流，从源头锚定角色外观/服饰/整体设定一致性；
    # 参考图上传或节点装配异常一律回退原工作流（锚定是增强不是阻断）
    storyboard_keyframe_anchor_enabled: bool = True
    storyboard_keyframe_anchor_weight: float = 0.6
    # M25.2 AutoLink 自动资产匹配：分镜提示词装配前扫描场景文本
    # （description/character_actions/dialogue），文本提及的资产库角色自动
    # 并入出场角色（外观锁定卡注入 + 定妆照锚定图源）；仅精确/CI 包含匹配，
    # 不做 fuzzy（宁缺毋滥）；请求级 StoryboardRequest.auto_link_assets 可覆盖
    auto_link_assets_enabled: bool = True
    # M25.9 C1 线稿先行两段式分镜（DramaClaw 虾导本地化）：
    # 草图阶段低步数/低 CFG/小尺寸快速出构图（返工成本卡在最便宜阶段），
    # 用户确认后同 seed 精渲染（防构图漂移——草图与精图共享确定性锚点）。
    # 默认关闭：一键成片全自动流水线无人值守时不启用（前端分镜修正场景手动开启）
    sketch_mode_enabled: bool = True
    sketch_steps: int = 8        # 精渲染 25 步的 1/3 耗时
    sketch_cfg: float = 4.0      # 低 CFG 给构图更多自由度
    sketch_width: int = 512      # 9:16 同比例小尺寸
    sketch_height: int = 896
    ipadapter_sdxl_model_name: str = "ip-adapter-plus-face_sdxl_vit-h.safetensors"
    ipadapter_clip_vision_name: str = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
    # M18.3.1 LB 后端直连清单（逗号分隔）：LB /upload/image 轮询单实例而 /prompt
    # 按负载选实例，定妆照只落单点后端会导致 LoadImage 跨后端找不到文件（400）。
    # 配置后定妆照以同一文件名直连复制到每个后端；空串 = 旧行为（经 LB 上传一次）
    comfyui_lb_backend_urls: str = ""
    # M18.4 H3 画风漂移治理（约束 + 检测 + 纠偏三层）：
    # 约束：H3 三条 prompt 路径（fl2va/r2v/多镜）统一做画风冲突清洗 + 幂等风格尾
    # 检测：H3 产出视频中点帧送 VLM 比对目标画风（fail-open，质检故障不阻断生产）
    # 纠偏：漂移时前置强化画风子句 + 换 seed 重提交，重试耗尽放行最后结果
    h3_style_anchor_enabled: bool = True
    h3_style_qc_enabled: bool = True
    h3_style_qc_max_retries: int = 1

    # ====================================================================
    # ComfyUI 集群（LB 入口 8188）
    # 2026-08-05 收敛为 3 后端：本地 GPU0 (:8189) + pc01 (:8188) + pc02 (:8193)
    # GPU1/GPU2 不再跑独立 ComfyUI；GPU3 跑 FlashTalk/OpenTalking，不跑 ComfyUI。
    # AGENTS.md 硬规则: 禁止直连单卡 8189-8192，必须走 LB 入口 8188。
    # ====================================================================
    comfyui_image_hq: str = "http://192.168.71.127:8188"
    comfyui_image_fast: str = "http://192.168.71.127:8188"
    comfyui_video_a: str = "http://192.168.71.127:8188"
    comfyui_video_b: str = "http://192.168.71.127:8188"

    # 视频生成并发度上限（与视频 worker 数对齐；当前本地 GPU0 + pc01 + pc02）
    video_max_concurrency: int = 2

    # ====================================================================
    # TTS 配音（ToIV 项目 IndexTTS2 共用）
    # workstation:9200, GPU0 (cuda:0), systemd 托管（2026-07-27 变更）
    # 备注: root 路径返回 404 正常, 需查实际 API 路径
    # ====================================================================
    tts_backend: str = "indextts"  # 'indextts'（ToIV 共用默认）/ 'cosyvoice'（备选）/ 'edge'（回退）
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
    # ASR 字幕（已部署到 workstation GPU2）
    # 2026-08-05 修正：qwen3_asr 端点 (:9880) 当前未部署，subtitle_agent 分支里
    # 也没有 'qwen3_asr' 这个 case。默认改为 'ai_omni' 走 workstation :9210
    # faster-whisper large-v3；firered/whisper 作为回退。
    # ====================================================================
    asr_backend: str = "ai_omni"  # 'ai_omni' / 'firered' / 'whisper'
    firered_asr_endpoint: str = "http://192.168.71.127:8300/v1"  # 回退
    firered_asr_model: str = "FireRedTeam/FireRedASR-AED-L"
    firered_asr_timeout: float = 120.0
    # AI-Omni ASR（faster-whisper large-v3，Workstation GPU2，OpenAI 兼容端点）
    ai_omni_asr_endpoint: str = "http://192.168.71.127:9210"
    ai_omni_asr_timeout: float = 180.0

    # ====================================================================
    # 视频生成主后端：MiniMax H3（workstation :8195，GPU0 UNet 分片 + GPU2 CLIP/VAE）
    # xDiT/HunyuanVideo（:8288）已于 2026-08 下线，回退路径已移除（激进清理）。
    # ====================================================================
    # 视频后端：'h3'（主，MiniMax H3）/ 'comfyui'（回退 Wan 2.2）
    video_backend: str = "h3"

    # ====================================================================
    # M10 MiniMax H3 视频生成（workstation 独立 ComfyUI 实例 :8195，GPU0+GPU2 跨卡）
    # 2026-08-04 用户部署：33B H3-Omni-Transformer + Qwen3-VL-32B 文本编码器
    # 2K 直出 / 最长 15s / 原生立体声（联合音视频 latent，双 VAE 解码）
    # 官方 ComfyUI 模板特性：无负面提示词、无 CFG（BasicGuider 单条件蒸馏采样）
    # 模型权重在 NAS: toiv/comfyui-models/h3/（fl2va/ref2va INT8 + bf16）
    # ====================================================================
    h3_comfyui_url: str = "http://192.168.71.127:8195"
    h3_unet_name: str = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    h3_clip_name: str = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    h3_video_vae_name: str = "minimax_h3_video_vae_fp16.safetensors"
    h3_audio_vae_name: str = "minimax_h3_audio_vae_fp32.safetensors"
    h3_width: int = 768  # 9:16 竖屏短剧（节点默认 1344x768 横屏，需显式翻转）
    h3_height: int = 1344
    h3_steps: int = 20  # 官方模板 BasicScheduler steps=20
    h3_sampler: str = "res_multistep"  # 官方模板采样器
    h3_scheduler: str = "simple"
    h3_result_timeout: float = 1800.0  # 33B 模型单场景 5-15 分钟
    # --- MiniMax-H3 Turbo LoRA（可选加速，默认关闭）---
    # 2026-08-08 部署：larryvrh/drbaph Turbo LoRA 已下载到 NAS h3/loras/
    # 开启后 20 步 → 4-8 步，约 5× 采样加速；对高质量短剧属于实验性可选项
    h3_turbo_enabled: bool = False  # 默认关闭；P3 预览路径按请求打开，成片保持原生 20 步
    # 产品默认已在 :8195 的 MiniMaxH3TurboLoRA；官方 minimax_h3_fl2v / lightx2v 仅当已是该默认时使用
    h3_turbo_lora_name: str = "minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors"
    h3_turbo_steps: int = 6  # 全局 Turbo 开关（非预览）回退步数；预览走 fl2va/ref2va 分档
    h3_turbo_fl2va_steps: int = 8  # P3 预览 FL2VA ~8 步
    h3_turbo_ref2va_steps: int = 4  # P3 预览 Ref2VA ~4 步
    h3_turbo_strength: float = 1.0  # 模糊拖影→1.05-1.2；过锐噪点→0.8-0.95
    h3_turbo_low_vram: bool = False  # 爆显存时改为 True（合并权重，画质略软）
    # NSFW 预览可用盘上 10Eros turbo；SFW 永不加载 10Eros
    h3_nsfw_turbo_lora_name: str = "10Eros_Max_h3_TURBO_ref2va.safetensors"

    # --- M20 长视频分块续写（PoC，默认关闭）---
    # 技术路线 A：H3 I2V 帧链续写 —— chunk i+1 首帧 = chunk i 末帧（ffmpeg 抽取），
    # 逐块复用角色参考图 + 画风锚定保持跨块一致性，最后 ffmpeg concat 拼接成长视频。
    # 默认关闭：PoC 验证接缝/角色漂移前不影响现有高质量短剧主流程。
    long_video_enabled: bool = False
    long_video_max_chunks: int = 4  # PoC 上限 2-4 块（每块 ≤14s，H3 训练上限）
    long_video_chunk_seconds: int = 5  # 单块时长（秒）；PoC 用 5s 控时，生产可提至 14
    # 续写帧上传 ComfyUI input 目录的文件名前缀（overwrite=true 避免堆积）
    long_video_frame_prefix: str = "longvideo_chain"

    # --- H3 ref2va（参考图生成，跨分镜角色一致性）---
    # VideoRequest.reference_images 非空时触发：分镜关键帧作第 1 张参考图（构图），
    # 角色资产库三视图参考图随后（外观锁定），共用 CLIP/双VAE/采样链配置
    h3_ref_unet_name: str = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    h3_nsfw_unet_name: str = "10Eros_Max_h3_fl2va_beta2_pruned_int8_convrot.safetensors"
    h3_nsfw_ref_unet_name: str = "10Eros_Max_h3_ref2va_beta2_pruned_int8_convrot.safetensors"
    # 参考图缩放策略：'match'（默认，与画布对齐）；'max' 保真度更高但慢数倍（官方 tooltip）
    h3_ref_image_size: str = "match"
    # r2v 节点 ref_images 动态组上限（COMFY_AUTOGROW_V3 max=9，含分镜关键帧 1 席）
    h3_ref_max_images: int = 9

    # --- H3 多镜叙事联合生成（M11）---
    # 同集相邻场景合并为一次 H3 多镜推理（单 prompt 多 SHOT，运镜/光影/角色跨镜连续），
    # 再按帧边界 ffmpeg 切分回各场景视频；任一环节失败整组回退为逐场景生成
    h3_multishot_enabled: bool = True
    h3_multishot_max_scenes: int = 3  # 单组最多场景数
    # 单组总时长上限（秒）：H3 训练上限约 15s（362 帧@24fps），留 1s 余量
    h3_multishot_max_seconds: float = 14.0

    # --- M17 H3 全模态能力释放 ---
    # M17.1 原生 CUT 语法：多镜 prompt 采用官方 Context-IR 格式
    # （integrated_multimodal_description: [Shot 1] ... [Shot N] At MM:SS.mmm, the camera cuts to ...），
    # False 时回退 M11 旧版 "SHOT X:" 格式（保险丝）
    h3_native_cut_prompt_enabled: bool = True
    # P1 local Context-IR rewrite: spark LLM (qwen3.6-uncensored) rewrites the
    # prompt H3 actually receives; optional VLM retention when refs exist.
    # Fail-open to the original assembled prompt. Default on.
    h3_context_ir_rewrite_enabled: bool = True
    # M17.2 原生音频方向：按组内叙事节拍确定性生成 overall_soundscape /
    # non_diegetic_music 两字段注入 prompt，引导 H3 生成真实 BGM/环境音轨
    h3_audio_direction_enabled: bool = True
    # P2 / M17.3 FL2VA 末帧链（默认开）：同集顺序镜头
    # 1) 生成前把下一镜关键帧填入 last_frame_url 作 FL2VA 双锚定 bootstrap
    # 2) 上一镜解码末帧覆盖下一镜 last_frame_url（video_agent 顺序镜头实际消费）
    # 失败重试一次，再降级为首帧-only（不再走 Wan）
    h3_last_frame_chain_enabled: bool = True
    # M17.4 ref2va 音视频参考上限（节点 COMFY_AUTOGROW_V3 max=3/3；H3 全模态
    # 参考文件总预算 12，图片侧已占 9 席，音视频保守各 3 以内）
    h3_ref_max_videos: int = 3
    h3_ref_max_audios: int = 3

    # H3 原生音轨混音（H3 输出 mp4 自带立体声环境音/氛围声 AAC 32kHz，非人声）
    h3_native_audio_enabled: bool = True  # 保留 H3 原生音轨作环境音，与人声混音垫底
    h3_ambience_gain: float = 0.25  # 环境音线性增益（约 -12dB），垫于人声之下
    # M12.2 按对白密度动态调增益：人声/视频时长比 ≥0.85 用 dense 档（避免盖人声），
    # <0.4 用 sparse 档（留白多，提升氛围），中间档维持 h3_ambience_gain
    h3_dynamic_gain_enabled: bool = True
    h3_ambience_gain_dense: float = 0.15  # 对白密集档（约 -16dB）
    h3_ambience_gain_sparse: float = 0.40  # 对白稀疏档（约 -8dB）

    # ====================================================================
    # 图像生成（2026-08 架构：SDXL 经 ComfyUI-LB :8188 为唯一在线后端）
    # HunyuanImage（:8600）服务侧已损坏（No module named hyimage.pipelines）；
    # FLUX+PuLID（:8601）从未部署；两者代码路径已移除（激进清理）。
    # 画风由 style_anchor 按写实性自动选 SDXL checkpoint（majicMIX/animagineXL40）。
    # ====================================================================
    image_backend: str = "sdxl"

    # ====================================================================
    # LTX-2.5 视频（已退役；comfyui-ltx25 inactive，保留 URL 但不启用）
    # 2026-08-23 ToIV 彻底 disable；H3 为唯一主力视频引擎。
    # 与 .env.example 对齐：LTX_ENABLED=false，无 .env 时不得漂回 True。
    # ====================================================================
    ltx_enabled: bool = False
    ltx_comfyui_url: str = "http://192.168.71.127:8198"
    ltx_result_timeout: float = 600.0  # distilled 8 步远快于 H3 33B

    # ====================================================================
    # M21 统一提示词扩写（场景 IR → H3/LTX 双引擎编译器）
    # True：LLM（spark02）扩写 ShotSpec 后编译；False/LLM 失败 → 确定性模板
    # ====================================================================
    prompt_expander_enabled: bool = True

    # ====================================================================
    # 唇形同步 / 后处理（LatentSync/RealBasicVSR/RIFE/DeepFilterNet）
    # 对应服务（:8289/:8290/:8301）2026-08 均已下线，代码路径已移除（激进清理）。
    # 超分能力由 ToIV M6 fleet（workstation :8261/:8262/:8263）承担，见 AGENTS.md。
    # ====================================================================

    # 下载器配置路径（相对于项目根目录，env: DOWNLOADER_CONFIG_PATH）
    downloader_config_path: str = "config.json"

    # ====================================================================
    # NAS 模型库浏览 / 模型下载整合（2026-08-16，M27）
    # workstation: CIFS /mnt/toiv-nas/... ；Mac 另扫 lora_manifest destination_dir
    # 父目录（~/NAS/Windows/ComfyUI/ComfyUIModel/models）。不要把 SMB 密码写入仓库。
    # ====================================================================
    nas_model_roots: str = "/mnt/toiv-nas/Windows/ComfyUI/ComfyUIModel/models,/mnt/toiv-nas/toiv/comfyui-models"
    nas_library_cache_ttl: float = 60.0  # 模型库扫描缓存秒数
    model_file_extensions: str = ".safetensors,.pt,.pth,.ckpt,.bin,.onnx"
    # NSFW 文件名关键词（逗号分隔，小写子串匹配；配合 nsfw_exact_names 精确名单）
    nsfw_keywords: str = "nsfw,porn,xxx,hentai,r18,erotic,nude,urpm,lustify,bigasse,sexgod,footjob,10eros"
    nsfw_exact_names: str = ""  # 精确文件名（不含扩展名），逗号分隔
    # 模型下载
    civitai_api_base: str = "https://civitai.red/api"  # 与 Rust 端 default_civitai_host 一致
    hf_endpoint: str = "https://hf-mirror.com"
    download_chunk_size: int = 1024 * 1024  # 1MB
    download_timeout: float = 30.0  # 单次网络读超时（非整体）
    download_max_concurrency: int = 2
    # 下载落盘子目录白名单（防路径穿越，对齐 ComfyUI extra_model_paths）
    download_subdir_whitelist: str = "checkpoints,loras,vae,clip,clip_vision,controlnet,diffusion_models,text_encoders,upscale_models,embeddings,ipadapter,unet"
    # 应用设置持久化（NSFW 开关/PIN 等），相对 backend/ 目录
    app_settings_path: str = "data/app_settings.json"

    # ====================================================================
    # 剧本生成：联网搜索默认关闭（DuckDuckGo/Wiki 每轮可额外数秒～数十秒）
    # 请求级 ScriptRequest.web_search 或本开关任一为 True 才搜索
    # ====================================================================
    script_web_search_enabled: bool = False

    # ====================================================================
    # 内置 RAG 提示词优化
    # ====================================================================
    rag_optimize_enabled: bool = True
    rag_embed_model: str = "BAAI/bge-small-zh-v1.5"
    rag_top_k: int = 5

    # 后端服务
    backend_host: str = "127.0.0.1"
    backend_port: int = 8100
    cors_origins: str = "http://127.0.0.1:8080,http://localhost:8080,http://127.0.0.1:3501,http://localhost:3501,http://localhost:3508,http://localhost:3509,http://localhost:8085,http://localhost:5173,http://192.168.71.47:3501"

    # 运行时填充
    downloader_config: DownloaderConfig | None = None

    def load_downloader_config(self) -> DownloaderConfig:
        """读取仓库根目录 config.json（DOWNLOADER_CONFIG_PATH），实现配置共享。"""
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
