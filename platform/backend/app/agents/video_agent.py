"""视频 Agent — 分镜关键帧图片 → 视频片段。

P4.1 升级：xDiT (HunyuanVideo 1.5, 4 卡并行) 为主，ComfyUI (Wan 2.2) 为回退。

后端选择由 settings.video_backend 控制：
- 'xdit' (默认): HunyuanVideo 1.5 + xDiT 多卡并行，单场景 45-70s
- 'comfyui': Wan 2.2 I2V 单卡，作为回退

xDiT 主路径失败时自动回退到 ComfyUI（保留原 Wan 2.2 工作流）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    VideoBatchRequest,
    VideoBatchResult,
    VideoRequest,
    VideoResult,
)
from app.services.xdit_service import XDiTService

logger = logging.getLogger(__name__)

# Wan 2.2 I2V 工作流模板
# 节点说明：
#   1: WanVideoModelLoader - 加载 Wan 2.2 I2V 模型
#   2: WanVideoVAELoader - 加载 VAE
#   3: LoadImage - 加载分镜关键帧图片
#   5: WanVideoImageToVideoEncode - 图片编码为视频 latent
#   6: WanVideoSampler - 采样生成视频
#   7: WanVideoDecode - 解码视频
#   8: VHS_VideoCombine - 保存为 MP4
#  11: CLIPLoader - 原生 CLIP 加载器（type=wan，支持 fp8 scaled UMT5）
#  12: CLIPTextEncode - 正面提示词编码
#  13: CLIPTextEncode - 反向提示词编码
#  14: WanVideoTextEmbedBridge - 桥接 CONDITIONING → WANVIDEOTEXTEMBEDS
WORKFLOW_TEMPLATE = {
    "1": {
        "class_type": "WanVideoModelLoader",
        "inputs": {
            "model": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            "base_precision": "bf16",
            "quantization": "disabled",
            "load_device": "main_device",
        }
    },
    "2": {
        "class_type": "WanVideoVAELoader",
        "inputs": {
            "model_name": "wan_2.1_vae.safetensors",
            "precision": "bf16",
        }
    },
    "3": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "{input_image_name}"
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
    "14": {
        "class_type": "WanVideoTextEmbedBridge",
        "inputs": {
            "positive": ["12", 0],
            "negative": ["13", 0],
        }
    },
    "5": {
        "class_type": "WanVideoImageToVideoEncode",
        "inputs": {
            "width": 480,
            "height": 832,
            "num_frames": 81,
            "noise_aug_strength": 0.0,
            "start_latent_strength": 1.0,
            "end_latent_strength": 1.0,
            "force_offload": True,
            "vae": ["2", 0],
            "start_image": ["3", 0],
        }
    },
    "6": {
        "class_type": "WanVideoSampler",
        "inputs": {
            "model": ["1", 0],
            "image_embeds": ["5", 0],
            "text_embeds": ["14", 0],
            "steps": 20,
            "cfg": 6.0,
            "shift": 5.0,
            "seed": 0,
            "force_offload": True,
            "scheduler": "unipc",
            "riflex_freq_index": 0,
        }
    },
    "7": {
        "class_type": "WanVideoDecode",
        "inputs": {
            "vae": ["2", 0],
            "samples": ["6", 0],
            "enable_vae_tiling": True,
            "tile_x": 272,
            "tile_y": 272,
            "tile_stride_x": 144,
            "tile_stride_y": 128,
        }
    },
    "8": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["7", 0],
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
    - 'xdit' (默认): HunyuanVideo 1.5 + xDiT 多卡并行，单场景 45-70s
    - 'comfyui': Wan 2.2 I2V 单卡（回退路径）

    xDiT 主路径失败时自动回退到 ComfyUI（保留原 Wan 2.2 工作流）。
    """

    def __init__(self):
        super().__init__("video_agent")
        # 懒加载 xDiT 客户端，避免在测试 mock 阶段就建立 httpx 连接
        self._xdit: XDiTService | None = None

    @property
    def xdit_service(self) -> XDiTService:
        """懒加载 XDiTService，复用 BaseAgent 的 httpx 客户端（trust_env=False）。"""
        if self._xdit is None:
            self._xdit = XDiTService(http_client=self.http)
        return self._xdit

    async def execute(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
        worker_url: str | None = None,
    ) -> AgentResponse:
        """根据 settings.video_backend 派发到 xDiT 或 ComfyUI。

        xDiT 失败时自动回退到 ComfyUI（仅当 backend=='xdit' 时启用回退）。
        """
        start = time.time()
        backend = settings.video_backend.lower()

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        try:
            if backend == "xdit":
                _report(5, "xDiT/HunyuanVideo 1.5 多卡并行推理")
                return await self._execute_via_xdit(request, progress_callback)
            # backend == 'comfyui' 或其它未知值 → 走 ComfyUI 原路径
            return await self._execute_via_comfyui(
                request, progress_callback, worker_url
            )
        except Exception as xdit_err:
            # 仅当主后端是 xdit 时尝试回退到 ComfyUI
            if backend != "xdit":
                return AgentResponse(
                    success=False,
                    error=f"视频生成失败: {xdit_err}",
                    elapsed_seconds=time.time() - start,
                )
            logger.warning(
                "xDiT 视频生成失败，回退到 ComfyUI/Wan 2.2: scene_id=%s err=%s",
                request.scene_id, xdit_err,
            )
            _report(50, f"xDiT 失败，回退 ComfyUI: {xdit_err}")
            try:
                return await self._execute_via_comfyui(
                    request, progress_callback, worker_url
                )
            except Exception as comfyui_err:
                return AgentResponse(
                    success=False,
                    error=(
                        f"视频生成失败(xdit+comfyui 均失败): "
                        f"xdit={xdit_err}; comfyui={comfyui_err}"
                    ),
                    elapsed_seconds=time.time() - start,
                )

    async def _execute_via_xdit(
        self,
        request: VideoRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """xDiT/HunyuanVideo 1.5 多卡并行推理路径。"""
        start = time.time()
        positive = request.prompt or "cinematic, high quality, smooth motion"
        negative = request.negative_prompt or "blurry, low quality, distorted"

        result = await self.xdit_service.generate_video(
            image_url=request.image_url,
            prompt=positive,
            negative_prompt=negative,
            scene_id=request.scene_id,
            duration_seconds=request.duration_seconds,
            progress_callback=progress_callback,
        )
        video_url = result["video_url"]
        duration = int(result.get("duration_seconds") or request.duration_seconds)

        result_data = VideoResult(
            scene_id=request.scene_id,
            video_url=video_url,
            duration_seconds=duration,
        )
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
        workflow["3"]["inputs"]["image"] = input_image_name
        workflow["12"]["inputs"]["text"] = positive
        workflow["13"]["inputs"]["text"] = negative
        workflow["5"]["inputs"]["num_frames"] = num_frames
        workflow["6"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)
        workflow["8"]["inputs"]["filename_prefix"] = f"video_scene_{request.scene_id}"

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
        candidates = [settings.comfyui_video_a, settings.comfyui_video_b]
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
