"""视频 Agent — 分镜关键帧图片 → 视频片段。

M10 升级：MiniMax H3 (33B, 原生音视频联合生成) 为主，ComfyUI(Wan 2.2) 为回退。
M11 升级：H3 多镜叙事联合生成 — 同集相邻场景合并为一次多镜推理（单 prompt
多 SHOT），再按帧边界 ffmpeg 切分回各场景视频，提升跨镜连续性。
M21 升级：MiniMax H3 + LTX-2.5 双引擎路由 — VideoRequest.engine 显式指定直达；
None/'auto' 按镜头类型路由（对白/角色一致性 → H3；空镜/动作/长场景 → LTX-2.5）。
回退链：ltx → h3 → comfyui；h3 → comfyui（settings.video_backend='comfyui'
时钉死旧 ComfyUI 路径，向后兼容）。

后端选择由 settings.video_backend 控制：
- 'h3' (默认): MiniMax H3 fl2va，workstation 独立 ComfyUI 实例 :8195
- 'comfyui': Wan 2.2 I2V 单卡，作为回退

主后端（h3）失败时自动回退到 ComfyUI（保留原 Wan 2.2 工作流）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import json_repair
from openai import AsyncOpenAI

from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    VideoBatchRequest,
    VideoBatchResult,
    VideoRequest,
    VideoResult,
)
from app.services.ltx25_video_service import (
    LTX25_FPS,
    LTX25VideoService,
    _snap_ltx_frames,
)
from app.services.model_gateway import model_gateway
from app.services.style_anchor import (
    resolve_style_anchor,
    sanitize_style_conflicts,
    style_positive_tail,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# M21 双引擎路由（H3 + LTX-2.5）
# ---------------------------------------------------------------------------

# H3 单镜训练上限约 15s（362 帧@24fps），超过即路由 LTX-2.5
H3_MAX_SINGLE_SHOT_SECONDS = 15.0

# 纯运动/空镜镜头关键词（命中且无台词时路由 LTX-2.5；集中常量便于后续校正）
LTX_MOTION_KEYWORDS = (
    "establishing shot",
    "aerial",
    "drone",
    "landscape",
    "cityscape",
    "timelapse",
    "time-lapse",
    "car chase",
    "chase scene",
    "explosion",
    "camera pans",
    "camera tracks",
    "camera follows",
    "dolly",
    "crane shot",
    "empty street",
    "crowd",
    "fast motion",
    "high-speed",
)


def _prompt_has_dialogue(prompt: str) -> bool:
    """判定 prompt 是否含台词（H3 Context-IR 的 <d> 标签或显式 dialogue 字段）。"""
    text = (prompt or "").lower()
    return "<d>" in text or "dialogue:" in text


def _is_pure_motion_prompt(prompt: str) -> bool:
    """判定是否为纯运动/空镜描述（命中运动关键词且无台词）。"""
    text = (prompt or "").lower()
    if not text or _prompt_has_dialogue(text):
        return False
    return any(keyword in text for keyword in LTX_MOTION_KEYWORDS)


def route_video_engine(request: VideoRequest, settings) -> str:
    """M21 双引擎路由判定（纯函数，便于单测）。

    返回 'h3' / 'ltx' / 'comfyui'：
    1. engine 显式指定（h3/ltx/comfyui）→ 直达；显式 'ltx' 但 ltx_enabled=False
       时降级 'h3'（未启用的引擎不可达）。
    2. engine=None/'auto'/其他 → settings.video_backend='comfyui' 钉死旧路径
       （向后兼容）；'ltx' 直达 LTX；'h3'/'auto' 按镜头类型路由：
       - 有台词（<d> 标签/dialogue:）或 reference_images/videos/audios 或
         last_frame_url（角色一致性/首尾帧锚定）→ 'h3'
       - 时长超 H3 训练上限或纯运动空镜描述（且 ltx_enabled）→ 'ltx'
       - 其余（短剧默认）→ 'h3'
    """
    explicit = (getattr(request, "engine", None) or "").strip().lower()
    if explicit in ("h3", "ltx", "comfyui"):
        if explicit == "ltx" and not settings.ltx_enabled:
            logger.warning("LTX-2.5 未启用（ltx_enabled=False），显式 ltx 降级为 h3")
            return "h3"
        return explicit

    backend_default = (settings.video_backend or "h3").strip().lower()
    if backend_default == "comfyui":
        return "comfyui"  # 旧行为钉死
    if backend_default == "ltx":
        return "ltx" if settings.ltx_enabled else "h3"

    # 'h3'/'auto'：按镜头类型在 H3 与 LTX-2.5 间路由
    if request.reference_images or request.reference_videos or request.reference_audios:
        return "h3"
    if (request.last_frame_url or "").strip():
        return "h3"
    if _prompt_has_dialogue(request.prompt):
        return "h3"
    if settings.ltx_enabled:
        if float(request.duration_seconds or 0) > H3_MAX_SINGLE_SHOT_SECONDS:
            return "ltx"
        if _is_pure_motion_prompt(request.prompt):
            return "ltx"
    return "h3"


def _snap_h3_frames(duration_seconds: int | float) -> int:
    """按时长秒数计算 H3 帧数并对齐 17k+5 网格（24fps）。

    官方模板公式: max(5, round(sec*24)) + (5 - n % 17) % 17
    训练范围 124-362 帧（约 5-15s）；短剧单镜一般 3-6s。
    """
    n = max(5, round(duration_seconds * 24))
    return n + (5 - n % 17) % 17


# MiniMax H3 fl2va 工作流模板（对齐官方 video_minimax_h3_i2v 模板）
# 与 Wan 2.2 的关键差异：
#   - 无负面提示词、无 CFG：BasicGuider 单条件 + SamplerCustomAdvanced 蒸馏采样
#   - 联合音视频 latent：MiniMaxH3ImageToVideo 输出 LATENT 同时含视频/音频隐变量，
#     分别经 VAEDecode（视频）与 VAEDecodeAudio（音频）解码，CreateVideo 合成
#   - 采样器 res_multistep + simple scheduler，steps=20（官方推荐）
# 节点说明：
#    1: UNETLoader - H3 fl2va 扩散模型（默认 INT8 pruned，显存友好）
#    2: CLIPLoader - Qwen3-VL-32B 文本编码器（type=minimax）
#    3: VAELoader - 视频 VAE (fp16)
#    4: VAELoader - 音频 VAE (fp32)
#   10: LoadImage - 分镜关键帧（first_frame）
#   20: MiniMaxH3ImageToVideo - 条件 + 联合音视频 latent（可选 last_frame）
#   30: RandomNoise / 31: KSamplerSelect / 32: BasicScheduler / 33: BasicGuider
#   34: SamplerCustomAdvanced - 采样输出联合 latent
#   40: VAEDecode - 视频帧 / 41: VAEDecodeAudio - 音频
#   50: CreateVideo - 帧+音频合成 VIDEO / 60: SaveVideo - 保存 mp4
WORKFLOW_TEMPLATE_H3 = {
    "1": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "{h3_unet_name}",
            "weight_dtype": "default",
        }
    },
    "2": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "{h3_clip_name}",
            "type": "minimax",
            "device": "default",
        }
    },
    "3": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "{h3_video_vae_name}",
        }
    },
    "4": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "{h3_audio_vae_name}",
        }
    },
    "10": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "{input_image_name}"
        }
    },
    "20": {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "prompt": "{positive_prompt}",
            "width": 768,
            "height": 1344,
            "length": 124,
            "first_frame": ["10", 0],
        }
    },
    "30": {
        "class_type": "RandomNoise",
        "inputs": {
            "noise_seed": 0,
        }
    },
    "31": {
        "class_type": "KSamplerSelect",
        "inputs": {
            "sampler_name": "res_multistep",
        }
    },
    "32": {
        "class_type": "BasicScheduler",
        "inputs": {
            "model": ["1", 0],
            "scheduler": "simple",
            "steps": 20,
            "denoise": 1.0,
        }
    },
    "33": {
        "class_type": "BasicGuider",
        "inputs": {
            "model": ["1", 0],
            "conditioning": ["20", 0],
        }
    },
    "34": {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["30", 0],
            "guider": ["33", 0],
            "sampler": ["31", 0],
            "sigmas": ["32", 0],
            "latent_image": ["20", 1],
        }
    },
    "40": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["34", 0],
            "vae": ["3", 0],
        }
    },
    "41": {
        "class_type": "VAEDecodeAudio",
        "inputs": {
            "samples": ["34", 0],
            "vae": ["4", 0],
        }
    },
    "50": {
        "class_type": "CreateVideo",
        "inputs": {
            "images": ["40", 0],
            "audio": ["41", 0],
            "fps": 24,
            "bit_depth": 8,
        }
    },
    "60": {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["50", 0],
            "filename_prefix": "{video_prefix}",
            "format": "auto",
            "codec": "auto",
        }
    },
}

# MiniMax H3 ref2va 工作流模板（与 fl2va 共用 CLIP/双VAE/采样链/解码尾部）
# 差异点：
#   1: UNETLoader 加载 ref2va 模型（替换为 {h3_ref_unet_name}）
#  20: MiniMaxH3ReferenceToVideo（无 first_frame，有 audio_vae 与 ref_image_size）
#  参考图 LoadImage 节点（含关键帧+角色参考图）经 COMFY_AUTOGROW_V3 组接线：
#  API 格式为嵌套 dict：inputs["ref_images"] = {"ref_image_0": ["10",0], ...}
WORKFLOW_TEMPLATE_H3_R2V = {
    "1": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "{h3_unet_name}",
            "weight_dtype": "default",
        }
    },
    "2": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "{h3_clip_name}",
            "type": "minimax",
            "device": "default",
        }
    },
    "3": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "{h3_video_vae_name}",
        }
    },
    "4": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "{h3_audio_vae_name}",
        }
    },
    # 以下 LoadImage 节点为占位示例，实际根据 reference_images 数量动态注入
    "10": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "{input_image_name}"
        }
    },
    "20": {
        "class_type": "MiniMaxH3ReferenceToVideo",
        "inputs": {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "audio_vae": ["4", 0],
            "prompt": "{positive_prompt}",
            "width": 768,
            "height": 1344,
            "length": 124,
            "ref_image_size": "match",
            "ref_images": {"ref_image_0": ["10", 0]},
        }
    },
    "30": {
        "class_type": "RandomNoise",
        "inputs": {
            "noise_seed": 0,
        }
    },
    "31": {
        "class_type": "KSamplerSelect",
        "inputs": {
            "sampler_name": "res_multistep",
        }
    },
    "32": {
        "class_type": "BasicScheduler",
        "inputs": {
            "model": ["1", 0],
            "scheduler": "simple",
            "steps": 20,
            "denoise": 1.0,
        }
    },
    "33": {
        "class_type": "BasicGuider",
        "inputs": {
            "model": ["1", 0],
            "conditioning": ["20", 0],
        }
    },
    "34": {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["30", 0],
            "guider": ["33", 0],
            "sampler": ["31", 0],
            "sigmas": ["32", 0],
            "latent_image": ["20", 1],
        }
    },
    "40": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["34", 0],
            "vae": ["3", 0],
        }
    },
    "41": {
        "class_type": "VAEDecodeAudio",
        "inputs": {
            "samples": ["34", 0],
            "vae": ["4", 0],
        }
    },
    "50": {
        "class_type": "CreateVideo",
        "inputs": {
            "images": ["40", 0],
            "audio": ["41", 0],
            "fps": 24,
            "bit_depth": 8,
        }
    },
    "60": {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["50", 0],
            "filename_prefix": "{video_prefix}",
            "format": "auto",
            "codec": "auto",
        }
    },
}

# ref2va prompt 固定引导（官方节点实践：prompt 内用 1-based 标签 <Picture i>
# 引用参考图）。<Picture 1> = 分镜关键帧（构图参考），<Picture 2> 起 = 角色定妆三视图（外观锁定）
H3_R2V_PROMPT_GUIDE = (
    " Reference images: <Picture 1> is this shot's composition keyframe; "
    "<Picture 2> onward are the characters' appearance references. "
    "Keep each character's face, hairstyle, outfit and body shape exactly "
    "consistent with their reference picture."
)

# ---------------------------------------------------------------------------
# M11: H3 多镜叙事联合生成
# ---------------------------------------------------------------------------

# 多镜 prompt 固定总览前缀（官方多镜叙事实践：显式声明跨镜连续性要求），
# 随后按官方 Context-IR 格式组装 integrated_multimodal_description（M17.1）
H3_MULTISHOT_PROMPT_GUIDE = (
    "Continuous multi-shot narrative video. Keep the characters' appearance, "
    "lighting and cinematic style consistent across shots, with natural shot transitions."
)

# M12.1 叙事节拍 → 英文视觉指令（与 storyboard_agent.BEAT_VISUAL_HINTS 同语义，
# 但 SHOT prompt 为英文故独立维护英文版，避免中英混排稀释 Qwen3-VL 编码语义）
_MULTISHOT_BEAT_HINTS_EN = {
    "hook": "high-contrast dramatic lighting, oppressive composition, intense expression, instant visual impact",
    "escalation": "tighter framing, stronger chiaroscuro, confrontational body language, rising tension",
    "reversal": "frozen beat of subverted expectation, dramatic twist, expressive close-up",
    "cliffhanger": "withheld information, negative-space composition, suspense, urge to continue",
    "emotional_beat": "soft light, shallow depth of field, delicate emotional close-up, slowed pace",
    "transition": "calm establishing framing, visual lead-in, uncluttered composition",
}

# ---------------------------------------------------------------------------
# M17.2 原生音频方向（overall_soundscape / non_diegetic_music）
# ---------------------------------------------------------------------------
# H3 原生支持 32kHz 立体声音频联合生成：prompt 中给出官方三段式音频字段后，
# 原生音轨从「随机环境噪音」升级为「有叙事意图的 BGM + 环境声景」，再经现有
# 混音链（h3_native_audio_enabled 垫底增益）与人声 TTS 叠加，成片配乐质变。
# 官方指南约束：soundscape 1-4 句（环境声/物理声/非语言人声，不含对白），
# music 1-3 句（器乐/速度/动态，禁用抽象情绪词），无配乐写 N/A。
_BEAT_SOUNDSCAPE_EN = {
    "hook": "A low, tense room tone with a sharp sudden impact and lingering reverb.",
    "escalation": "Rising ambient tension with quickened footsteps, rustling fabric and heavy breathing.",
    "reversal": "An abrupt hush: all ambience drops to a faint room tone with a single sharp accent.",
    "cliffhanger": "A sustained low drone of room ambience, distant muffled sounds, held breath.",
    "emotional_beat": "Soft room tone with gentle breathing, delicate fabric movement and quiet sniffles.",
    "transition": "Calm neutral ambience with soft distant background murmur and light footsteps.",
}

_BEAT_MUSIC_EN = {
    "hook": "A tense pulsating electronic score at a fast tempo with heavy percussion hits.",
    "escalation": "Driving orchestral strings at a rising tempo, layered and building in intensity.",
    "reversal": "A sparse solo piano motif at a slow tempo after the previous score cuts out abruptly.",
    "cliffhanger": "A sustained low synth drone with sparse high string stabs, holding without resolution.",
    "emotional_beat": "A gentle solo piano melody at a slow tempo with warm sustained cello underneath.",
    "transition": "Light ambient pads at a moderate tempo with a soft rhythmic pulse.",
}

# 组内多节拍时取戏剧性最强者定全组音频基调（hook/reversal 优先于平缓节拍）
_BEAT_AUDIO_PRIORITY = (
    "reversal", "hook", "cliffhanger", "escalation", "emotional_beat", "transition",
)


def build_audio_direction(beats: list[str]) -> str:
    """按组内叙事节拍确定性生成 H3 官方音频字段（soundscape + music）。

    无合法节拍时返回空串（调用方跳过注入，保持原 prompt 不变）。
    多节拍组按 _BEAT_AUDIO_PRIORITY 取最强节拍定全组音频基调。
    """
    valid = {(b or "").strip().lower() for b in beats}
    dominant = next((b for b in _BEAT_AUDIO_PRIORITY if b in valid), None)
    if dominant is None:
        return ""
    return (
        f"overall_soundscape: {_BEAT_SOUNDSCAPE_EN[dominant]}\n"
        f"non_diegetic_music: {_BEAT_MUSIC_EN[dominant]}"
    )


def _format_cut_timestamp(seconds: float) -> str:
    """M17.1 CUT 时间戳：MM:SS.mmm（官方指南示例 00:04.500）。"""
    ms = max(0, round(seconds * 1000))
    minutes, rem = divmod(ms, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def build_fl2va_alignment_instruction(
    duration_seconds: float, last_shot: str = "Shot 1"
) -> str:
    """M17.3 FL2VA 官方对齐指令（前置 prompt 首部）。

    官方指南固定句式：Picture 1 → 0.00s（首帧），Picture 2 → S.SSs（末帧）；
    多镜组末帧属于组末 Shot N（last_shot 传入 "Shot N"）。
    """
    return (
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from {last_shot}) aligns with the "
        f"{float(duration_seconds):.2f}-second mark of the target video."
    )


def build_r2v_media_guide(n_videos: int, n_audios: int) -> str:
    """M17.4 ref2va 音视频参考的 prompt 引导（官方 <Video N>/<Audio N> 标签）。

    标签编号与 ref_videos / ref_audios autogrow 组内挂接顺序一致（0-based 组键
    对应 1-based 标签）；无音视频参考时返回空串（prompt 不变）。
    """
    parts: list[str] = []
    if n_videos > 0:
        labels = ", ".join(f"<Video {i}>" for i in range(1, n_videos + 1))
        parts.append(
            f"{labels} {'is' if n_videos == 1 else 'are'} reference "
            f"clip{'s' if n_videos > 1 else ''} for camera movement, motion rhythm "
            "and editing structure, with its soundtrack as audio reference"
        )
    if n_audios > 0:
        labels = ", ".join(f"<Audio {i}>" for i in range(1, n_audios + 1))
        parts.append(
            f"{labels} {'is' if n_audios == 1 else 'are'} audio "
            f"reference{'s' if n_audios > 1 else ''} for background-music style "
            "and soundscape texture (referenced, not copied)"
        )
    if not parts:
        return ""
    return " " + " ".join(parts) + "."


def _append_audio_direction(prompt: str, beats: list[str]) -> str:
    """M17.2：音频方向开启且存在合法节拍时，在 prompt 尾部追加官方音频字段。

    官方 prompt 结构约定 soundscape/music 位于描述段之后，故始终放最末。
    """
    if not settings.h3_audio_direction_enabled:
        return prompt
    direction = build_audio_direction(beats)
    return f"{prompt}\n{direction}" if direction else prompt


def _apply_h3_turbo_to_workflow(workflow: dict[str, Any]) -> None:
    """将原生 H3 工作流改造为 Turbo LoRA 工作流（可选加速，默认关闭）。

    改造点：
      1. 在 UNETLoader(节点 1) 后插入 MiniMaxH3TurboLoRA(节点 100)
      2. BasicGuider/BasicScheduler 的 model 输入指向 LoRA 输出
      3. 新增 MiniMaxH3TurboSampler(节点 101) 替换 KSamplerSelect
      4. SamplerCustomAdvanced 的 sampler 输入指向 TurboSampler
      5. BasicScheduler steps 改为 h3_turbo_steps

    注意：Turbo 会改变扩散轨迹，M18.4 画风 QC 阈值可能需要针对 Turbo
    重新标定；当前默认关闭以保持原生高质量。
    """
    if not settings.h3_turbo_enabled:
        return

    if workflow.get("1", {}).get("class_type") != "UNETLoader":
        logger.warning("H3 Turbo: 未找到 UNETLoader 节点 1，跳过改造")
        return

    lora_node_id = "100"
    sampler_node_id = "101"

    # 1. 插入 Turbo LoRA 节点
    workflow[lora_node_id] = {
        "class_type": "MiniMaxH3TurboLoRA",
        "inputs": {
            "model": ["1", 0],
            "lora_name": settings.h3_turbo_lora_name,
            "strength": settings.h3_turbo_strength,
            "low_vram": settings.h3_turbo_low_vram,
        },
    }

    # 2. 把依赖 UNet 输出的节点 model 输入改指向 LoRA（跳过 LoRA 自身）
    for nid, node in workflow.items():
        if nid == lora_node_id or not isinstance(node, dict) or "inputs" not in node:
            continue
        for key, value in list(node["inputs"].items()):
            if isinstance(value, list) and len(value) == 2 and value == ["1", 0]:
                node["inputs"][key] = [lora_node_id, 0]

    # 3. 新增 Turbo Sampler 节点
    workflow[sampler_node_id] = {
        "class_type": "MiniMaxH3TurboSampler",
        "inputs": {},
    }

    # 4. SamplerCustomAdvanced 的 sampler 输入指向 TurboSampler
    sampler_advanced = workflow.get("34", {})
    if sampler_advanced.get("class_type") == "SamplerCustomAdvanced":
        sampler_advanced["inputs"]["sampler"] = [sampler_node_id, 0]

    # 5. BasicScheduler steps 改为 turbo steps
    scheduler = workflow.get("32", {})
    if scheduler.get("class_type") == "BasicScheduler":
        scheduler["inputs"]["steps"] = settings.h3_turbo_steps

    logger.info(
        "H3 Turbo 工作流已启用: lora=%s strength=%s steps=%s low_vram=%s",
        settings.h3_turbo_lora_name,
        settings.h3_turbo_strength,
        settings.h3_turbo_steps,
        settings.h3_turbo_low_vram,
    )


# ---------------------------------------------------------------------------
# M18.4: H3 画风漂移治理 —— 约束层（prompt 冲突清洗 + 风格锚定尾）
# ---------------------------------------------------------------------------
# 背景：M18.1 帧级核验发现 H3 输出（半写实厚涂）与参考图（卡通平涂）系统性
# 脱节——orchestrator 虽追加了 style tail，但剧本 LLM 场景 prompt 残留的
# hyperrealistic / cinematic realism 等冲突词在 H3 侧无任何清洗，风格尾被
# 正文冲突信号抵消。约束层对 fl2va / r2v / 多镜三条 H3 路径统一锚定。


def apply_h3_style_anchor(prompt: str, style: str) -> str:
    """M18.4 约束层：清洗画风冲突词 + 幂等追加风格锚定尾。

    style 为空（直连 API 未传画风）或开关关闭时原样透传（向后兼容）；
    orchestrator 已追加风格尾（M15.1）时幂等跳过，不二次追加。
    """
    if not settings.h3_style_anchor_enabled or not (style or "").strip():
        return prompt
    anchor = resolve_style_anchor(style)
    cleaned = sanitize_style_conflicts(prompt, anchor)
    tail = style_positive_tail(anchor)
    if tail and tail.strip().lower() not in cleaned.lower():
        cleaned = cleaned.rstrip(" ,") + tail
    return cleaned


def strengthen_h3_style_clause(prompt: str, style: str) -> str:
    """M18.4 纠偏层：漂移重生成时前置强化画风子句（原 prompt 保留在后）。"""
    anchor = resolve_style_anchor(style)
    if not anchor.style_name_en:
        return prompt
    return f"Rendered strictly in {anchor.style_name_en}. {prompt}"


def build_multishot_prompt(
    requests: list[VideoRequest],
    native_cut: bool | None = None,
    audio_direction: bool | None = None,
) -> str:
    """将同组各场景 prompt 组装为 H3 多镜叙事 prompt。

    M17.1 原生 CUT 语法（默认开启）：官方 Context-IR 格式
      integrated_multimodal_description: [Shot 1] ... [Shot N] At MM:SS.mmm, the camera cuts to ...
    首镜不带时间戳；后续镜时间戳 = 前序场景时长累计（与 _multishot_split_plan
    帧边界同源）；native_cut=False 回退 M11 旧版 "SHOT X:" 格式（保险丝）。

    M12.1：场景带合法 narrative_beat 时在各镜尾追加对应英文节拍视觉指令。
    M17.2：audio_direction 开启且组内含合法节拍时，尾部追加官方
      overall_soundscape / non_diegetic_music 音频方向字段。
    M18.4：各镜 prompt 逐镜做画风冲突清洗 + 幂等风格锚定（取各镜自身 style，
      空串回退组内首个非空 style；全组无 style 时原样，向后兼容）。
    """
    if native_cut is None:
        native_cut = settings.h3_native_cut_prompt_enabled
    if audio_direction is None:
        audio_direction = settings.h3_audio_direction_enabled

    # M18.4 组级画风基准：组内首个非空 style（同集任务画风一致，逐镜兜底用）
    group_style = next((r.style for r in requests if (r.style or "").strip()), "")

    lines = [H3_MULTISHOT_PROMPT_GUIDE]
    shots: list[str] = []
    elapsed = 0.0
    for idx, req in enumerate(requests, start=1):
        shot = (req.prompt or "").strip() or "cinematic, high quality, smooth motion"
        shot = apply_h3_style_anchor(shot, req.style or group_style)
        hint = _MULTISHOT_BEAT_HINTS_EN.get((req.narrative_beat or "").strip().lower())
        text = shot + (f" ({hint})" if hint else "")
        if native_cut:
            if idx == 1:
                shots.append(f"[Shot 1] {text}")
            else:
                ts = _format_cut_timestamp(elapsed)
                shots.append(f"[Shot {idx}] At {ts}, the camera cuts to {text}")
        else:
            shots.append(f"SHOT {idx}: {text}")
        elapsed += float(req.duration_seconds)
    if native_cut:
        lines.append("integrated_multimodal_description: " + " ".join(shots))
    else:
        lines.extend(shots)
    if audio_direction:
        direction = build_audio_direction([r.narrative_beat for r in requests])
        if direction:
            lines.append(direction)
    return "\n".join(lines)

# 多镜切分产物落地目录与 URL 约定（与剪辑 Agent 成片一致）：
# 文件写入 platform/backend/output/video/，经 /static/video/ 对外提供，
# 后续管线（配音/字幕/剪辑）按 URL 下载或经本地静态路径直读
_MULTISHOT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "video"

# ffmpeg 最小封装（与剪辑 Agent 同源的二进制解析，避免 import edit_agent 造成循环依赖）
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFMPEG_BIN = str(_FFMPEG_FULL) if _FFMPEG_FULL.exists() else (shutil.which("ffmpeg") or "ffmpeg")


def group_scenes_for_multishot(
    requests: list[VideoRequest],
    max_scenes: int,
    max_seconds: float,
) -> list[list[VideoRequest]]:
    """同集相邻场景贪心合并为多镜组（保持输入顺序，纯函数）。

    规则：
    - 仅同集（episode 相同）且在输入列表中相邻的场景可同组
    - 组内场景数 ≤ max_scenes，组内总时长 ≤ max_seconds
    - 返回覆盖全部输入的分组；单元素组由调用方走原逐场景路径（≥2 场景才成组）
    """
    groups: list[list[VideoRequest]] = []
    current: list[VideoRequest] = []
    current_seconds = 0.0
    current_episode: int | None = None
    for req in requests:
        duration = float(req.duration_seconds)
        if (
            current
            and req.episode == current_episode
            and len(current) < max_scenes
            and current_seconds + duration <= max_seconds
        ):
            current.append(req)
            current_seconds += duration
        else:
            if current:
                groups.append(current)
            current = [req]
            current_seconds = duration
            current_episode = req.episode
    if current:
        groups.append(current)
    return groups


def _multishot_split_plan(
    durations_seconds: list[int | float],
    total_frames: int,
    fps: int = 24,
) -> list[tuple[int, int]]:
    """按各场景时长累计帧偏移计算切分边界，最后一场吃到组尾。

    组总帧数经 17k+5 网格吸附后可能略大于各场景时长之和，
    余量全部归末段；返回每场景 (start_frame, end_frame) 帧区间（左闭右开）。
    """
    plan: list[tuple[int, int]] = []
    start = 0
    last = len(durations_seconds) - 1
    for i, duration in enumerate(durations_seconds):
        end = total_frames if i == last else start + round(float(duration) * fps)
        plan.append((start, end))
        start = end
    return plan


async def _run_ffmpeg(cmd: list[str]) -> None:
    """异步运行 FFmpeg（create_subprocess_exec，不阻塞事件循环），失败抛 RuntimeError。"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="ignore")[-500:]
        raise RuntimeError(f"FFmpeg failed: {err}")


