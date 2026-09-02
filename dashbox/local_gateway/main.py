"""DashBox 本地网关适配层（local gateway adapter）。

NewAPI/OpenAI 兼容协议契约面，把 DashBox CE 的全部模型调用转发到本地已部署服务：

| 能力 | 契约端点 | 本地目标 |
|------|----------|----------|
| 文本 LLM / VLM | POST /v1/chat/completions | spark02 qwen3.6-uncensored :8000/v1 |
| Embedding | POST /v1/embeddings | Qwen3-Embedding-4B :9302/v1 |
| 图像生成 | POST /v1/images/generations, /v1/images/edits | ComfyUI-LB SDXL :8188 |
| 视频生成 | POST/GET /v1/video/generations | MiniMax H3 :8195 / LTX-2.5 :8198 |
| TTS | POST /v1/audio/speech | IndexTTS-2 :9200 |
| 模型清单 | GET /v1/models | 聚合逻辑模型名 |

启动：uvicorn local_gateway.main:app --host 127.0.0.1 --port 8790
或：python -m local_gateway.main

真机核验（2026-08-15）：
- spark02 /v1/models 真实模型名 = qwen3.6-uncensored
- :9302 无 /v1/models 路由，但 /v1/embeddings 可用（服务端不校验 model 名）
- LTX-2.5 :8198 无 LTXVBaseSampler/LTXVLatentUpscale 节点；真实可用链路为
  CheckpointLoaderSimple + LTXAVTextEncoderLoader + EmptyLTXVLatentVideo + KSampler
  （sampler euler_ancestral_cfg_pp / scheduler simple 已核验存在）；
  权重真实文件名：ltx-2.5-22b-distilled-transformer-nvfp4.safetensors /
  gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors /
  ltx-2.5-video-vae-bf16.safetensors / ltx-2.5-audio-vae-bf16.safetensors
- LB :8188 majicMIX ckpt 存在；IPAdapter 真实文件名
  ip-adapter-plus-face_sdxl_vit-h.safetensors + CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors
- H3 :8195 权重名与模板一致；MiniMaxH3ImageToVideo first_frame/last_frame 均 optional；有参考图/视频/音频走 MiniMaxH3ReferenceToVideo（ref2va）；NSFW PIN 用 10Eros UNet，SFW 用 minimax_h3_*；2K 不是本地 H3-Regenerate
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any

from local_gateway.h3_context_ir import rewrite_h3_prompt as rewrite_h3_context_ir
from local_gateway.h3_video import (
    H3RepairUnavailable,
    apply_h3_repair_guide,
    collect_video_inputs,
    h3_resolution_scale,
    h3_unets,
    r2v_ref_images,
    request_inpaint_mask,
    request_nsfw,
    request_nsfw_variant,
    request_repair,
    request_repair_denoise,
    require_h3_add_guide,
    select_h3_mode,
)
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("local_gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# ---------------------------------------------------------------------------
# 配置（环境变量可覆盖）
# ---------------------------------------------------------------------------

LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://192.168.71.84:8000/v1").rstrip("/")
VLM_BASE_URL = os.getenv("LOCAL_VLM_BASE_URL", "http://192.168.71.82:8000/v1").rstrip("/")
VLM_MODEL_NAME = os.getenv("LOCAL_VLM_MODEL", "qwen3-vl-32b")
VLM_LOGICAL_MODELS = {
    "DC-freezone-vision-LLM",
    "DC-style-analyzer-LLM",
    "DC-video-identity-detector-LLM",
    "DC-video-prompt-optimizer-LLM",
}
EMBEDDING_BASE_URL = os.getenv("LOCAL_EMBEDDING_BASE_URL", "http://192.168.71.127:9302/v1").rstrip("/")
COMFYUI_LB_URL = os.getenv("LOCAL_COMFYUI_LB_URL", "http://192.168.71.127:8188").rstrip("/")
# LB 三后端直连清单（2026-08-17 固化）：参考图上传必须覆盖全部后端——LB /upload
# 轮询单实例而 /prompt 加权随机选实例，只传 LB 入口时约 2/3 概率 LoadImage 在
# 另一后端找不到文件（Invalid image file → 502，画布 R18 节点实测复现）。
COMFYUI_LB_BACKEND_URLS = os.getenv(
    "LOCAL_COMFYUI_LB_BACKEND_URLS",
    "http://192.168.71.127:8189,http://192.168.71.116:8188,http://192.168.71.114:8193",
)
H3_BASE_URL = os.getenv("LOCAL_H3_BASE_URL", "http://192.168.71.127:8195").rstrip("/")
LTX_BASE_URL = os.getenv("LOCAL_LTX_BASE_URL", "http://192.168.71.127:8198").rstrip("/")
# Krea2 专用实例（workstation GPU0 ComfyUI :8189 直连，不经 LB——TE 只在本地）
KREA2_BASE_URL = os.getenv("LOCAL_KREA2_BASE_URL", "http://192.168.71.127:8189").rstrip("/")
TTS_BASE_URL = os.getenv("LOCAL_TTS_BASE_URL", "http://192.168.71.127:9200").rstrip("/")
VIDEO_BACKEND_FORCE = os.getenv("LOCAL_VIDEO_BACKEND", "").strip().lower()  # "h3" / "ltx" 强制指定；不读 DashBox VIDEO_BACKEND
GATEWAY_HOST = os.getenv("LOCAL_GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("LOCAL_GATEWAY_PORT", "8790"))
PUBLIC_BASE_URL = os.getenv("LOCAL_GATEWAY_PUBLIC_BASE", f"http://{GATEWAY_HOST}:{GATEWAY_PORT}").rstrip("/")

CHAT_MODEL_NAME = os.getenv("LOCAL_CHAT_MODEL", "qwen3.6-uncensored")
EMBEDDING_MODEL_NAME = os.getenv("LOCAL_EMBEDDING_MODEL", "Qwen3-Embedding-4B")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TIMEOUT = float(os.getenv("LOCAL_GATEWAY_TIMEOUT", "300"))
IMAGE_POLL_TIMEOUT = float(os.getenv("LOCAL_GATEWAY_IMAGE_TIMEOUT", "300"))

# ---------------------------------------------------------------------------
# 逻辑模型清单（GET /v1/models）
# ---------------------------------------------------------------------------

LOGICAL_MODELS = [
    "DC-hermes-LLM",
    "DC-cognee-LLM",
    "DC-cognee-embedding",
    "DC-freezone-vision-LLM",
    "local-sdxl",
    "LingShan-G2",
    "LingShan-NB-2",
    "NanoBanana",
    "MiniMax-H3",
    "LTX-2.5",
    "happyhorse-1.0",
    "index-tts-2",
]

# ---------------------------------------------------------------------------
# SDXL / H3 / LTX 常量（真机核验值）
# ---------------------------------------------------------------------------

SDXL_CHECKPOINT = os.getenv("LOCAL_SDXL_CHECKPOINT", "majicMIX realistic 麦橘写实_v7.safetensors")
IPADAPTER_FILE = os.getenv("LOCAL_IPADAPTER_FILE", "ip-adapter-plus-face_sdxl_vit-h.safetensors")
IPADAPTER_CLIP_VISION = os.getenv("LOCAL_IPADAPTER_CLIP_VISION", "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors")

H3_UNET_NAME = os.getenv("LOCAL_H3_UNET", "minimax_h3_fl2va_pruned_int8_convrot.safetensors")
H3_REF_UNET_NAME = os.getenv("LOCAL_H3_REF_UNET", "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
H3_NSFW_UNET_NAME = os.getenv("LOCAL_H3_NSFW_UNET", "10Eros_Max_h3_fl2va_beta2_pruned_int8_convrot.safetensors")
H3_NSFW_REF_UNET_NAME = os.getenv(
    "LOCAL_H3_NSFW_REF_UNET", "10Eros_Max_h3_ref2va_beta2_pruned_int8_convrot.safetensors"
)
H3_CLIP_NAME = os.getenv("LOCAL_H3_CLIP", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
H3_VIDEO_VAE = os.getenv("LOCAL_H3_VIDEO_VAE", "minimax_h3_video_vae_fp16.safetensors")
H3_AUDIO_VAE = os.getenv("LOCAL_H3_AUDIO_VAE", "minimax_h3_audio_vae_fp32.safetensors")

LTX_TRANSFORMER_NAME = os.getenv("LOCAL_LTX_TRANSFORMER", "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors")
LTX_TEXT_ENCODER_NAME = os.getenv("LOCAL_LTX_TEXT_ENCODER", "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors")
LTX_VIDEO_VAE = os.getenv("LOCAL_LTX_VIDEO_VAE", "ltx-2.5-video-vae-bf16.safetensors")
LTX_AUDIO_VAE = os.getenv("LOCAL_LTX_AUDIO_VAE", "ltx-2.5-audio-vae-bf16.safetensors")
LTX_FPS = 25
LTX_SAMPLER = "euler_ancestral_cfg_pp"
LTX_SCHEDULER = "simple"
LTX_CFG = 1.0  # 蒸馏模型 CFG=1
LTX_STEPS = 8

# LTX 提交前必须核验的节点（真机 /object_info）
LTX_REQUIRED_NODES = [
    "CheckpointLoaderSimple", "LTXAVTextEncoderLoader", "VAELoader", "CLIPTextEncode",
    "EmptyLTXVLatentVideo", "KSampler", "VAEDecode", "LTXVSeparateAVLatent", "LTXVAudioVAEDecode",
    "LTXVEmptyLatentAudio", "LTXVConcatAVLatent",
    "CreateVideo", "SaveVideo", "LoadImage", "LTXVImgToVideo", "LTXVAddGuide",
]

DEFAULT_NEGATIVE_PROMPT = (
    "lowres, bad anatomy, bad hands, missing fingers, extra fingers, deformed, "
    "worst quality, low quality, watermark, text, signature"
)

# ---------------------------------------------------------------------------
# SDXL 文生图工作流模板（参照 AICG storyboard_agent.WORKFLOW_TEMPLATE）
# ---------------------------------------------------------------------------

SDXL_WORKFLOW_TEMPLATE: dict[str, Any] = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": SDXL_CHECKPOINT}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
    "5": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0, "steps": 25, "cfg": 7.0,
            "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
        },
    },
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "dc_image"}},
}

# ---------------------------------------------------------------------------
# Flux 完整 checkpoint 工作流（flux1-dev-fp8 系：单文件含 diffusion+clip_l+t5+vae，
# CheckpointLoaderSimple 可加载；采样链按 Flux dev 范式 cfg=1.0 + FluxGuidance）。
# 与 SDXL 链的差别：EmptySD3LatentImage 潜空间 + FluxGuidance 3.5 + euler/simple。
# ---------------------------------------------------------------------------

FLUX_WORKFLOW_TEMPLATE: dict[str, Any] = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev-fp8.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    # Flux 的引导强度走 conditioning（3.5 = dev 官方推荐），cfg 恒 1.0
    "4": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["2", 0], "guidance": 3.5}},
    "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "6": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0, "steps": 20, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["1", 0], "positive": ["4", 0], "negative": ["3", 0], "latent_image": ["5", 0],
        },
    },
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "dc_flux"}},
}

# ---------------------------------------------------------------------------
# Krea2 工作流模板（2026-08-18 真机核验，官方 image_krea2_turbo_t2i 模板链路：
# UNETLoader + CLIPLoader(type=krea2, Qwen3-VL-4B 编码器) + VAELoader(qwen_image_vae,
# latent_format=Wan21) + KSampler(8 步/cfg1/euler/simple, turbo 蒸馏) +
# ConditioningZeroOut 负条件。注意不是 gemma3——sd.py 明确 Krea2 走 Qwen3-VL-4B。
# ---------------------------------------------------------------------------

KREA2_UNET_NAME = os.getenv("LOCAL_KREA2_UNET", "krea2TurboFP8_krea2TURBO.safetensors")
KREA2_TEXT_ENCODER = os.getenv("LOCAL_KREA2_TEXT_ENCODER", "qwen3vl_4b_fp8_scaled.safetensors")
KREA2_VAE = os.getenv("LOCAL_KREA2_VAE", "qwen_image_vae.safetensors")

KREA2_WORKFLOW_TEMPLATE: dict[str, Any] = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": KREA2_UNET_NAME, "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": KREA2_TEXT_ENCODER, "type": "krea2", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": KREA2_VAE}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
    "6": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "7": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
        },
    },
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "dc_krea2"}},
}

# IPAdapter 参考图锚定节点（参照 storyboard_agent.IPADAPTER_ANCHOR_NODES）
IPADAPTER_ANCHOR_NODES: dict[str, Any] = {
    "8": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": IPADAPTER_FILE}},
    "9": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": IPADAPTER_CLIP_VISION}},
    "11": {"class_type": "LoadImage", "inputs": {"image": ""}},
    "12": {
        "class_type": "IPAdapterAdvanced",
        "inputs": {
            "model": ["1", 0], "ipadapter": ["8", 0], "clip_vision": ["9", 0], "image": ["11", 0],
            "weight": 0.6, "weight_type": "linear", "combine_embeds": "concat",
            "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only",
        },
    },
}

# ---------------------------------------------------------------------------
# H3 fl2va 工作流模板（参照 AICG video_agent.WORKFLOW_TEMPLATE_H3）
# ---------------------------------------------------------------------------

H3_WORKFLOW_TEMPLATE: dict[str, Any] = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": H3_UNET_NAME, "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": H3_CLIP_NAME, "type": "minimax", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": H3_VIDEO_VAE}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": H3_AUDIO_VAE}},
    "20": {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": "", "width": 768, "height": 1344, "length": 124},
    },
    "30": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
    "31": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
    "32": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": 20, "denoise": 1.0}},
    "33": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["20", 0]}},
    "34": {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {"noise": ["30", 0], "guider": ["33", 0], "sampler": ["31", 0], "sigmas": ["32", 0], "latent_image": ["20", 1]},
    },
    "40": {"class_type": "VAEDecode", "inputs": {"samples": ["34", 0], "vae": ["3", 0]}},
    "41": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["34", 0], "vae": ["4", 0]}},
    "50": {"class_type": "CreateVideo", "inputs": {"images": ["40", 0], "audio": ["41", 0], "fps": 24, "bit_depth": 8}},
    "60": {"class_type": "SaveVideo", "inputs": {"video": ["50", 0], "filename_prefix": "dc_video", "format": "auto", "codec": "auto"}},
}


# MiniMax H3 ref2va 工作流（对齐 platform VideoAgent.WORKFLOW_TEMPLATE_H3_R2V）
H3_R2V_WORKFLOW_TEMPLATE: dict[str, Any] = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": H3_REF_UNET_NAME, "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": H3_CLIP_NAME, "type": "minimax", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": H3_VIDEO_VAE}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": H3_AUDIO_VAE}},
    "20": {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0],
            "prompt": "", "width": 768, "height": 1344, "length": 124,
            "ref_image_size": "match",
            "ref_images": {},
        },
    },
    "30": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
    "31": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
    "32": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": 20, "denoise": 1.0}},
    "33": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["20", 0]}},
    "34": {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {"noise": ["30", 0], "guider": ["33", 0], "sampler": ["31", 0], "sigmas": ["32", 0], "latent_image": ["20", 1]},
    },
    "40": {"class_type": "VAEDecode", "inputs": {"samples": ["34", 0], "vae": ["3", 0]}},
    "41": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["34", 0], "vae": ["4", 0]}},
    "50": {"class_type": "CreateVideo", "inputs": {"images": ["40", 0], "audio": ["41", 0], "fps": 24, "bit_depth": 8}},
    "60": {"class_type": "SaveVideo", "inputs": {"video": ["50", 0], "filename_prefix": "dc_video", "format": "auto", "codec": "auto"}},
}

# ---------------------------------------------------------------------------
# LTX-2.5 工作流模板（真机核验后的单阶段链路；T2V，I2V/FLF2V 动态注入）
# ---------------------------------------------------------------------------

LTX_WORKFLOW_TEMPLATE: dict[str, Any] = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": LTX_TRANSFORMER_NAME}},
    "2": {"class_type": "LTXAVTextEncoderLoader", "inputs": {"text_encoder": LTX_TEXT_ENCODER_NAME, "ckpt_name": LTX_TRANSFORMER_NAME, "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_VIDEO_VAE}},
    "4": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_AUDIO_VAE}},
    "10": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "11": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "20": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": 768, "height": 512, "length": 121, "batch_size": 1}},
    "21": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": 121, "frame_rate": LTX_FPS, "batch_size": 1, "audio_vae": ["4", 0]}},
    "22": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["20", 0], "audio_latent": ["21", 0]}},
    "30": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0, "steps": LTX_STEPS, "cfg": LTX_CFG,
            "sampler_name": LTX_SAMPLER, "scheduler": LTX_SCHEDULER, "denoise": 1.0,
            "model": ["1", 0], "positive": ["10", 0], "negative": ["11", 0], "latent_image": ["22", 0],
        },
    },
    "45": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["30", 0]}},
    "50": {"class_type": "VAEDecode", "inputs": {"samples": ["45", 0], "vae": ["3", 0]}},
    "51": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["45", 1], "audio_vae": ["4", 0]}},
    "60": {"class_type": "CreateVideo", "inputs": {"images": ["50", 0], "audio": ["51", 0], "fps": LTX_FPS}},
    "70": {"class_type": "SaveVideo", "inputs": {"video": ["60", 0], "filename_prefix": "dc_video", "format": "auto", "codec": "auto"}},
}


# ---------------------------------------------------------------------------
# 纯函数：帧数/尺寸/工作流构建
# ---------------------------------------------------------------------------

def _snap_ltx_frames(n: int | float) -> int:
    """LTX-2.5 帧数对齐 %8==1 网格（下限 9 帧）。"""
    n = max(9, int(n))
    return n - ((n - 1) % 8)


def _snap_h3_frames(duration_seconds: int | float) -> int:
    """H3 帧数：max(5, round(sec*24)) + (5 - n % 17) % 17（17k+5 网格，24fps）。"""
    n = max(5, round(duration_seconds * 24))
    return n + (5 - n % 17) % 17


def _snap_dim(d: int | float, multiple: int = 32) -> int:
    return max(multiple, (int(d) // multiple) * multiple)


_RATIO_BASE_SIZES = {
    "9:16": (832, 1216),
    "16:9": (1216, 832),
    "1:1": (1024, 1024),
    "4:3": (1152, 864),
    "3:4": (864, 1152),
    "2:3": (832, 1248),
    "3:2": (1248, 832),
}
_RESOLUTION_SCALE = {"480p": 0.5, "720p": 0.75, "1080p": 1.0, "2k": 1.5, "4k": 2.0}


def _derive_image_size(body: dict[str, Any], default: tuple[int, int] = (832, 1216)) -> tuple[int, int]:
    """从 OpenAI images 请求推导尺寸：width/height > size("WxH") > metadata.resolution/ratio > 默认。"""
    width = body.get("width")
    height = body.get("height")
    if width and height:
        return int(width), int(height)
    size = str(body.get("size") or "")
    if "x" in size:
        try:
            w, h = size.lower().split("x", 1)
            return int(w), int(h)
        except ValueError:
            pass
    metadata = body.get("metadata") or {}
    ratio = str(metadata.get("ratio") or "").strip()
    base = _RATIO_BASE_SIZES.get(ratio, default)
    resolution = str(metadata.get("resolution") or "").strip().lower()
    scale = _RESOLUTION_SCALE.get(resolution, 1.0)
    if scale != 1.0:
        return max(64, int(base[0] * scale) // 8 * 8), max(64, int(base[1] * scale) // 8 * 8)
    return base


def _derive_video_size(body: dict[str, Any], backend: str) -> tuple[int, int]:
    """视频尺寸（32 对齐）。H3 默认 768x1344 竖屏；LTX 默认 768x512。"""
    width = body.get("width")
    height = body.get("height")
    if width and height:
        return _snap_dim(width), _snap_dim(height)
    metadata = body.get("metadata") or {}
    ratio = str(body.get("ratio") or metadata.get("ratio") or "").strip()
    if backend == "h3":
        base = {"9:16": (768, 1344), "16:9": (1344, 768), "1:1": (1024, 1024)}.get(ratio, (768, 1344))
    else:
        base = {"9:16": (576, 1024), "16:9": (1024, 576), "1:1": (768, 768)}.get(ratio, (768, 512))
    resolution = str(body.get("resolution") or metadata.get("resolution") or "").strip().lower()
    scale = _RESOLUTION_SCALE.get(resolution, 1.0)
    if backend == "h3":
        scale = h3_resolution_scale(resolution, scale)
    return _snap_dim(base[0] * scale), _snap_dim(base[1] * scale)


def _build_sdxl_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
    checkpoint: str = "",
) -> dict[str, Any]:
    wf = json.loads(json.dumps(SDXL_WORKFLOW_TEMPLATE))
    if checkpoint:
        wf["1"]["inputs"]["ckpt_name"] = checkpoint
    wf["2"]["inputs"]["text"] = prompt
    wf["3"]["inputs"]["text"] = negative_prompt
    wf["4"]["inputs"]["width"] = width
    wf["4"]["inputs"]["height"] = height
    wf["5"]["inputs"]["seed"] = seed
    wf["7"]["inputs"]["filename_prefix"] = filename_prefix
    return wf


def _build_flux_workflow(
    *,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
    checkpoint: str = "",
) -> dict[str, Any]:
    wf = json.loads(json.dumps(FLUX_WORKFLOW_TEMPLATE))
    if checkpoint:
        wf["1"]["inputs"]["ckpt_name"] = checkpoint
    wf["2"]["inputs"]["text"] = prompt
    # Flux cfg=1.0 下 negative 不参与引导，仅占连线位
    wf["3"]["inputs"]["text"] = negative_prompt
    wf["5"]["inputs"]["width"] = width
    wf["5"]["inputs"]["height"] = height
    wf["6"]["inputs"]["seed"] = seed
    wf["8"]["inputs"]["filename_prefix"] = filename_prefix
    return wf


def _build_krea2_workflow(
    *,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    filename_prefix: str,
    unet_name: str = "",
) -> dict[str, Any]:
    wf = json.loads(json.dumps(KREA2_WORKFLOW_TEMPLATE))
    if unet_name:
        wf["1"]["inputs"]["unet_name"] = unet_name
    wf["4"]["inputs"]["text"] = prompt
    wf["6"]["inputs"]["width"] = width
    wf["6"]["inputs"]["height"] = height
    wf["7"]["inputs"]["seed"] = seed
    wf["9"]["inputs"]["filename_prefix"] = filename_prefix
    return wf


def _inject_ipadapter(workflow: dict[str, Any], image_name: str) -> dict[str, Any]:
    """注入 IPAdapter 参考图锚定节点，KSampler model 重定向到节点 12。"""
    nodes = json.loads(json.dumps(IPADAPTER_ANCHOR_NODES))
    nodes["8"]["inputs"]["ipadapter_file"] = IPADAPTER_FILE
    nodes["9"]["inputs"]["clip_name"] = IPADAPTER_CLIP_VISION
    nodes["11"]["inputs"]["image"] = image_name
    workflow.update(nodes)
    workflow["5"]["inputs"]["model"] = ["12", 0]
    return workflow


def _build_h3_workflow(
    *,
    prompt: str,
    width: int,
    height: int,
    num_frames: int,
    seed: int,
    filename_prefix: str,
    first_image_name: str | None = None,
    last_image_name: str | None = None,
    unet_name: str | None = None,
) -> dict[str, Any]:
    wf = json.loads(json.dumps(H3_WORKFLOW_TEMPLATE))
    wf["1"]["inputs"]["unet_name"] = unet_name or H3_UNET_NAME
    wf["2"]["inputs"]["clip_name"] = H3_CLIP_NAME
    wf["3"]["inputs"]["vae_name"] = H3_VIDEO_VAE
    wf["4"]["inputs"]["vae_name"] = H3_AUDIO_VAE
    wf["20"]["inputs"]["prompt"] = prompt
    wf["20"]["inputs"]["width"] = width
    wf["20"]["inputs"]["height"] = height
    wf["20"]["inputs"]["length"] = num_frames
    wf["30"]["inputs"]["noise_seed"] = seed
    wf["60"]["inputs"]["filename_prefix"] = filename_prefix
    if first_image_name:
        wf["10"] = {"class_type": "LoadImage", "inputs": {"image": first_image_name}}
        wf["20"]["inputs"]["first_frame"] = ["10", 0]
    if last_image_name:
        wf["11"] = {"class_type": "LoadImage", "inputs": {"image": last_image_name}}
        wf["20"]["inputs"]["last_frame"] = ["11", 0]
    return wf



def _build_h3_r2v_workflow(
    *,
    prompt: str,
    width: int,
    height: int,
    num_frames: int,
    seed: int,
    filename_prefix: str,
    ref_image_names: list[str],
    ref_video_names: list[str] | None = None,
    ref_audio_names: list[str] | None = None,
    unet_name: str | None = None,
) -> dict[str, Any]:
    """MiniMaxH3ReferenceToVideo：ref_images 为 COMFY_AUTOGROW_V3 嵌套 dict。"""
    wf = json.loads(json.dumps(H3_R2V_WORKFLOW_TEMPLATE))
    wf["1"]["inputs"]["unet_name"] = unet_name or H3_REF_UNET_NAME
    wf["2"]["inputs"]["clip_name"] = H3_CLIP_NAME
    wf["3"]["inputs"]["vae_name"] = H3_VIDEO_VAE
    wf["4"]["inputs"]["vae_name"] = H3_AUDIO_VAE
    wf["20"]["inputs"]["prompt"] = prompt
    wf["20"]["inputs"]["width"] = width
    wf["20"]["inputs"]["height"] = height
    wf["20"]["inputs"]["length"] = num_frames
    wf["30"]["inputs"]["noise_seed"] = seed
    wf["60"]["inputs"]["filename_prefix"] = filename_prefix
    ref_group: dict[str, Any] = {}
    for idx, name in enumerate(ref_image_names):
        node_id = str(10 + idx)
        wf[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        ref_group[f"ref_image_{idx}"] = [node_id, 0]
    wf["20"]["inputs"]["ref_images"] = ref_group
    node_inputs = wf["20"]["inputs"]
    for idx, name in enumerate(ref_video_names or []):
        load_id, comp_id = f"7{idx}", f"8{idx}"
        wf[load_id] = {"class_type": "LoadVideo", "inputs": {"file": name}}
        wf[comp_id] = {"class_type": "GetVideoComponents", "inputs": {"video": [load_id, 0]}}
        node_inputs.setdefault("ref_videos", {})[f"ref_video_{idx}"] = [comp_id, 0]
        node_inputs.setdefault("ref_video_audios", {})[f"ref_video_audio_{idx}"] = [comp_id, 1]
    for idx, name in enumerate(ref_audio_names or []):
        load_id = f"9{idx}"
        wf[load_id] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        node_inputs.setdefault("ref_audios", {})[f"ref_audio_{idx}"] = [load_id, 0]
    return wf


def _build_ltx_workflow(
    *,
    mode: str,  # t2v / i2v / flf2v
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    num_frames: int,
    seed: int,
    filename_prefix: str,
    first_image_name: str | None = None,
    last_image_name: str | None = None,
) -> dict[str, Any]:
    wf = json.loads(json.dumps(LTX_WORKFLOW_TEMPLATE))
    wf["1"]["inputs"]["ckpt_name"] = LTX_TRANSFORMER_NAME
    wf["2"]["inputs"]["text_encoder"] = LTX_TEXT_ENCODER_NAME
    wf["2"]["inputs"]["ckpt_name"] = LTX_TRANSFORMER_NAME
    wf["3"]["inputs"]["vae_name"] = LTX_VIDEO_VAE
    wf["4"]["inputs"]["vae_name"] = LTX_AUDIO_VAE
    wf["10"]["inputs"]["text"] = prompt
    wf["11"]["inputs"]["text"] = negative_prompt
    wf["20"]["inputs"]["width"] = _snap_dim(width)
    wf["20"]["inputs"]["height"] = _snap_dim(height)
    wf["20"]["inputs"]["length"] = num_frames
    wf["21"]["inputs"]["frames_number"] = num_frames
    wf["30"]["inputs"]["seed"] = seed
    wf["70"]["inputs"]["filename_prefix"] = filename_prefix

    if mode in ("i2v", "flf2v") and first_image_name:
        # 首帧图像条件：LTXVImgToVideo 输出接管 KSampler positive/negative/latent
        wf["80"] = {"class_type": "LoadImage", "inputs": {"image": first_image_name}}
        wf["81"] = {
            "class_type": "LTXVImgToVideo",
            "inputs": {
                "positive": ["10", 0], "negative": ["11", 0], "vae": ["3", 0],
                "image": ["80", 0],
                "width": _snap_dim(width), "height": _snap_dim(height), "length": num_frames,
            },
        }
        pos_out, neg_out, lat_out = ["81", 0], ["81", 1], ["81", 2]
        if mode == "flf2v" and last_image_name:
            # 末帧锚定：LTXVAddGuide frame_idx=length-1（真机 LTXVImgToVideo 无 last_image 输入）
            wf["82"] = {"class_type": "LoadImage", "inputs": {"image": last_image_name}}
            wf["83"] = {
                "class_type": "LTXVAddGuide",
                "inputs": {
                    "positive": pos_out, "negative": neg_out, "latent": lat_out,
                    "vae": ["3", 0], "image": ["82", 0], "frame_idx": num_frames - 1,
                },
            }
            pos_out, neg_out, lat_out = ["83", 0], ["83", 1], ["83", 2]
        stage = wf["30"]["inputs"]
        stage["positive"] = pos_out
        stage["negative"] = neg_out
        # 视频侧 latent（含图像条件）替换 concat 的视频输入，音频侧仍由节点 21 提供
        wf["22"]["inputs"]["video_latent"] = lat_out
    return wf


def _looks_like_audio(data: bytes) -> bool:
    """校验音频魔数：WAV(RIFF....WAVE) / MP3(ID3 或 0xFFEx 帧同步)。"""
    if len(data) < 12:
        return False
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return True
    return False


# ---------------------------------------------------------------------------
# HTTP 客户端工厂（trust_env=False 防系统代理拦截内网；测试中可 monkeypatch）
# ---------------------------------------------------------------------------

def _http(timeout: float | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        trust_env=False,
        timeout=httpx.Timeout(timeout or DEFAULT_TIMEOUT, connect=10.0),
        follow_redirects=True,
    )


def _log(capability: str, model: str, start: float, status: str, error: str = "") -> None:
    logger.info(
        "capability=%s model=%s elapsed_ms=%d status=%s error=%s",
        capability, model, int((time.time() - start) * 1000), status, error or "-",
    )


def _error_response(message: str, status_code: int = 500, err_type: str = "server_error") -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"message": message, "type": err_type}})


# ---------------------------------------------------------------------------
# ComfyUI 通用辅助
# ---------------------------------------------------------------------------

async def _upload_image_to_comfyui(
    client: httpx.AsyncClient,
    base_url: str,
    image_url: str,
    prefix: str = "dc",
    *,
    replicate_to_lb: bool = True,
) -> str:
    """下载 HTTP URL 图片并上传到 ComfyUI input 目录，返回文件名。

    配置 LOCAL_COMFYUI_LB_BACKEND_URLS（逗号分隔直连后端清单）时以同一文件名
    复制到全部后端（LB /upload 轮询单实例而 /prompt 按负载选实例，避免 LoadImage
    跨后端找不到文件）；部分后端失败仍继续，全部失败抛错。
    H3 专用实例必须 replicate_to_lb=False，只传到 :8195。
    """
    img_resp = await client.get(image_url)
    if img_resp.status_code != 200:
        raise RuntimeError(f"下载参考图失败: {image_url} status={img_resp.status_code}")
    img_bytes = img_resp.content
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}.png"

    backends = [u.strip().rstrip("/") for u in COMFYUI_LB_BACKEND_URLS.split(",") if u.strip()]
    targets = (backends or [base_url]) if replicate_to_lb else [base_url]
    ok = 0
    last_err = ""
    for target in targets:
        try:
            up = await client.post(
                f"{target}/upload/image",
                files={"image": (filename, img_bytes, "image/png")},
                data={"type": "input", "overwrite": "true"},
            )
            if up.status_code == 200:
                ok += 1
            else:
                last_err = f"{target} status={up.status_code}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{target} {e}"
    if ok == 0:
        raise RuntimeError(f"参考图上传 ComfyUI 全部失败: {last_err}")
    return filename



async def _upload_media_to_comfyui(
    client: httpx.AsyncClient,
    base_url: str,
    media_url: str,
    prefix: str,
    fallback_suffix: str,
) -> str:
    """下载任意媒体并上传到单一 ComfyUI 实例 input（H3 :8195 无独立 /upload/audio）。"""
    resp = await client.get(media_url)
    if resp.status_code != 200 or not resp.content:
        raise RuntimeError(f"下载参考媒体失败: {media_url} status={resp.status_code}")
    url_path = media_url.split("?", 1)[0].rstrip("/")
    ext = ""
    if "." in url_path.rsplit("/", 1)[-1]:
        ext = "." + url_path.rsplit(".", 1)[-1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".mp3", ".wav", ".m4a", ".flac"}:
        ext = fallback_suffix if fallback_suffix.startswith(".") else f".{fallback_suffix}"
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"
    up = await client.post(
        f"{base_url}/upload/image",
        files={"image": (filename, resp.content, "application/octet-stream")},
        data={"type": "input", "overwrite": "true"},
    )
    if up.status_code != 200:
        raise RuntimeError(f"参考媒体上传 ComfyUI 失败: {base_url} status={up.status_code}")
    return filename


async def _submit_comfyui(client: httpx.AsyncClient, base_url: str, workflow: dict[str, Any]) -> str:
    resp = await client.post(f"{base_url}/prompt", json={"prompt": workflow})
    if resp.status_code != 200:
        raise RuntimeError(f"ComfyUI /prompt 拒绝: status={resp.status_code} body={resp.content[:400]!r}")
    result = resp.json()
    prompt_id = result.get("prompt_id", "")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI 未返回 prompt_id: {result}")
    return prompt_id


# ---------------------------------------------------------------------------
# 生成前预检：权重文件名核验（/object_info 选项清单，TTL 缓存，fail-open）
# ---------------------------------------------------------------------------

# loader 节点 class_type → 携带权重文件名的 input 字段（与 novelvideo.model_library 同步维护）
LOADER_FILE_FIELDS: dict[str, tuple[str, ...]] = {
    "CheckpointLoaderSimple": ("ckpt_name",),
    "LoraLoader": ("lora_name",),
    "LoraLoaderModelOnly": ("lora_name",),
    "VAELoader": ("vae_name",),
    "LTXVAudioVAELoader": ("vae_name",),
    "CLIPLoader": ("clip_name",),
    "DualCLIPLoader": ("clip_name",),
    "CLIPVisionLoader": ("clip_name",),
    "UNETLoader": ("unet_name",),
    "LTXAVTextEncoderLoader": ("text_encoder", "ckpt_name"),
    "IPAdapterModelLoader": ("ipadapter_file",),
    "UpscaleModelLoader": ("model_name",),
    "LatentUpscaleModelLoader": ("model_name",),
    "ControlNetLoader": ("control_net_name",),
}

_OBJECT_INFO_TTL = 60.0
_object_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def _object_info(client: httpx.AsyncClient, base_url: str) -> dict[str, Any] | None:
    """拉取并缓存 /object_info；不可达返回 None（预检 fail-open，不阻断生成）。"""
    now = time.time()
    cached = _object_info_cache.get(base_url)
    if cached and now - cached[0] < _OBJECT_INFO_TTL:
        return cached[1]
    try:
        resp = await client.get(f"{base_url}/object_info", timeout=10.0)
        if resp.status_code != 200:
            logger.warning("预检: %s /object_info status=%s（跳过权重核验）", base_url, resp.status_code)
            return None
        info = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("预检: %s /object_info 不可达: %s（跳过权重核验）", base_url, e)
        return None
    _object_info_cache[base_url] = (now, info)
    return info


def _node_file_choices(node_info: dict[str, Any], field: str) -> set[str]:
    """从 object_info 节点定义提取某 input 字段的候选文件名清单。"""
    inputs = node_info.get("input") or {}
    for section in ("required", "optional"):
        spec = inputs.get(section, {}).get(field)
        if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], (list, tuple)):
            return {str(x) for x in spec[0]}
    return set()


async def _preflight_workflow(client: httpx.AsyncClient, base_url: str, workflow: dict[str, Any]) -> list[str]:
    """核验 workflow 引用的权重文件名在 ComfyUI 实例可用；返回缺失明细（空=通过）。

    /object_info 不可达或字段无选项清单时 fail-open（不误报），只在有清单且不命中时报缺。
    """
    info = await _object_info(client, base_url)
    if info is None:
        return []
    missing: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        fields = LOADER_FILE_FIELDS.get(class_type)
        if not fields:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        node_info = info.get(class_type) or {}
        for field in fields:
            value = inputs.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            choices = _node_file_choices(node_info, field)
            if choices and value.strip() not in choices:
                missing.append(f"节点{node_id}({class_type}.{field}): {value.strip()}")
    return missing


def _extract_output_media(outputs: dict[str, Any], base_url: str) -> tuple[str, str] | None:
    """从 ComfyUI outputs 提取首个媒体文件 URL，返回 (url, kind)。"""
    for node_output in outputs.values():
        for key in ("videos", "gifs", "images"):
            items = node_output.get(key)
            if items:
                info = items[0]
                filename = info["filename"]
                subfolder = info.get("subfolder", "")
                img_type = info.get("type", "output")
                return (
                    f"{base_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}",
                    key,
                )
    return None


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(title="DashBox Local Gateway Adapter", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 视频任务表（内存）
VIDEO_TASKS: dict[str, dict[str, Any]] = {}
# LTX 节点核验缓存（True=通过；字符串=错误信息）
_ltx_nodes_check: bool | str = False


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    start = time.time()
    data = [
        {"id": name, "object": "model", "created": int(time.time()), "owned_by": "local-gateway"}
        for name in LOGICAL_MODELS
    ]
    _log("models", "-", start, "ok")
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    start = time.time()
    try:
        body = await request.json()
    except Exception:
        return _error_response("invalid JSON body", 400, "invalid_request_error")
    model = str(body.get("model") or "")
    use_vlm = model in VLM_LOGICAL_MODELS or "vision" in model.lower()
    body["model"] = VLM_MODEL_NAME if use_vlm else CHAT_MODEL_NAME
    url = f"{(VLM_BASE_URL if use_vlm else LLM_BASE_URL)}/chat/completions"

    if body.get("stream"):
        _log("chat", model, start, "streaming")

        async def _gen() -> AsyncIterator[bytes]:
            client = _http()
            try:
                req = client.build_request("POST", url, json=body)
                resp = await client.send(req, stream=True)
                if resp.status_code != 200:
                    content = await resp.aread()
                    yield content
                    return
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await client.aclose()

        return StreamingResponse(_gen(), media_type="text/event-stream")

    try:
        async with _http() as client:
            resp = await client.post(url, json=body)
    except Exception as e:  # noqa: BLE001
        _log("chat", model, start, "error", str(e))
        return _error_response(f"LLM upstream unreachable: {e}", 502)
    _log("chat", model, start, "ok" if resp.status_code == 200 else f"upstream_{resp.status_code}")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
    start = time.time()
    try:
        body = await request.json()
    except Exception:
        return _error_response("invalid JSON body", 400, "invalid_request_error")
    model = str(body.get("model") or "")
    body["model"] = EMBEDDING_MODEL_NAME
    try:
        async with _http() as client:
            resp = await client.post(f"{EMBEDDING_BASE_URL}/embeddings", json=body)
    except Exception as e:  # noqa: BLE001
        _log("embedding", model, start, "error", str(e))
        return _error_response(f"embedding upstream unreachable: {e}", 502)
    _log("embedding", model, start, "ok" if resp.status_code == 200 else f"upstream_{resp.status_code}")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


async def _generate_image(body: dict[str, Any], reference_url: str = "") -> dict[str, Any] | JSONResponse:
    """SDXL 文生图/参考图编辑共用内核，返回 OpenAI images 响应 dict 或错误响应。"""
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return _error_response("prompt is required", 400, "invalid_request_error")
    negative = str(body.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT)
    width, height = _derive_image_size(body)
    seed = random.randint(0, 2**32 - 1)
    prefix = f"dc_img_{uuid.uuid4().hex[:8]}"

    workflow_kind = str(body.get("workflow") or "sdxl").strip().lower()
    if workflow_kind == "krea2":
        # Krea2 turbo（UNETLoader + Qwen3-VL-4B + qwen_image_vae）：cfg=1
        # 蒸馏链，negative 不参与（ConditioningZeroOut 占位）
        workflow = _build_krea2_workflow(
            prompt=prompt,
            width=width, height=height, seed=seed, filename_prefix=prefix,
            unet_name=str(body.get("checkpoint") or ""),
        )
    elif workflow_kind == "flux":
        # Flux 完整 checkpoint（flux1-dev-fp8 系）：走 Flux 采样链
        workflow = _build_flux_workflow(
            prompt=prompt, negative_prompt=negative,
            width=width, height=height, seed=seed, filename_prefix=prefix,
            checkpoint=str(body.get("checkpoint") or ""),
        )
    else:
        workflow = _build_sdxl_workflow(
            prompt=prompt, negative_prompt=negative,
            width=width, height=height, seed=seed, filename_prefix=prefix,
            checkpoint=str(body.get("checkpoint") or ""),
        )

    # Krea2 权重（unet 12.9GB + Qwen3-VL-4B TE 5.2GB）只在 workstation 本地
    # models 目录，pc01/pc02 走 NAS 根没有 TE——直连 GPU0 实例，不入 LB 池
    comfy_base = KREA2_BASE_URL if workflow_kind == "krea2" else COMFYUI_LB_URL

    async with _http() as client:
        # 参考图：上传 + IPAdapter 注入（仅 SDXL 链；Flux/Krea2 链无 IPAdapter
        # 配套权重，注入会预检失败）；失败回退普通文生图（不阻断）
        if reference_url and workflow_kind == "sdxl":
            try:
                image_name = await _upload_image_to_comfyui(client, comfy_base, reference_url)
                workflow = _inject_ipadapter(workflow, image_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("IPAdapter 参考图注入失败，回退普通文生图: %s", e)
        try:
            missing = await _preflight_workflow(client, comfy_base, workflow)
            if missing:
                return _error_response(f"生成前预检失败，缺失权重: {'; '.join(missing)}", 502)
            prompt_id = await _submit_comfyui(client, comfy_base, workflow)
        except Exception as e:  # noqa: BLE001
            return _error_response(f"{workflow_kind} 工作流提交失败: {e}", 502)

        # 轮询 /history 取图
        deadline = time.time() + IMAGE_POLL_TIMEOUT
        image_url = ""
        while time.time() < deadline:
            hist = await client.get(f"{comfy_base}/history/{prompt_id}")
            if hist.status_code == 200:
                entry = hist.json().get(prompt_id)
                if entry:
                    status = entry.get("status") or {}
                    if status.get("status_str") == "error" or (status.get("completed") is False and status.get("status_str") == "error"):
                        msgs = status.get("messages") or []
                        return _error_response(f"{workflow_kind} 执行失败: {msgs}", 502)
                    found = _extract_output_media(entry.get("outputs") or {}, comfy_base)
                    if found:
                        image_url = found[0]
                        break
            await asyncio.sleep(1.0)
        if not image_url:
            return _error_response(f"{workflow_kind} 生成超时（{IMAGE_POLL_TIMEOUT}s）", 504)

        img = await client.get(image_url)
        if img.status_code != 200 or not img.content:
            return _error_response(f"取回生成图失败: status={img.status_code}", 502)
        b64 = base64.b64encode(img.content).decode("ascii")
    return {"created": int(time.time()), "data": [{"b64_json": b64}]}


@app.post("/v1/images/generations")
async def images_generations(request: Request) -> Response:
    start = time.time()
    try:
        body = await request.json()
    except Exception:
        return _error_response("invalid JSON body", 400, "invalid_request_error")
    model = str(body.get("model") or "")
    result = await _generate_image(body)
    if isinstance(result, JSONResponse):
        _log("image", model, start, "error", str(result.body[:200]))
        return result
    _log("image", model, start, "ok")
    return JSONResponse(content=result)


@app.post("/v1/images/edits")
async def images_edits(request: Request) -> Response:
    start = time.time()
    try:
        body = await request.json()
    except Exception:
        return _error_response("invalid JSON body", 400, "invalid_request_error")
    model = str(body.get("model") or "")
    # 参考图以 HTTP URL 传入：image（str 或 list）/ reference_images
    ref = body.get("image") or body.get("reference_images") or ""
    if isinstance(ref, list):
        ref = ref[0] if ref else ""
    result = await _generate_image(body, reference_url=str(ref))
    if isinstance(result, JSONResponse):
        _log("image_edit", model, start, "error", str(result.body[:200]))
        return result
    _log("image_edit", model, start, "ok")
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# 视频生成
# ---------------------------------------------------------------------------

async def _h3_online(client: httpx.AsyncClient) -> bool:
    """提交前确认 H3 实例在线（system_stats）。"""
    try:
        resp = await client.get(f"{H3_BASE_URL}/system_stats", timeout=5.0)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def _verify_ltx_nodes(client: httpx.AsyncClient) -> str:
    """核验 LTX 实例必需节点存在；返回空串表示通过，否则错误信息。"""
    global _ltx_nodes_check
    if _ltx_nodes_check is True:
        return ""
    if isinstance(_ltx_nodes_check, str):
        return _ltx_nodes_check
    try:
        resp = await client.get(f"{LTX_BASE_URL}/object_info")
        if resp.status_code != 200:
            _ltx_nodes_check = f"LTX /object_info status={resp.status_code}"
            return _ltx_nodes_check
        info = resp.json()
    except Exception as e:  # noqa: BLE001
        _ltx_nodes_check = f"LTX /object_info 不可达: {e}"
        return _ltx_nodes_check
    missing = [n for n in LTX_REQUIRED_NODES if n not in info]
    if missing:
        _ltx_nodes_check = f"LTX 实例缺少必需节点: {missing}"
        return _ltx_nodes_check
    _ltx_nodes_check = True
    return ""


def _select_video_backend(body: dict[str, Any]) -> str:
    """路由：VIDEO_BACKEND 强制 > NSFW→H3 > H3 模型名 > LTX 模型名 > 时长>15s / audio → H3，否则 LTX。

    NSFW 不走 Wan 2.2 / LTX（P0：SFW/NSFW 视频引擎都是集群 :8195 MiniMax H3）。
    """
    if VIDEO_BACKEND_FORCE in ("h3", "ltx"):
        return VIDEO_BACKEND_FORCE
    if request_nsfw(body):
        return "h3"
    model = str(body.get("model") or "").lower()
    duration = float(body.get("duration") or 5)
    generate_audio = bool(body.get("generate_audio"))
    if "ltx" in model:
        return "ltx"
    if "happyhorse" in model or "minimax-h3" in model or "minimax_h3" in model:
        return "h3"
    if duration > 15 or generate_audio:
        return "h3"
    return "ltx"


@app.post("/v1/video/generations")
async def video_generations(request: Request) -> Response:
    start = time.time()
    try:
        body = await request.json()
    except Exception:
        return _error_response("invalid JSON body", 400, "invalid_request_error")
    model = str(body.get("model") or "")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return _error_response("prompt is required", 400, "invalid_request_error")
    duration = float(body.get("duration") or 5)
    backend = _select_video_backend(body)

    media = collect_video_inputs(body)
    first_frame = media["first"]
    last_frame = media["last"]
    nsfw = request_nsfw(body)

    task_id = f"task_{uuid.uuid4().hex[:16]}"
    task: dict[str, Any] = {
        "status": "queued", "prompt_id": "", "backend": backend,
        "video_url": "", "error": "", "created": time.time(),
    }

    try:
        async with _http(timeout=60.0) as client:
            if backend == "h3":
                if not await _h3_online(client):
                    if nsfw or request_repair(body):
                        return _error_response(
                            "H3 实例离线，NSFW/repair 视频不回退 LTX/Wan", 502
                        )
                    logger.warning("H3 实例离线，回退 LTX-2.5")
                    backend = "ltx"
                    task["backend"] = "ltx"
            if backend == "h3":
                nsfw = request_nsfw(body)
                fl2va_unet, ref2va_unet = h3_unets(nsfw, request_nsfw_variant(body))
                media = collect_video_inputs(body)
                first_frame = media["first"]
                last_frame = media["last"]
                mode = select_h3_mode(media)
                # P1: rewrite the prompt H3 actually receives (fail-open)
                ref_urls_for_ir = r2v_ref_images(media) if mode == "r2v" else []
                ir_mode = (
                    "ref2va" if mode == "r2v"
                    else ("fl2va" if last_frame else ("i2va" if first_frame else "t2va"))
                )
                n_pics = (
                    len(ref_urls_for_ir) if mode == "r2v"
                    else ((1 if first_frame else 0) + (1 if last_frame else 0))
                )
                prompt = await rewrite_h3_context_ir(
                    prompt,
                    mode=ir_mode,
                    duration=duration,
                    nsfw=nsfw,
                    n_pictures=n_pics,
                    n_videos=len(media["ref_videos"][:3]),
                    n_audios=len(media["ref_audios"][:3]),
                    original_fallback=prompt,
                    reference_image_urls=ref_urls_for_ir or [u for u in (first_frame, last_frame) if u],
                )
                width, height = _derive_video_size(body, "h3")
                num_frames = _snap_h3_frames(duration)
                seed = random.randint(0, 2**32 - 1)
                prefix = f"dc_video_{task_id[-8:]}"
                if mode == "r2v":
                    ref_urls = r2v_ref_images(media)
                    ref_names: list[str] = []
                    for url in ref_urls:
                        ref_names.append(
                            await _upload_image_to_comfyui(
                                client, H3_BASE_URL, url, "dc_h3_ref", replicate_to_lb=False
                            )
                        )
                    video_names = [
                        await _upload_media_to_comfyui(
                            client, H3_BASE_URL, url, "dc_h3_vref", ".mp4"
                        )
                        for url in media["ref_videos"][:3]
                    ]
                    audio_names = [
                        await _upload_media_to_comfyui(
                            client, H3_BASE_URL, url, "dc_h3_aref", ".mp3"
                        )
                        for url in media["ref_audios"][:3]
                    ]
                    workflow = _build_h3_r2v_workflow(
                        prompt=prompt, width=width, height=height, num_frames=num_frames,
                        seed=seed, filename_prefix=prefix,
                        ref_image_names=ref_names,
                        ref_video_names=video_names,
                        ref_audio_names=audio_names,
                        unet_name=ref2va_unet,
                    )
                else:
                    # i2v / t2va：T2VA = omit first/last on ImageToVideo
                    first_name = (
                        await _upload_image_to_comfyui(
                            client, H3_BASE_URL, first_frame, "dc_h3_first", replicate_to_lb=False
                        )
                        if first_frame
                        else None
                    )
                    last_name = (
                        await _upload_image_to_comfyui(
                            client, H3_BASE_URL, last_frame, "dc_h3_last", replicate_to_lb=False
                        )
                        if last_frame
                        else None
                    )
                    workflow = _build_h3_workflow(
                        prompt=prompt, width=width, height=height, num_frames=num_frames,
                        seed=seed, filename_prefix=prefix,
                        first_image_name=first_name, last_image_name=last_name,
                        unet_name=fl2va_unet,
                    )
                if request_repair(body):
                    info = await _object_info(client, H3_BASE_URL)
                    try:
                        require_h3_add_guide(info)
                    except H3RepairUnavailable as exc:
                        return _error_response(str(exc), 502)
                    mask_url = request_inpaint_mask(body)
                    mask_name = ""
                    if mask_url:
                        mask_name = await _upload_image_to_comfyui(
                            client, H3_BASE_URL, mask_url, "dc_h3_mask", replicate_to_lb=False
                        )
                    guide_name = ""
                    if mode != "r2v":
                        # FL2VA/I2V already uploaded first_name into node 10 when present
                        pass
                    apply_h3_repair_guide(
                        workflow,
                        mask_name=mask_name,
                        guide_image_name=guide_name,
                        denoise=request_repair_denoise(body),
                    )
                    task["h3_repair"] = True
                missing = await _preflight_workflow(client, H3_BASE_URL, workflow)
                if missing:
                    return _error_response(f"H3 生成前预检失败，缺失权重: {'; '.join(missing)}", 502)
                task["prompt_id"] = await _submit_comfyui(client, H3_BASE_URL, workflow)
                task["h3_mode"] = mode
                task["unet"] = workflow["1"]["inputs"]["unet_name"]
            else:
                node_err = await _verify_ltx_nodes(client)
                if node_err:
                    return _error_response(f"LTX-2.5 后端不可用: {node_err}", 502)
                mode = "flf2v" if (first_frame and last_frame) else ("i2v" if first_frame else "t2v")
                width, height = _derive_video_size(body, "ltx")
                num_frames = _snap_ltx_frames(duration * LTX_FPS)
                first_name = await _upload_image_to_comfyui(client, LTX_BASE_URL, first_frame, "dc_ltx_first") if first_frame else None
                last_name = await _upload_image_to_comfyui(client, LTX_BASE_URL, last_frame, "dc_ltx_last") if last_frame else None
                workflow = _build_ltx_workflow(
                    mode=mode, prompt=prompt,
                    negative_prompt=str(body.get("negative_prompt") or ""),
                    width=width, height=height, num_frames=num_frames,
                    seed=random.randint(0, 2**32 - 1), filename_prefix=f"dc_video_{task_id[-8:]}",
                    first_image_name=first_name, last_image_name=last_name,
                )
                missing = await _preflight_workflow(client, LTX_BASE_URL, workflow)
                if missing:
                    return _error_response(f"LTX 生成前预检失败，缺失权重: {'; '.join(missing)}", 502)
                task["prompt_id"] = await _submit_comfyui(client, LTX_BASE_URL, workflow)
        task["status"] = "queued"
    except Exception as e:  # noqa: BLE001
        _log("video_submit", model, start, "error", str(e))
        return _error_response(f"视频任务提交失败: {e}", 502)

    VIDEO_TASKS[task_id] = task
    _log("video_submit", model, start, "ok")
    return JSONResponse(content={"task_id": task_id, "status": "queued", "backend": backend})


@app.get("/v1/video/generations/{task_id}")
async def video_generation_status(task_id: str) -> Response:
    start = time.time()
    task = VIDEO_TASKS.get(task_id)
    if not task:
        return _error_response(f"task not found: {task_id}", 404, "invalid_request_error")
    if task["status"] in ("succeeded", "failed"):
        _log("video_poll", task_id, start, task["status"])
        return JSONResponse(content=_video_task_view(task_id, task))

    base_url = H3_BASE_URL if task["backend"] == "h3" else LTX_BASE_URL
    try:
        async with _http(timeout=15.0) as client:
            hist = await client.get(f"{base_url}/history/{task['prompt_id']}")
            entry = hist.json().get(task["prompt_id"]) if hist.status_code == 200 else None
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    task["status"] = "failed"
                    task["error"] = json.dumps(status.get("messages") or [], ensure_ascii=False)[:500]
                else:
                    found = _extract_output_media(entry.get("outputs") or {}, base_url)
                    if found:
                        task["status"] = "succeeded"
                        task["video_url"] = found[0]
            if task["status"] == "queued":
                queue = await client.get(f"{base_url}/queue")
                if queue.status_code == 200:
                    q = queue.json()
                    running = [item[1] for item in q.get("queue_running", []) if len(item) > 1]
                    if task["prompt_id"] in running:
                        task["status"] = "running"
    except Exception as e:  # noqa: BLE001
        _log("video_poll", task_id, start, "error", str(e))
        return _error_response(f"轮询后端失败: {e}", 502)

    _log("video_poll", task_id, start, task["status"])
    return JSONResponse(content=_video_task_view(task_id, task))


def _video_task_view(task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": task["status"],
        "backend": task["backend"],
        "video_url": task["video_url"] or None,
        "error": task["error"] or None,
    }


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

@app.post("/v1/audio/speech")
async def audio_speech(request: Request) -> Response:
    start = time.time()
    try:
        body = await request.json()
    except Exception:
        return _error_response("invalid JSON body", 400, "invalid_request_error")
    model = str(body.get("model") or "")
    text = str(body.get("input") or "").strip()
    if not text:
        return _error_response("input is required", 400, "invalid_request_error")
    metadata = body.get("metadata") or {}
    audio_url = str(metadata.get("audio_url") or "").strip()
    emotion_prompt = str(metadata.get("emotion_prompt") or "").strip()

    # OpenAI audio/speech → IndexTTS-2 POST /tts multipart
    # 文本字段用 files={k: (None, v)} 形式发送（真实契约为 multipart/form-data）
    files: dict[str, Any] = {
        "text": (None, text),
        "language": (None, "zh"),
    }
    if emotion_prompt:
        files["emo_text"] = (None, emotion_prompt)

    try:
        async with _http() as client:
            if audio_url:
                ref = await client.get(audio_url)
                if ref.status_code != 200 or not ref.content:
                    return _error_response(f"下载参考声线失败: status={ref.status_code}", 502)
                ext = ".wav"
                for cand in (".wav", ".mp3", ".m4a", ".flac"):
                    if audio_url.lower().split("?")[0].endswith(cand):
                        ext = cand
                        break
                files["ref_audio"] = (f"ref_{uuid.uuid4().hex[:6]}{ext}", ref.content, "application/octet-stream")
            resp = await client.post(f"{TTS_BASE_URL}/tts", files=files)
    except Exception as e:  # noqa: BLE001
        _log("tts", model, start, "error", str(e))
        return _error_response(f"TTS upstream unreachable: {e}", 502)

    if resp.status_code != 200:
        _log("tts", model, start, f"upstream_{resp.status_code}")
        return _error_response(f"TTS 上游错误: status={resp.status_code} body={resp.content[:300]!r}", 502)
    audio_bytes = resp.content
    if not _looks_like_audio(audio_bytes):
        _log("tts", model, start, "error", "non_audio_response")
        return _error_response(f"TTS 返回非音频内容 ({len(audio_bytes)} 字节): {audio_bytes[:80]!r}", 502)

    accept = request.headers.get("accept", "")
    if "application/json" in accept.lower():
        name = f"tts_{uuid.uuid4().hex[:12]}.wav"
        (STATIC_DIR / name).write_bytes(audio_bytes)
        url = f"{PUBLIC_BASE_URL}/static/{name}"
        _log("tts", model, start, "ok")
        # audio/url 双键兼容 DashBox indextts2_fal._extract_audio_url 解析
        return JSONResponse(content={"audio": {"url": url}, "url": url})

    _log("tts", model, start, "ok")
    return Response(content=audio_bytes, media_type="audio/wav")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=GATEWAY_HOST, port=GATEWAY_PORT, log_level="info")


if __name__ == "__main__":
    main()
