"""LTX-2.5 视频服务 — workstation GPU0 专用 ComfyUI 实例（:8198）的 T2V/I2V/FLF2V 封装。

与生产 LB（:8188）隔离，不入 LB 池；与 MiniMax H3（:8195）形成双引擎路由——
对白/角色一致性镜头走 H3，空镜/动作/长场景/快速预览走 LTX-2.5（25fps 音画同出，
音频与画面 1:1 对齐）。

✅ 实机核验（2026-08-16，:8198 /object_info + DashBox local_gateway 同链路已出片）：
- 真实可用链路为单阶段：CheckpointLoaderSimple + LTXAVTextEncoderLoader
  （text_encoder + ckpt_name 双输入）→ CLIPTextEncode →
  EmptyLTXVLatentVideo + LTXVEmptyLatentAudio → LTXVConcatAVLatent →
  **KSampler**（euler_ancestral_cfg_pp / simple / CFG=1 / 8 步）→
  LTXVSeparateAVLatent → VAEDecode + LTXVAudioVAEDecode → CreateVideo → SaveVideo
- :8198 无 LTXVBaseSampler / LTXVLatentUpscale 节点（两阶段离线写法已废弃）；
  无 VAEDecodeAudio（音频解码走 LTXVAudioVAEDecode）；
  EmptyLTXVLatentVideo 无 fps 输入（仅 width/height/length/batch_size）
- I2V：LTXVImgToVideo 接管 KSampler positive/negative + concat 视频侧 latent；
  FLF2V 末帧走 LTXVAddGuide frame_idx=length-1（LTXVImgToVideo 无 last_image 输入）
- 真实权重文件名见下方 LTX25_*_NAME 常量（nvfp4 蒸馏 transformer /
  gemma4-12b-with-proj int8 / 双 VAE bf16）
- 帧数必须 %8==1；宽高须 32 倍数
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import AgentResponse, VideoResult
from app.services.model_gateway import model_gateway

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 集中常量（实机核验点，见模块 docstring）
# ---------------------------------------------------------------------------

# 权重文件名（workstation :8198 本地 models 目录，2026-08-16 实机核验）
LTX25_TRANSFORMER_NAME = "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors"
LTX25_TEXT_ENCODER_NAME = "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
LTX25_VIDEO_VAE_NAME = "ltx-2.5-video-vae-bf16.safetensors"
LTX25_AUDIO_VAE_NAME = "ltx-2.5-audio-vae-bf16.safetensors"

# 节点 class_type（2026-08-16 :8198 /object_info 核验存在）
LTX25_NODE_MODEL_LOADER = "CheckpointLoaderSimple"
LTX25_NODE_TEXT_ENCODER_LOADER = "LTXAVTextEncoderLoader"
LTX25_NODE_VAE_LOADER = "VAELoader"
LTX25_NODE_TEXT_ENCODE = "CLIPTextEncode"
LTX25_NODE_EMPTY_LATENT = "EmptyLTXVLatentVideo"
LTX25_NODE_EMPTY_AUDIO = "LTXVEmptyLatentAudio"
LTX25_NODE_CONCAT_AV = "LTXVConcatAVLatent"
LTX25_NODE_SAMPLER = "KSampler"
LTX25_NODE_SEPARATE_AV = "LTXVSeparateAVLatent"
LTX25_NODE_VAE_DECODE = "VAEDecode"
LTX25_NODE_AUDIO_VAE_DECODE = "LTXVAudioVAEDecode"
LTX25_NODE_CREATE_VIDEO = "CreateVideo"
LTX25_NODE_SAVE_VIDEO = "SaveVideo"
LTX25_NODE_LOAD_IMAGE = "LoadImage"
LTX25_NODE_IMG_TO_VIDEO = "LTXVImgToVideo"
LTX25_NODE_ADD_GUIDE = "LTXVAddGuide"

# 采样常量（官方蒸馏单阶段）
LTX25_FPS = 25  # 音频 1:1 对齐帧率
LTX25_SAMPLER_NAME = "euler_ancestral_cfg_pp"
LTX25_SCHEDULER = "simple"
LTX25_CFG = 1.0  # 蒸馏模型 CFG=1（负提示词仅作占位，不参与引导）
LTX25_STEPS = 8  # 蒸馏 8 步单阶段全分辨率

# LTX-2.5 帧数网格：%8==1（25fps 下 9/17/25/.../121/... 帧）
def _snap_ltx_frames(n: int | float) -> int:
    """把帧数对齐到 %8==1 网格（向下取整，下限 9 帧）。"""
    n = max(9, int(n))
    return n - ((n - 1) % 8)


def _snap_dim(d: int | float) -> int:
    """把宽/高对齐到 32 的倍数（向下取整，下限 32）。"""
    return max(32, (int(d) // 32) * 32)


# LTX-2.5 distilled 单阶段 T2V 工作流模板（2026-08-16 实机核验链，占位符在执行期注入）
# 节点说明：
#    1: CheckpointLoaderSimple - nvfp4 蒸馏 transformer
#    2: LTXAVTextEncoderLoader - gemma4-12b int8（text_encoder + ckpt_name 双输入）
#    3: VAELoader - 视频 VAE (bf16) / 4: VAELoader - 音频 VAE (bf16)
#   10: CLIPTextEncode - 正面提示词（散文式，见 prompt_expander.LTXProseCompiler）
#   11: CLIPTextEncode - 负面提示词（蒸馏 CFG=1 时为占位）
#   20: EmptyLTXVLatentVideo - 视频空 latent（length %8==1，无 fps 输入）
#   21: LTXVEmptyLatentAudio - 音频空 latent（frames_number + frame_rate=25）
#   22: LTXVConcatAVLatent - 音画 latent 合并
#   30: KSampler - 单阶段全分辨率采样（8 步，CFG=1，euler_ancestral_cfg_pp/simple）
#   45: LTXVSeparateAVLatent - 采样输出拆分音画
#   50: VAEDecode - 视频帧 / 51: LTXVAudioVAEDecode - 音频（双 VAE 解码）
#   60: CreateVideo - 帧+音频 25fps 合成 / 70: SaveVideo - 保存 mp4
# I2V/FLF2V 动态注入：
#   80: LoadImage - 首帧 / 81: LTXVImgToVideo - 接管采样 positive/negative + 22 视频侧 latent
#   82: LoadImage - 末帧 / 83: LTXVAddGuide - FLF2V 末帧锚定（frame_idx=length-1）
WORKFLOW_TEMPLATE_LTX25_T2V: dict[str, Any] = {
    "1": {
        "class_type": LTX25_NODE_MODEL_LOADER,
        "inputs": {"ckpt_name": "{ltx_transformer_name}"},
    },
    "2": {
        "class_type": LTX25_NODE_TEXT_ENCODER_LOADER,
        "inputs": {
            "text_encoder": "{ltx_text_encoder_name}",
            "ckpt_name": "{ltx_transformer_name}",
            "device": "default",
        },
    },
    "3": {
        "class_type": LTX25_NODE_VAE_LOADER,
        "inputs": {"vae_name": "{ltx_video_vae_name}"},
    },
    "4": {
        "class_type": LTX25_NODE_VAE_LOADER,
        "inputs": {"vae_name": "{ltx_audio_vae_name}"},
    },
    "10": {
        "class_type": LTX25_NODE_TEXT_ENCODE,
        "inputs": {"text": "{positive_prompt}", "clip": ["2", 0]},
    },
    "11": {
        "class_type": LTX25_NODE_TEXT_ENCODE,
        "inputs": {"text": "{negative_prompt}", "clip": ["2", 0]},
    },
    "20": {
        "class_type": LTX25_NODE_EMPTY_LATENT,
        "inputs": {"width": 768, "height": 512, "length": 121, "batch_size": 1},
    },
    "21": {
        "class_type": LTX25_NODE_EMPTY_AUDIO,
        "inputs": {
            "frames_number": 121,
            "frame_rate": LTX25_FPS,
            "batch_size": 1,
            "audio_vae": ["4", 0],
        },
    },
    "22": {
        "class_type": LTX25_NODE_CONCAT_AV,
        "inputs": {"video_latent": ["20", 0], "audio_latent": ["21", 0]},
    },
    "30": {
        "class_type": LTX25_NODE_SAMPLER,
        "inputs": {
            "model": ["1", 0],
            "positive": ["10", 0],
            "negative": ["11", 0],
            "latent_image": ["22", 0],
            "seed": 0,
            "steps": LTX25_STEPS,
            "cfg": LTX25_CFG,
            "sampler_name": LTX25_SAMPLER_NAME,
            "scheduler": LTX25_SCHEDULER,
            "denoise": 1.0,
        },
    },
    "45": {
        "class_type": LTX25_NODE_SEPARATE_AV,
        "inputs": {"av_latent": ["30", 0]},
    },
    "50": {
        "class_type": LTX25_NODE_VAE_DECODE,
        "inputs": {"samples": ["45", 0], "vae": ["3", 0]},
    },
    "51": {
        "class_type": LTX25_NODE_AUDIO_VAE_DECODE,
        "inputs": {"samples": ["45", 1], "audio_vae": ["4", 0]},
    },
    "60": {
        "class_type": LTX25_NODE_CREATE_VIDEO,
        "inputs": {"images": ["50", 0], "audio": ["51", 0], "fps": LTX25_FPS},
    },
    "70": {
        "class_type": LTX25_NODE_SAVE_VIDEO,
        "inputs": {
            "video": ["60", 0],
            "filename_prefix": "{video_prefix}",
            "format": "auto",
            "codec": "auto",
        },
    },
}


def _extract_video_url(outputs: dict[str, Any], worker_url: str) -> str:
    """从 ComfyUI 输出中提取视频 URL（与 video_agent 同款宽松提取：videos/gifs/images）。"""
    for node_output in outputs.values():
        for key in ("videos", "gifs", "images"):
            if key in node_output and node_output[key]:
                info = node_output[key][0]
                filename = info["filename"]
                subfolder = info.get("subfolder", "")
                return f"{worker_url}/view?filename={filename}&subfolder={subfolder}&type=output"
    raise RuntimeError(f"未找到生成的视频: {outputs}")


class LTX25VideoService(BaseAgent):
    """LTX-2.5 视频服务：封装 :8198 专用实例的 提交-轮询-提取。

    所有生成方法失败时返回 AgentResponse(success=False) 而非抛异常，
    由调用方（video_agent 路由链）决定是否回退 H3。
    """

    def __init__(self) -> None:
        super().__init__("ltx25_video")

    @staticmethod
    def is_enabled() -> bool:
        """LTX-2.5 引擎总开关（settings.ltx_enabled）。"""
        return bool(settings.ltx_enabled)

    async def generate_t2v(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 768,
        height: int = 512,
        num_frames: int = 121,
        seed: int | None = None,
        scene_id: int = 0,
    ) -> AgentResponse:
        """文生视频（无首帧锚定，纯文本驱动）。"""
        start = time.time()
        try:
            num_frames = _snap_ltx_frames(num_frames)
            workflow = self._build_workflow(
                mode="t2v",
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                num_frames=num_frames,
                seed=seed,
                filename_prefix=f"ltx25_t2v_{scene_id}",
            )
            return await self._submit_and_extract(workflow, scene_id, num_frames, start)
        except Exception as e:
            logger.warning("LTX-2.5 T2V 生成失败: scene_id=%s err=%s", scene_id, e)
            return AgentResponse(
                success=False,
                error=f"LTX-2.5 T2V 生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def generate_i2v(
        self,
        image_url: str,
        prompt: str,
        negative_prompt: str = "",
        width: int = 768,
        height: int = 512,
        num_frames: int = 121,
        seed: int | None = None,
        scene_id: int = 0,
    ) -> AgentResponse:
        """图生视频（首帧单锚定）。"""
        start = time.time()
        try:
            worker_url = model_gateway.endpoint("video_ltx")
            first_name = await self.upload_image_to_comfyui(worker_url, image_url)
            num_frames = _snap_ltx_frames(num_frames)
            workflow = self._build_workflow(
                mode="i2v",
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                num_frames=num_frames,
                seed=seed,
                filename_prefix=f"ltx25_i2v_{scene_id}",
                first_image_name=first_name,
            )
            return await self._submit_and_extract(workflow, scene_id, num_frames, start)
        except Exception as e:
            logger.warning("LTX-2.5 I2V 生成失败: scene_id=%s err=%s", scene_id, e)
            return AgentResponse(
                success=False,
                error=f"LTX-2.5 I2V 生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def generate_flf2v(
        self,
        first_frame_url: str,
        last_frame_url: str,
        prompt: str,
        negative_prompt: str = "",
        width: int = 768,
        height: int = 512,
        num_frames: int = 121,
        seed: int | None = None,
        scene_id: int = 0,
    ) -> AgentResponse:
        """首尾帧双锚定视频（first+last frame to video）。"""
        start = time.time()
        try:
            worker_url = model_gateway.endpoint("video_ltx")
            first_name = await self.upload_image_to_comfyui(worker_url, first_frame_url)
            last_name = await self.upload_image_to_comfyui(worker_url, last_frame_url)
            num_frames = _snap_ltx_frames(num_frames)
            workflow = self._build_workflow(
                mode="flf2v",
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                num_frames=num_frames,
                seed=seed,
                filename_prefix=f"ltx25_flf2v_{scene_id}",
                first_image_name=first_name,
                last_image_name=last_name,
            )
            return await self._submit_and_extract(workflow, scene_id, num_frames, start)
        except Exception as e:
            logger.warning("LTX-2.5 FLF2V 生成失败: scene_id=%s err=%s", scene_id, e)
            return AgentResponse(
                success=False,
                error=f"LTX-2.5 FLF2V 生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    def _build_workflow(
        self,
        *,
        mode: str,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        num_frames: int,
        seed: int | None,
        filename_prefix: str,
        first_image_name: str | None = None,
        last_image_name: str | None = None,
    ) -> dict[str, Any]:
        """按模式构建 LTX-2.5 单阶段工作流（纯本地组装，便于单测与实机校正）。

        全分辨率单次 8 步采样；音频 latent 由节点 21 提供、经 22 与视频 latent
        合并后进入采样器。I2V 注入 LoadImage + LTXVImgToVideo，接管采样器
        positive/negative 与节点 22 的视频侧 latent；FLF2V 再经 LTXVAddGuide
        叠加末帧锚定（LTXVImgToVideo 无 last_image 输入，实机核验）。
        """
        width = _snap_dim(width)
        height = _snap_dim(height)
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE_LTX25_T2V))
        workflow["1"]["inputs"]["ckpt_name"] = LTX25_TRANSFORMER_NAME
        workflow["2"]["inputs"]["text_encoder"] = LTX25_TEXT_ENCODER_NAME
        workflow["2"]["inputs"]["ckpt_name"] = LTX25_TRANSFORMER_NAME
        workflow["3"]["inputs"]["vae_name"] = LTX25_VIDEO_VAE_NAME
        workflow["4"]["inputs"]["vae_name"] = LTX25_AUDIO_VAE_NAME
        workflow["10"]["inputs"]["text"] = prompt
        workflow["11"]["inputs"]["text"] = negative_prompt
        workflow["20"]["inputs"]["width"] = width
        workflow["20"]["inputs"]["height"] = height
        workflow["20"]["inputs"]["length"] = num_frames
        workflow["21"]["inputs"]["frames_number"] = num_frames
        workflow["30"]["inputs"]["seed"] = seed
        workflow["70"]["inputs"]["filename_prefix"] = filename_prefix

        if mode in ("i2v", "flf2v"):
            # 首帧图像条件：LTXVImgToVideo 接管采样 positive/negative + 视频侧 latent
            workflow["80"] = {
                "class_type": LTX25_NODE_LOAD_IMAGE,
                "inputs": {"image": first_image_name},
            }
            workflow["81"] = {
                "class_type": LTX25_NODE_IMG_TO_VIDEO,
                "inputs": {
                    "positive": ["10", 0],
                    "negative": ["11", 0],
                    "vae": ["3", 0],
                    "image": ["80", 0],
                    "width": width,
                    "height": height,
                    "length": num_frames,
                },
            }
            pos_out, neg_out, lat_out = ["81", 0], ["81", 1], ["81", 2]
            if mode == "flf2v":
                # 末帧锚定：LTXVAddGuide frame_idx=length-1（实机 LTXVImgToVideo 无 last_image）
                workflow["82"] = {
                    "class_type": LTX25_NODE_LOAD_IMAGE,
                    "inputs": {"image": last_image_name},
                }
                workflow["83"] = {
                    "class_type": LTX25_NODE_ADD_GUIDE,
                    "inputs": {
                        "positive": pos_out,
                        "negative": neg_out,
                        "latent": lat_out,
                        "vae": ["3", 0],
                        "image": ["82", 0],
                        "frame_idx": num_frames - 1,
                    },
                }
                pos_out, neg_out, lat_out = ["83", 0], ["83", 1], ["83", 2]
            sampler = workflow["30"]["inputs"]
            sampler["positive"] = pos_out
            sampler["negative"] = neg_out
            # 视频侧 latent（含图像条件）替换 concat 的视频输入，音频侧仍由节点 21 提供
            workflow["22"]["inputs"]["video_latent"] = lat_out
        return workflow

    async def _submit_and_extract(
        self,
        workflow: dict[str, Any],
        scene_id: int,
        num_frames: int,
        start: float,
    ) -> AgentResponse:
        """提交 LTX 工作流并轮询结果（T2V/I2V/FLF2V 共用的提交-等待-提取尾部）。"""
        worker_url = model_gateway.endpoint("video_ltx")
        result = await self.call_comfyui(worker_url, workflow)
        prompt_id = result.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError(f"LTX ComfyUI 未返回 prompt_id: {result}")

        outputs = await self.get_comfyui_result(
            worker_url, prompt_id, timeout=settings.ltx_result_timeout
        )
        video_url = _extract_video_url(outputs, worker_url)

        return AgentResponse(
            success=True,
            data=VideoResult(
                scene_id=scene_id,
                video_url=video_url,
                duration_seconds=num_frames // LTX25_FPS,
            ).model_dump(),
            elapsed_seconds=time.time() - start,
        )