# Wan 2.2 I2V 工作流模板（ComfyUI 原生节点，无需 WanVideoWrapper 自定义插件）
# 节点说明：
#    1: UNETLoader - 加载 Wan 2.2 I2V high_noise 模型（前半步数去噪）
#    2: UNETLoader - 加载 Wan 2.2 I2V low_noise 模型（后半步数去噪）
#    3: ModelSamplingSD3 - high_noise shift 调整（480p 推荐 3.0）
#    4: ModelSamplingSD3 - low_noise shift 调整
#   10: VAELoader - 加载 Wan 2.1 VAE
#   11: CLIPLoader - 原生 CLIP 加载器（type=wan，支持 fp8 scaled UMT5）
#   12: CLIPTextEncode - 正面提示词编码
#   13: CLIPTextEncode - 反向提示词编码
#   20: LoadImage - 加载分镜关键帧图片
#   21: WanImageToVideo - 原生 I2V 条件节点（输出 positive/negative/latent）
#   22: CLIPVisionLoader - 加载 CLIP-ViT-H 视觉编码器
#   23: CLIPVisionEncode - 关键帧视觉特征编码（提升主体一致性）
#   30: KSamplerAdvanced - high_noise 采样（0 → steps/2，保留噪声）
#   31: KSamplerAdvanced - low_noise 采样（steps/2 → 结束）
#   40: VAEDecode - 解码视频帧
#   50: VHS_VideoCombine - 保存为 MP4
WORKFLOW_TEMPLATE = {
    "1": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "weight_dtype": "default",
        }
    },
    "2": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            "weight_dtype": "default",
        }
    },
    "3": {
        "class_type": "ModelSamplingSD3",
        "inputs": {
            "shift": 3.0,
            "model": ["1", 0],
        }
    },
    "4": {
        "class_type": "ModelSamplingSD3",
        "inputs": {
            "shift": 3.0,
            "model": ["2", 0],
        }
    },
    "10": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "wan_2.1_vae.safetensors",
        }
    },
    "11": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "type": "wan",
        }
    },
    "12": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "{positive_prompt}",
            "clip": ["11", 0],
        }
    },
    "13": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "{negative_prompt}",
            "clip": ["11", 0],
        }
    },
    "20": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "{input_image_name}"
        }
    },
    "22": {
        "class_type": "CLIPVisionLoader",
        "inputs": {
            "clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
        }
    },
    "23": {
        "class_type": "CLIPVisionEncode",
        "inputs": {
            "crop": "center",
            "clip_vision": ["22", 0],
            "image": ["20", 0],
        }
    },
    "21": {
        "class_type": "WanImageToVideo",
        "inputs": {
            "width": 480,
            "height": 832,
            "length": 81,
            "batch_size": 1,
            "positive": ["12", 0],
            "negative": ["13", 0],
            "vae": ["10", 0],
            "clip_vision_output": ["23", 0],
            "start_image": ["20", 0],
        }
    },
    "30": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "add_noise": "enable",
            "noise_seed": 0,
            "steps": 20,
            "cfg": 3.5,
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": 0,
            "end_at_step": 10,
            "return_with_leftover_noise": "enable",
            "model": ["3", 0],
            "positive": ["21", 0],
            "negative": ["21", 1],
            "latent_image": ["21", 2],
        }
    },
    "31": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "add_noise": "disable",
            "noise_seed": 0,
            "steps": 20,
            "cfg": 3.5,
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": 10,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
            "model": ["4", 0],
            "positive": ["21", 0],
            "negative": ["21", 1],
            "latent_image": ["30", 0],
        }
    },
    "40": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["31", 0],
            "vae": ["10", 0],
        }
    },
    "50": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["40", 0],
            "frame_rate": 24,
            "loop_count": 0,
            "filename_prefix": "{video_prefix}",
            "format": "video/h264-mp4",
            "save_output": True,
            "pingpong": False,
        }
    },
}


class VideoAgent(BaseAgent):
    """视频 Agent：分镜图片 → 视频片段。

    后端选择由 settings.video_backend 控制：
    - 'h3' (默认): MiniMax H3 fl2va（workstation :8195 独立 ComfyUI 实例）
    - 'comfyui': Wan 2.2 I2V 单卡（回退路径）

    主后端（h3）失败时自动回退到 ComfyUI（保留原 Wan 2.2 工作流）。
    """

    def __init__(self):
        super().__init__("video_agent")
        # M18.4 画风质检 VLM 客户端（懒加载，复用基类 httpx）
        self._vlm_client: AsyncOpenAI | None = None

    def _get_vlm_client(self) -> AsyncOpenAI:
        """懒加载 VLM 客户端（与 character_agent 同构，复用基类 httpx）。"""
        if self._vlm_client is None:
            self._vlm_client = AsyncOpenAI(
                base_url=settings.visual_model_url,
                api_key="not-needed",
                http_client=self.http,
            )
        return self._vlm_client

    async def execute(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
        worker_url: str | None = None,
    ) -> AgentResponse:
        """M21 双引擎分发：route_video_engine 判定引擎，按回退链依次尝试。

        回退链：ltx → h3 → comfyui；h3 → comfyui；comfyui 直达。
        全部失败时返回 success=False，error 携带各引擎错误（{engine}={err} 串联）。
        """
        start = time.time()
        engine = route_video_engine(request, settings)

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        chains = {
            "ltx": ("ltx", "h3", "comfyui"),
            "h3": ("h3", "comfyui"),
            "comfyui": ("comfyui",),
        }
        chain = chains[engine]
        errors: list[str] = []
        for idx, eng in enumerate(chain):
            try:
                if eng == "ltx":
                    _report(5, "LTX-2.5 音视频联合生成")
                    return await self._execute_via_ltx(request, progress_callback)
                if eng == "h3":
                    _report(5, "MiniMax H3 音视频联合生成")
                    return await self._execute_h3_with_style_qc(request, progress_callback)
                # 'comfyui' → Wan 2.2 原路径
                return await self._execute_via_comfyui(
                    request, progress_callback, worker_url
                )
            except Exception as err:
                errors.append(f"{eng}={err}")
                if idx == len(chain) - 1:
                    break
                logger.warning(
                    "%s 视频生成失败，回退到 %s: scene_id=%s err=%s",
                    eng, chain[idx + 1], request.scene_id, err,
                )
                _report(50, f"{eng} 失败，回退 {chain[idx + 1]}: {err}")
        return AgentResponse(
            success=False,
            error=(
                f"视频生成失败({'+'.join(chain)} 均失败): {'; '.join(errors)}"
            ),
            elapsed_seconds=time.time() - start,
        )

    async def _execute_via_ltx(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """LTX-2.5 推理路径（workstation :8198 专用实例，与生产 LB 隔离）。

        帧率 25fps（音频 1:1 对齐）：num_frames = duration*25+1 再吸附 %8==1 网格。
        有末帧 → FLF2V 双锚定；有首帧（分镜关键帧）→ I2V；否则 T2V。
        服务层失败（success=False）在此转为异常，由 execute 回退链统一兜底。
        """
        if progress_callback:
            progress_callback(5, "LTX-2.5 音视频联合生成（distilled 8+3 步）")
        service = LTX25VideoService()
        num_frames = _snap_ltx_frames(int(round(request.duration_seconds * LTX25_FPS)) + 1)
        prompt = request.prompt or "cinematic, high quality, smooth motion"
        if (request.last_frame_url or "").strip():
            resp = await service.generate_flf2v(
                request.image_url,
                request.last_frame_url,
                prompt,
                negative_prompt=request.negative_prompt,
                num_frames=num_frames,
                scene_id=request.scene_id,
            )
        elif (request.image_url or "").strip():
            resp = await service.generate_i2v(
                request.image_url,
                prompt,
                negative_prompt=request.negative_prompt,
                num_frames=num_frames,
                scene_id=request.scene_id,
            )
        else:
            resp = await service.generate_t2v(
                prompt,
                negative_prompt=request.negative_prompt,
                num_frames=num_frames,
                scene_id=request.scene_id,
            )
        if not resp.success:
            raise RuntimeError(resp.error or "LTX-2.5 生成失败")
        return resp

    async def execute_multi_shot(
        self,
        requests: list[VideoRequest],
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> list[AgentResponse]:
        """M11 多镜叙事联合生成：同组场景一次 H3 推理 → ffmpeg 切分 → 逐场景返回。

        仅 h3 后端且 ≥2 场景时走多镜；任一环节失败整组回退为逐场景调用
        execute（每场景各自走 h3 单镜 + comfyui 回退），保证健壮性。
        返回与 requests 一一对应的 AgentResponse 列表（scene_id 对应）。
        """
        start = time.time()

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        if settings.video_backend.lower() != "h3" or len(requests) < 2:
            return await self._execute_scenes_individually(
                requests, progress_callback, start
            )

        _report(5, f"MiniMax H3 多镜叙事联合生成（{len(requests)} 场景）")
        try:
            return await self._execute_multishot_group(requests, _report, start)
        except Exception as err:
            logger.warning(
                "H3 多镜联合生成失败，整组回退逐场景生成: scenes=%s err=%s",
                [r.scene_id for r in requests], err,
            )
            _report(50, f"多镜失败，回退逐场景生成: {err}")
            return await self._execute_scenes_individually(
                requests, progress_callback, start
            )

    async def _execute_scenes_individually(
        self,
        requests: list[VideoRequest],
        progress_callback: Callable[[int, str], None] | None,
        start: float,
    ) -> list[AgentResponse]:
        """逐场景调用 execute 的兜底路径（各自享有 h3 单镜 + comfyui 回退）。"""
        results: list[AgentResponse] = []
        for req in requests:
            try:
                results.append(
                    await self.execute(req, progress_callback=progress_callback)
                )
            except Exception as err:
                # execute 内部已兜底返回 AgentResponse，此处防御性捕获
                results.append(
                    AgentResponse(
                        success=False,
                        error=f"视频生成失败: {err}",
                        elapsed_seconds=time.time() - start,
                    )
                )
        return results

    async def _execute_multishot_group(
        self,
        requests: list[VideoRequest],
        report: Callable[[int, str], None],
        start: float,
    ) -> list[AgentResponse]:
        """H3 多镜联合推理：一次工作流生成组视频，再按帧边界 ffmpeg 切分回各场景。

        组内有参考图走 ref2va（首场景关键帧作第 1 张参考图），否则 fl2va
        （首场景关键帧作 first_frame）；任一环节异常由 execute_multi_shot 整组回退。
        """
        worker_url = model_gateway.endpoint("video_h3")
        scene_ids = [r.scene_id for r in requests]
        total_seconds = sum(float(r.duration_seconds) for r in requests)
        num_frames = _snap_h3_frames(total_seconds)
        multi_prompt = build_multishot_prompt(requests)

        # 参考资产跨场景合并去重（保序）；任一非空则多镜优先走 r2v 工作流（M17.4
        # 从纯图片参考扩展到全模态：图/视频/音频触发条件与单镜 _execute_via_h3 对齐）
        merged_refs = self._merge_group_reference_images(requests)
        merged_videos = self._dedupe_reference_urls(
            [url for req in requests for url in req.reference_videos]
        )[: max(0, settings.h3_ref_max_videos)]
        merged_audios = self._dedupe_reference_urls(
            [url for req in requests for url in req.reference_audios]
        )[: max(0, settings.h3_ref_max_audios)]
        use_r2v = bool(merged_refs or merged_videos or merged_audios)

        report(10, f"上传多镜关键帧与参考图到 H3 ComfyUI（{len(requests)} 场景联合）")
        keyframe_name = await self.upload_image_to_comfyui(
            worker_url, requests[0].image_url
        )

        report(15, "构建 MiniMax H3 多镜工作流")
        import random

        if use_r2v:
            # 关键帧占 1 席，角色参考图最多 max-1 张
            ref_urls = merged_refs[: max(0, settings.h3_ref_max_images - 1)]
            ref_names: list[str] = []
            for url in ref_urls:
                ref_names.append(await self.upload_image_to_comfyui(worker_url, url))
            workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE_H3_R2V))
            workflow["1"]["inputs"]["unet_name"] = settings.h3_ref_unet_name
            # 角色参考图动态挂接：LoadImage 节点 11/12/... → ref_images 组内 ref_image_1/2/...
            # （COMFY_AUTOGROW_V3 API 格式为嵌套 dict，扁平键执行期 TypeError）
            ref_group = workflow["20"]["inputs"].setdefault("ref_images", {})
            for idx, name in enumerate(ref_names, start=1):
                node_id = str(10 + idx)
                workflow[node_id] = {
                    "class_type": "LoadImage",
                    "inputs": {"image": name},
                }
                ref_group[f"ref_image_{idx}"] = [node_id, 0]
            # M17.4 组级音视频参考注入（与单镜同一辅助方法，节点 7X/8X/9X）
            n_videos, n_audios = await self._inject_r2v_media_refs(
                workflow, worker_url, merged_videos, merged_audios
            )
            workflow["20"]["inputs"]["prompt"] = (
                multi_prompt
                + H3_R2V_PROMPT_GUIDE
                + build_r2v_media_guide(n_videos, n_audios)
            )
            workflow["20"]["inputs"]["ref_image_size"] = settings.h3_ref_image_size
        else:
            workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE_H3))
            workflow["1"]["inputs"]["unet_name"] = settings.h3_unet_name
            # M17.3 多镜 FL2VA 双锚定：组末帧 = 末场景的链式末帧（orchestrator 注入
            # 的组后一镜关键帧，实现组间链式连续）；无链式末帧时回退末场景自身关键帧
            last_frame_url = requests[-1].last_frame_url or requests[-1].image_url
            if last_frame_url:
                last_frame_name = await self.upload_image_to_comfyui(
                    worker_url, last_frame_url
                )
                workflow["11"] = {
                    "class_type": "LoadImage",
                    "inputs": {"image": last_frame_name},
                }
                workflow["20"]["inputs"]["last_frame"] = ["11", 0]
                multi_prompt = (
                    f"{build_fl2va_alignment_instruction(total_seconds, last_shot=f'Shot {len(requests)}')}\n"
                    f"{multi_prompt}"
                )
            workflow["20"]["inputs"]["prompt"] = multi_prompt

        workflow["2"]["inputs"]["clip_name"] = settings.h3_clip_name
        workflow["3"]["inputs"]["vae_name"] = settings.h3_video_vae_name
        workflow["4"]["inputs"]["vae_name"] = settings.h3_audio_vae_name
        workflow["10"]["inputs"]["image"] = keyframe_name
        workflow["20"]["inputs"]["width"] = settings.h3_width
        workflow["20"]["inputs"]["height"] = settings.h3_height
        workflow["20"]["inputs"]["length"] = num_frames
        workflow["30"]["inputs"]["noise_seed"] = random.randint(0, 2**32 - 1)
        workflow["31"]["inputs"]["sampler_name"] = settings.h3_sampler
        workflow["32"]["inputs"]["scheduler"] = settings.h3_scheduler
        workflow["32"]["inputs"]["steps"] = settings.h3_steps
        _apply_h3_turbo_to_workflow(workflow)
        workflow["60"]["inputs"]["filename_prefix"] = (
            f"video_multishot_{scene_ids[0]}_{scene_ids[-1]}"
        )

        report(25, "提交 H3 多镜联合生成任务")
        result = await self.call_comfyui(worker_url, workflow)
        prompt_id = result.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError(f"H3 ComfyUI 未返回 prompt_id: {result}")

        report(30, "H3 多镜采样中（33B 模型，组视频约 10-20 分钟）")
        outputs = await self.get_comfyui_result(
            worker_url, prompt_id, timeout=settings.h3_result_timeout
        )

        report(90, "下载多镜组视频")
        group_video_url = self._extract_video_url(outputs, worker_url, scene_ids[0])

        report(92, "按场景帧边界切分多镜视频")
        plan = _multishot_split_plan(
            [r.duration_seconds for r in requests], num_frames
        )
        _MULTISHOT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        responses: list[AgentResponse] = []
        with tempfile.TemporaryDirectory(prefix="h3_multishot_") as tmp:
            group_path = await self._download_to_file(
                group_video_url, Path(tmp) / "group.mp4"
            )
            base_url = f"http://localhost:{settings.backend_port}"
            for req, (start_frame, end_frame) in zip(requests, plan):
                seg_name = f"video_scene_{req.scene_id}.mp4"
                seg_path = _MULTISHOT_OUTPUT_DIR / seg_name
                # 重编码切分（libx264 + aac）：-ss 输入寻址 + 重编码保证帧精度
                await _run_ffmpeg([
                    FFMPEG_BIN,
                    "-y",
                    "-ss", f"{start_frame / 24:.3f}",
                    "-i", str(group_path),
                    "-t", f"{(end_frame - start_frame) / 24:.3f}",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    str(seg_path),
                ])
                responses.append(
                    AgentResponse(
                        success=True,
                        data=VideoResult(
                            scene_id=req.scene_id,
                            video_url=f"{base_url}/static/video/{seg_name}",
                            duration_seconds=(end_frame - start_frame) // 24,
                        ).model_dump(),
                        elapsed_seconds=time.time() - start,
                    )
                )
        report(100, f"H3 多镜联合生成完成（{len(responses)} 场景）")
        return responses

    @staticmethod
    def _merge_group_reference_images(requests: list[VideoRequest]) -> list[str]:
        """合并组内各场景参考图：跨场景去重、保序、过滤空串。"""
        return VideoAgent._dedupe_reference_urls(
            [url for req in requests for url in req.reference_images]
        )

    async def _download_to_file(self, url: str, dest: Path) -> Path:
        """下载远端视频到本地文件（复用基类 httpx 客户端，trust_env=False）。"""
        resp = await self.http.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest

    async def _execute_h3_with_style_qc(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """M18.4 检测+纠偏层：H3 单镜产出的 VLM 画风漂移检测与纠偏包装。

        检测：抽取产出视频中点帧送 VLM 比对目标画风（request.style 为基准）；
        纠偏：漂移时前置强化画风子句并换 seed 重提交，最多
        h3_style_qc_max_retries 次，重试耗尽放行最后结果（纠偏不阻断生产）。
        fail-open：开关关闭 / style 为空 / VLM 未配置 / VLM 异常 / 坏 JSON
        一律放行首次结果。多镜组视频重生成成本高（10-20 分钟/组），
        组级漂移由约束层（build_multishot_prompt 逐镜锚定）治理，不做组级 QC。
        """
        resp = await self._execute_via_h3(request, progress_callback)
        if (
            not settings.h3_style_qc_enabled
            or not settings.visual_model_url
            or not (request.style or "").strip()
            or not resp.success
        ):
            return resp

        max_retries = max(0, settings.h3_style_qc_max_retries)
        current = request
        for attempt in range(max_retries + 1):
            passed, reason = await self._h3_style_qc_check(
                resp.data["video_url"], current.style
            )
            if passed:
                return resp
            if attempt >= max_retries:
                logger.warning(
                    "H3 画风质检重试耗尽，放行最后结果: scene_id=%s reason=%s",
                    request.scene_id, reason,
                )
                return resp
            logger.warning(
                "H3 画风漂移，强化锚定换 seed 重生成 (%d/%d): scene_id=%s reason=%s",
                attempt + 1, max_retries, request.scene_id, reason,
            )
            if progress_callback:
                progress_callback(
                    96, f"画风漂移（{reason}），强化锚定重生成 {attempt + 1}/{max_retries}"
                )
            strengthened = strengthen_h3_style_clause(
                current.prompt or "cinematic, high quality, smooth motion",
                current.style,
            )
            current = current.model_copy(update={"prompt": strengthened})
            resp = await self._execute_via_h3(current, progress_callback)
            if not resp.success:
                return resp
        return resp  # pragma: no cover — 循环各分支（含末次迭代）必经 1337/1343/1359 return，结构不可达

    async def _h3_style_qc_check(self, video_url: str, style: str) -> tuple[bool, str]:
        """H3 产出画风单点判定：中点帧 + 目标画风送 VLM，返回 (是否合格, 原因)。

        fail-open：帧抽取失败 / VLM 异常 / 返回非 JSON / 结构不符均视为合格放行
        （质检器故障不阻断生产）。
        """
        try:
            frame_bytes = await self._extract_h3_middle_frame(video_url)
            encoded = base64.b64encode(frame_bytes).decode("utf-8")
            anchor = resolve_style_anchor(style)
            text = (
                "这是一帧 AI 生成视频的画面。请判定它的视觉画风是否符合目标画风：\n"
                f"目标画风：{style}（{anchor.style_name_en}）。\n"
                "判定要点：线条/上色/质感/光影的整体渲染风格是否与目标画风一致；"
                "画面内容、构图、人物外貌不作为判定依据。\n"
                '只输出 JSON：{"pass": true/false, "reason": "漂移时简述实际画风，合格时填空串"}。'
                "不要 markdown 代码块，不要解释。"
            )
            result = await self._get_vlm_client().chat.completions.create(
                model=settings.visual_model_name,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{encoded}",
                                "detail": "high",
                            },
                        },
                    ],
                }],
                temperature=0.1,
                max_tokens=300,
            )
            raw = (result.choices[0].message.content or "").strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json_repair.loads(raw)
            if not isinstance(data, dict) or "pass" not in data:
                logger.warning("H3 画风质检返回结构异常，fail-open 放行: %s", raw[:120])
                return True, ""
            return bool(data["pass"]), str(data.get("reason") or "")
        except Exception as e:
            logger.warning("H3 画风质检异常，fail-open 放行: %s", e)
            return True, ""

    async def _extract_h3_middle_frame(self, video_url: str) -> bytes:
        """下载 H3 产出视频并用 ffmpeg 抽取中点帧（PNG bytes）。"""
        with tempfile.TemporaryDirectory(prefix="h3_style_qc_") as td:
            video_path = Path(td) / "in.mp4"
            frame_path = Path(td) / "mid.png"
            await self._download_to_file(video_url, video_path)

            probe = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await probe.communicate()
            try:
                duration = float(out.decode().strip())
            except ValueError:
                duration = 0.0
            mid = max(0.0, duration / 2)

            extract = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-ss", f"{mid:.3f}", "-i", str(video_path),
                "-frames:v", "1", "-f", "image2", str(frame_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await extract.communicate()
            return frame_path.read_bytes()

    async def _execute_via_h3(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """MiniMax H3 调度：任一参考资产（图/视频/音频）非空走 ref2va，否则 fl2va。

        M17.4：ref2va 触发条件从 reference_images 扩展到全模态参考（H3 原生支持
        图片/视频/音频三类参考资产，总预算 12 文件）。
        ref2va 失败时先回退同实例 fl2va（仍享 H3 原生音频），
        fl2va 再失败由 execute 外层统一回退 ComfyUI/Wan 2.2。
        """
        if request.reference_images or request.reference_videos or request.reference_audios:
            try:
                return await self._execute_via_h3_r2v(request, progress_callback)
            except Exception as r2v_err:
                logger.warning(
                    "H3 ref2va 生成失败，回退 fl2va i2v: scene_id=%s err=%s",
                    request.scene_id, r2v_err,
                )
                if progress_callback:
                    progress_callback(20, f"ref2va 失败，回退 fl2va: {r2v_err}")
        return await self._execute_via_h3_fl2va(request, progress_callback)

    async def _execute_via_h3_fl2va(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """MiniMax H3 fl2va 推理路径（分镜关键帧 → 带原生音频的视频）。

        专用独立 ComfyUI 实例（经本地模型网关 video_h3 能力解析），不走 LB：
        H3 模型只部署在该实例上，且 GPU1 显存接近满载需独占调度。
        """
        start = time.time()

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        worker_url = model_gateway.endpoint("video_h3")

        _report(5, "上传分镜图片到 H3 ComfyUI")
        input_image_name = await self.upload_image_to_comfyui(
            worker_url, request.image_url
        )

        _report(15, "构建 MiniMax H3 fl2va 工作流")
        import random

        # H3 为单条件蒸馏模型：无负面提示词（官方模板无 negative 输入）
        # M18.4 约束层：场景 prompt 画风冲突清洗 + 幂等风格锚定尾
        positive = apply_h3_style_anchor(
            request.prompt or "cinematic, high quality, smooth motion", request.style
        )
        num_frames = _snap_h3_frames(request.duration_seconds)

        # M17.3 FL2VA 首帧+末帧双锚定：末帧非空时上传并前置官方对齐指令
        last_frame_name = ""
        if request.last_frame_url:
            _report(10, "上传 FL2VA 末帧到 H3 ComfyUI")
            last_frame_name = await self.upload_image_to_comfyui(
                worker_url, request.last_frame_url
            )
            positive = (
                f"{build_fl2va_alignment_instruction(request.duration_seconds)}\n"
                f"{positive}"
            )

        # M17.2 单镜原生音频方向（节拍 → soundscape/music 字段，置 prompt 最末）
        positive = _append_audio_direction(positive, [request.narrative_beat])

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE_H3))
        workflow["1"]["inputs"]["unet_name"] = settings.h3_unet_name
        workflow["2"]["inputs"]["clip_name"] = settings.h3_clip_name
        workflow["3"]["inputs"]["vae_name"] = settings.h3_video_vae_name
        workflow["4"]["inputs"]["vae_name"] = settings.h3_audio_vae_name
        workflow["10"]["inputs"]["image"] = input_image_name
        if last_frame_name:
            # 末帧 LoadImage 节点 11 → MiniMaxH3ImageToVideo.last_frame（optional IMAGE）
            workflow["11"] = {
                "class_type": "LoadImage",
                "inputs": {"image": last_frame_name},
            }
            workflow["20"]["inputs"]["last_frame"] = ["11", 0]
        workflow["20"]["inputs"]["prompt"] = positive
        workflow["20"]["inputs"]["width"] = settings.h3_width
        workflow["20"]["inputs"]["height"] = settings.h3_height
        workflow["20"]["inputs"]["length"] = num_frames
        workflow["30"]["inputs"]["noise_seed"] = random.randint(0, 2**32 - 1)
        workflow["31"]["inputs"]["sampler_name"] = settings.h3_sampler
        workflow["32"]["inputs"]["scheduler"] = settings.h3_scheduler
        workflow["32"]["inputs"]["steps"] = settings.h3_steps
        _apply_h3_turbo_to_workflow(workflow)
        workflow["60"]["inputs"]["filename_prefix"] = f"video_scene_{request.scene_id}"

        return await self._submit_h3_workflow(
            worker_url, workflow, request.scene_id, num_frames, start, _report
        )

    async def _execute_via_h3_r2v(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """MiniMax H3 ref2va 推理路径（全模态参考生成，跨分镜角色一致性）。

        分镜关键帧上传后作为第 1 张参考图（构图参考，ref_image_0），
        request.reference_images 去重保序后逐张上传作为角色外观参考
        （ref_image_1..N），参考图总数（含关键帧）不超过 h3_ref_max_images。
        M17.4：reference_videos（≤h3_ref_max_videos）经 LoadVideo→GetVideoComponents
        挂接 ref_videos/ref_video_audios 组（帧序列+原声音轨），reference_audios
        （≤h3_ref_max_audios）经 LoadAudio 挂接 ref_audios 组。
        """
        start = time.time()

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        worker_url = model_gateway.endpoint("video_h3")

        # 角色参考图：去重、保序、过滤空串；关键帧占 1 席，故角色图最多 max-1 张
        ref_urls = self._dedupe_reference_urls(request.reference_images)
        ref_urls = ref_urls[: max(0, settings.h3_ref_max_images - 1)]
        # M17.4 音视频参考：去重保序 + 节点组上限（各 3）
        ref_videos = self._dedupe_reference_urls(request.reference_videos)
        ref_videos = ref_videos[: max(0, settings.h3_ref_max_videos)]
        ref_audios = self._dedupe_reference_urls(request.reference_audios)
        ref_audios = ref_audios[: max(0, settings.h3_ref_max_audios)]

        _report(5, f"上传关键帧与 {len(ref_urls)} 张角色参考图到 H3 ComfyUI")
        keyframe_name = await self.upload_image_to_comfyui(
            worker_url, request.image_url
        )
        ref_names: list[str] = []
        for url in ref_urls:
            ref_names.append(await self.upload_image_to_comfyui(worker_url, url))

        _report(15, "构建 MiniMax H3 ref2va 工作流")
        import random

        num_frames = _snap_h3_frames(request.duration_seconds)

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE_H3_R2V))
        workflow["1"]["inputs"]["unet_name"] = settings.h3_ref_unet_name
        workflow["2"]["inputs"]["clip_name"] = settings.h3_clip_name
        workflow["3"]["inputs"]["vae_name"] = settings.h3_video_vae_name
        workflow["4"]["inputs"]["vae_name"] = settings.h3_audio_vae_name
        workflow["10"]["inputs"]["image"] = keyframe_name
        workflow["20"]["inputs"]["width"] = settings.h3_width
        workflow["20"]["inputs"]["height"] = settings.h3_height
        workflow["20"]["inputs"]["length"] = num_frames
        workflow["20"]["inputs"]["ref_image_size"] = settings.h3_ref_image_size
        # 角色参考图动态挂接：LoadImage 节点 11/12/... → ref_images 组内 ref_image_1/2/...
        # （COMFY_AUTOGROW_V3 API 格式为嵌套 dict，扁平键执行期 TypeError）
        ref_group = workflow["20"]["inputs"].setdefault("ref_images", {})
        for idx, name in enumerate(ref_names, start=1):
            node_id = str(10 + idx)
            workflow[node_id] = {
                "class_type": "LoadImage",
                "inputs": {"image": name},
            }
            ref_group[f"ref_image_{idx}"] = [node_id, 0]

        # M17.4 音视频参考注入（LoadVideo/GetVideoComponents/LoadAudio 节点 7X/8X/9X）
        if ref_videos or ref_audios:
            _report(18, f"上传 {len(ref_videos)} 个参考视频 / {len(ref_audios)} 个参考音频")
        n_videos, n_audios = await self._inject_r2v_media_refs(
            workflow, worker_url, ref_videos, ref_audios
        )

        # 官方模板实践：prompt 中显式说明参考资产用途（构图关键帧 + 角色外观锁定
        # + 音视频参考标签），音频方向字段置最末（官方 prompt 结构约定）
        # M18.4 约束层：锚定作用于场景 prompt 本体，风格尾位于参考图引导语之前
        positive = apply_h3_style_anchor(
            request.prompt or "cinematic, high quality, smooth motion", request.style
        )
        positive = positive + H3_R2V_PROMPT_GUIDE + build_r2v_media_guide(n_videos, n_audios)
        positive = _append_audio_direction(positive, [request.narrative_beat])
        workflow["20"]["inputs"]["prompt"] = positive
        workflow["30"]["inputs"]["noise_seed"] = random.randint(0, 2**32 - 1)
        workflow["31"]["inputs"]["sampler_name"] = settings.h3_sampler
        workflow["32"]["inputs"]["scheduler"] = settings.h3_scheduler
        workflow["32"]["inputs"]["steps"] = settings.h3_steps
        _apply_h3_turbo_to_workflow(workflow)
        workflow["60"]["inputs"]["filename_prefix"] = f"video_scene_{request.scene_id}"

        return await self._submit_h3_workflow(
            worker_url, workflow, request.scene_id, num_frames, start, _report
        )

    async def _inject_r2v_media_refs(
        self,
        workflow: dict[str, Any],
        worker_url: str,
        video_urls: list[str],
        audio_urls: list[str],
    ) -> tuple[int, int]:
        """M17.4 向 ref2va 工作流注入参考视频/独立音频并挂接 autogrow 组。

        参考视频：LoadVideo（节点 7X）→ GetVideoComponents（节点 8X），
        帧序列挂 ref_videos.ref_video_N，原声音轨挂 ref_video_audios.ref_video_audio_N
        （官方契约：同编号参考视频的原声，默认一并挂接充分释放全模态能力）。
        独立音频：LoadAudio（节点 9X）挂 ref_audios.ref_audio_N。
        返回实际挂接的 (视频数, 音频数)，供 prompt 标签引导使用。
        """
        node_inputs = workflow["20"]["inputs"]
        n_videos = 0
        for idx, url in enumerate(video_urls):
            name = await self.upload_media_to_comfyui(
                worker_url, url, fallback_name=f"ref_video_{idx}.mp4"
            )
            load_id, comp_id = f"7{idx}", f"8{idx}"
            workflow[load_id] = {
                "class_type": "LoadVideo",
                "inputs": {"file": name},
            }
            workflow[comp_id] = {
                "class_type": "GetVideoComponents",
                "inputs": {"video": [load_id, 0]},
            }
            node_inputs.setdefault("ref_videos", {})[f"ref_video_{idx}"] = [comp_id, 0]
            node_inputs.setdefault("ref_video_audios", {})[f"ref_video_audio_{idx}"] = [comp_id, 1]
            n_videos += 1
        n_audios = 0
        for idx, url in enumerate(audio_urls):
            name = await self.upload_media_to_comfyui(
                worker_url, url, fallback_name=f"ref_audio_{idx}.mp3"
            )
            load_id = f"9{idx}"
            workflow[load_id] = {
                "class_type": "LoadAudio",
                "inputs": {"audio": name},
            }
            node_inputs.setdefault("ref_audios", {})[f"ref_audio_{idx}"] = [load_id, 0]
            n_audios += 1
        return n_videos, n_audios

    @staticmethod
    def _dedupe_reference_urls(urls: list[str]) -> list[str]:
        """参考图 URL 去重（保序）并过滤空串。"""
        seen: set[str] = set()
        out: list[str] = []
        for url in urls:
            url = (url or "").strip()
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out

    async def _submit_h3_workflow(
        self,
        worker_url: str,
        workflow: dict[str, Any],
        scene_id: int,
        num_frames: int,
        start: float,
        report: Callable[[int, str], None],
    ) -> AgentResponse:
        """提交 H3 工作流并轮询结果（fl2va/ref2va 共用的提交-等待-提取尾部）。"""
        report(25, "提交 H3 视频生成任务")
        result = await self.call_comfyui(worker_url, workflow)
        prompt_id = result.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError(f"H3 ComfyUI 未返回 prompt_id: {result}")

        report(30, "H3 采样中（33B 模型，单场景约 5-15 分钟）")
        outputs = await self.get_comfyui_result(
            worker_url, prompt_id, timeout=settings.h3_result_timeout
        )
        report(95, "提取生成的视频")
        video_url = self._extract_video_url(outputs, worker_url, scene_id)

        result_data = VideoResult(
            scene_id=scene_id,
            video_url=video_url,
            duration_seconds=num_frames // 24,
        )

        report(100, "H3 视频生成完成（含原生音频）")
        return AgentResponse(
            success=True,
            data=result_data.model_dump(),
            elapsed_seconds=time.time() - start,
        )

    async def _execute_via_comfyui(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
        worker_url: str | None = None,
    ) -> AgentResponse:
        """ComfyUI/Wan 2.2 I2V 推理路径（原 execute 逻辑，作为回退）。"""
        start = time.time()

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        if worker_url is None:
            worker_url = await self.get_available_video_worker()

        _report(5, "上传分镜图片到 ComfyUI")
        input_image_name = await self.upload_image_to_comfyui(
            worker_url, request.image_url
        )

        _report(15, "构建 Wan 2.2 I2V 工作流")
        import random

        positive = request.prompt or "cinematic, high quality, smooth motion"
        negative = request.negative_prompt or "blurry, low quality, distorted"

        num_frames = max(21, request.duration_seconds * 24 + 1)
        num_frames = ((num_frames - 1) // 4) * 4 + 1

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE))
        workflow["20"]["inputs"]["image"] = input_image_name
        workflow["12"]["inputs"]["text"] = positive
        workflow["13"]["inputs"]["text"] = negative
        workflow["21"]["inputs"]["length"] = num_frames
        seed = random.randint(0, 2**32 - 1)
        # 高/低噪声双采样器必须同 seed，保证两阶段去噪连贯
        workflow["30"]["inputs"]["noise_seed"] = seed
        workflow["31"]["inputs"]["noise_seed"] = seed
        workflow["50"]["inputs"]["filename_prefix"] = f"video_scene_{request.scene_id}"

        _report(25, "提交视频生成任务")
        result = await self.call_comfyui(worker_url, workflow)
        prompt_id = result.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id: {result}")

        _report(30, "视频采样中，预计 5-10 分钟")
        outputs = await self.get_comfyui_result(worker_url, prompt_id, timeout=600.0)
        _report(95, "提取生成的视频")
        video_url = self._extract_video_url(outputs, worker_url, request.scene_id)

        result_data = VideoResult(
            scene_id=request.scene_id,
            video_url=video_url,
            duration_seconds=num_frames // 24,
        )

        _report(100, "视频生成完成")
        return AgentResponse(
            success=True,
            data=result_data.model_dump(),
            elapsed_seconds=time.time() - start,
        )

    async def batch_execute(
        self,
        request: VideoBatchRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """批量并行生成视频片段，自动将任务分散到多个视频 GPU。

        P3.1 优化：
        - asyncio.Semaphore 限制并发度（video_max_concurrency），避免单实例队列堆积
        - 透传 progress_callback，按场景聚合进度上报
        - worker 故障转移：单场景失败时换一个 worker 重试一次
        """
        start = time.time()
        results: list[VideoResult] = []
        failed: list[int] = []

        # 预先按当前负载为每个任务分配 Worker，避免并发查询时全部选中同一 GPU
        workers = await self.get_available_video_workers(len(request.items))

        # 并发度上限：与视频 worker 数对齐，过高会压垮单实例 ComfyUI 队列
        max_concurrent = max(1, settings.video_max_concurrency)
        sem = asyncio.Semaphore(max_concurrent)
        total = len(request.items)
        completed = 0

        async def _generate_one(
            item: VideoRequest, worker_url: str, scene_idx: int
        ) -> VideoResult | None:
            nonlocal completed

            # 每场景独立 progress，聚合到批次进度
            def scene_progress(percent: int, message: str) -> None:
                if progress_callback:
                    # 批次整体进度 = 已完成场景数 + 当前场景进度占比
                    batch_percent = int(
                        (completed + percent / 100.0) / total * 100
                    )
                    progress_callback(
                        batch_percent,
                        f"场景 {item.scene_id} ({scene_idx + 1}/{total}): {message}",
                    )

            async with sem:
                resp = await self.execute(
                    item, progress_callback=scene_progress, worker_url=worker_url
                )
                if resp.success and resp.data:
                    completed += 1
                    return VideoResult(**resp.data)

                # worker 故障转移：换一个不同的 worker 重试一次
                logger.warning(
                    "视频批量生成失败(首次), 尝试故障转移: scene_id=%s worker=%s error=%s",
                    item.scene_id, worker_url, resp.error,
                )
                alt_worker = await self._pick_alternate_worker(worker_url)
                if alt_worker:
                    resp2 = await self.execute(
                        item,
                        progress_callback=scene_progress,
                        worker_url=alt_worker,
                    )
                    if resp2.success and resp2.data:
                        completed += 1
                        logger.info(
                            "故障转移成功: scene_id=%s -> worker=%s",
                            item.scene_id, alt_worker,
                        )
                        return VideoResult(**resp2.data)
                logger.warning(
                    "视频批量生成失败(故障转移后仍失败): scene_id=%s",
                    item.scene_id,
                )
                return None

        tasks = [
            _generate_one(item, workers[idx], idx)
            for idx, item in enumerate(request.items)
        ]
        outputs = await asyncio.gather(*tasks, return_exceptions=True)

        for item, out in zip(request.items, outputs):
            if isinstance(out, Exception):
                logger.warning("视频批量生成异常: scene_id=%s error=%s", item.scene_id, out)
                failed.append(item.scene_id)
            elif out is None:
                failed.append(item.scene_id)
            else:
                results.append(out)

        if progress_callback:
            progress_callback(100, f"批量完成: {len(results)}/{total} 成功")

        return AgentResponse(
            success=True,
            data=VideoBatchResult(
                results=results,
                failed_scenes=failed,
            ).model_dump(),
            elapsed_seconds=time.time() - start,
        )

    async def _pick_alternate_worker(self, failed_url: str) -> str | None:
        """选择一个与 failed_url 不同的可用视频 worker；都相同时返回 None。"""
        candidates = model_gateway.endpoints("video_comfy")
        alternates = [u for u in candidates if u != failed_url]
        if not alternates:
            return None
        loads = await self._get_worker_loads(alternates)
        if not loads:
            return alternates[0]
        return self._select_workers_by_load(loads, 1)[0]

    def _extract_video_url(
        self, outputs: dict[str, Any], worker_url: str, scene_id: int
    ) -> str:
        """从 ComfyUI 输出中提取视频 URL。"""
        for node_id, node_output in outputs.items():
            if "videos" in node_output:
                vid_info = node_output["videos"][0]
                filename = vid_info["filename"]
                subfolder = vid_info.get("subfolder", "")
                return f"{worker_url}/view?filename={filename}&subfolder={subfolder}&type=output"
            if "gifs" in node_output:
                vid_info = node_output["gifs"][0]
                filename = vid_info["filename"]
                subfolder = vid_info.get("subfolder", "")
                return f"{worker_url}/view?filename={filename}&subfolder={subfolder}&type=output"
            if "images" in node_output:
                img_info = node_output["images"][0]
                filename = img_info["filename"]
                subfolder = img_info.get("subfolder", "")
                return f"{worker_url}/view?filename={filename}&subfolder={subfolder}&type=output"

        raise RuntimeError(f"未找到生成的视频: {outputs}")


video_agent = VideoAgent()
