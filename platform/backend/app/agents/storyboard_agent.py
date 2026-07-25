"""分镜 Agent — 剧本场景 → 分镜关键帧图片 + 可选 LTX-Video 预览。

P4.3 升级：HunyuanImage 2.1 / FLUX+PuLID 为主，ComfyUI SDXL 为回退；可选 LTX-Video 预览。

后端选择由 settings.image_backend 控制：
- 'hunyuanimage' (默认): HunyuanImage 2.1 17B FP8，原生 2K + 中文 prompt 最强（场景/分镜首选）
- 'flux_pulid': FLUX.1-dev + PuLID-FLUX v0.9.1，角色 ID 一致性专用
- 'sdxl': ComfyUI majicMIX realistic SDXL（回退路径）

主后端失败时自动回退到 SDXL。

LTX-Video 预览钩子：
- settings.ltx_video_enabled=True 时，分镜图生成成功后自动调用 LTX-Video 2B
- 生成低分辨率 65 帧预览视频（约 2.7s @ 24fps），用户快速判断分镜动态效果
- LTX-Video 预览失败不影响分镜主流程，仅记录 warning 并返回空 preview_video_url

流程：
1. 联网搜索镜头语言/构图参考资料
2. GLM-5.2 根据场景描述 + 角色信息 + 参考资料生成英文图像提示词
3. 按后端派发：
   - hunyuanimage/flux_pulid: 调用图像服务，保存字节到 output/storyboard/
   - sdxl: 提交 ComfyUI 工作流到 Worker
4. 若启用 LTX-Video 预览，调用 LTXVideoService 生成预览视频
5. 返回分镜结果
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import json_repair

from app.agents.ai_optimizer import web_search
from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    Character,
    Scene,
    StoryboardBatchRequest,
    StoryboardBatchResult,
    StoryboardRequest,
    StoryboardResult,
)
from app.services.image_service import FluxPuLIDService, HunyuanImageService
from app.services.ltx_video_service import LTXVideoService

logger = logging.getLogger(__name__)

# 输出目录：保存 HunyuanImage / FLUX+PuLID 返回的分镜图字节
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "storyboard"

PROMPT_SYSTEM = """你是分镜设计专家。根据中文场景描述，生成用于写实风格图像模型的英文提示词。

输出 JSON：
{
  "prompt": "英文正面提示词（含画质关键词、场景、光线、构图、角色动作）",
  "negative_prompt": "英文反向提示词"
}

要求：
- 提示词要包含：场景环境、光线氛围、角色位置与动作、镜头语言（特写/近景/中景/远景）
- 画质关键词：cinematic, 8k UHD, photorealistic, professional photography, film still
- 竖屏 9:16 构图
- JSON 字符串值中的双引号必须用 \\" 转义
- 直接输出纯 JSON，不要用 markdown 代码块包裹
"""

# 强制追加的正面提示词（确保高质量）
POSITIVE_SUFFIX = ", cinematic, 8k UHD, photorealistic, professional photography, film still, best quality, masterpiece, highly detailed, sharp focus, depth of field"

# 强制追加的负面提示词
NEGATIVE_SUFFIX = ", text, watermark, signature, low quality, worst quality, deformed, ugly, blurry, bad anatomy, bad hands, missing fingers, extra digits, cropped, out of frame, duplicate, clone"

# 默认反向提示词（当 LLM 未返回或返回空时使用）
DEFAULT_NEGATIVE_PROMPT = (
    "text, watermark, signature, low quality, worst quality, deformed, ugly, blurry, "
    "bad anatomy, bad hands, missing fingers, extra digits, cropped, out of frame, "
    "duplicate, clone, bad proportions, malformed limbs"
)

# SDXL 9:16 竖屏分镜工作流模板
WORKFLOW_TEMPLATE = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "majicMIX realistic 麦橘写实_v7.safetensors"
        }
    },
    "2": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "{positive_prompt}",
            "clip": ["1", 1]
        }
    },
    "3": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "{negative_prompt}",
            "clip": ["1", 1]
        }
    },
    "4": {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": 1024,
            "height": 1792,
            "batch_size": 1
        }
    },
    "5": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 25,
            "cfg": 7.0,
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
            "denoise": 1.0,
            "model": ["1", 0],
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": ["4", 0]
        }
    },
    "6": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["5", 0],
            "vae": ["1", 2]
        }
    },
    "7": {
        "class_type": "SaveImage",
        "inputs": {
            "images": ["6", 0],
            "filename_prefix": "storyboard_{scene_id}"
        }
    }
}


class StoryboardAgent(BaseAgent):
    """分镜 Agent：LLM 生成提示词 → 图像后端生成关键帧 → 可选 LTX-Video 预览。

    后端选择由 settings.image_backend 控制：
    - 'hunyuanimage' (默认): HunyuanImage 2.1 17B FP8，原生 2K + 中文 prompt 最强
    - 'flux_pulid': FLUX.1-dev + PuLID-FLUX v0.9.1，角色 ID 一致性专用
    - 'sdxl': ComfyUI majicMIX realistic SDXL（回退路径）

    主后端失败时自动回退到 SDXL。

    LTX-Video 预览由 settings.ltx_video_enabled 控制，默认关闭。
    """

    def __init__(self):
        super().__init__("storyboard_agent")
        self._hunyuanimage: HunyuanImageService | None = None
        self._flux_pulid: FluxPuLIDService | None = None
        self._ltx_video: LTXVideoService | None = None

    @property
    def hunyuanimage_service(self) -> HunyuanImageService:
        """懒加载 HunyuanImageService，复用 BaseAgent 的 httpx 客户端。"""
        if self._hunyuanimage is None:
            self._hunyuanimage = HunyuanImageService(http_client=self.http)
        return self._hunyuanimage

    @property
    def flux_pulid_service(self) -> FluxPuLIDService:
        """懒加载 FluxPuLIDService，复用 BaseAgent 的 httpx 客户端。"""
        if self._flux_pulid is None:
            self._flux_pulid = FluxPuLIDService(http_client=self.http)
        return self._flux_pulid

    @property
    def ltx_video_service(self) -> LTXVideoService:
        """懒加载 LTXVideoService，复用 BaseAgent 的 httpx 客户端。"""
        if self._ltx_video is None:
            self._ltx_video = LTXVideoService(http_client=self.http)
        return self._ltx_video

    async def execute(
        self,
        request: StoryboardRequest,
        worker_url: str | None = None,
    ) -> AgentResponse:
        start = time.time()
        try:
            scene = request.scene
            backend = settings.image_backend.lower()

            # AI 优化 step 1：联网搜索镜头语言参考资料
            search_query = f"电影分镜 {scene.shot_type} {scene.emotion} {scene.camera_movement} 构图技巧"
            reference = await web_search(search_query, max_results=3)
            if reference:
                logger.info("分镜 Agent 搜索到参考资料: %d 字符", len(reference))

            # Step 2: 确定英文提示词
            if scene.prompt:
                positive = scene.prompt
                negative = scene.negative_prompt or DEFAULT_NEGATIVE_PROMPT
            else:
                prompts = await self._generate_prompts(scene, request.characters, request.style, reference)
                positive = prompts.get("prompt", "")
                negative = prompts.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
                if not positive:
                    raise RuntimeError("LLM 未返回分镜提示词")

            # Step 3: 按后端派发图像生成
            image_url = await self._dispatch_image_generation(
                backend=backend,
                worker_url=worker_url,
                positive=positive,
                negative=negative,
                scene=scene,
            )

            # Step 4: LTX-Video 预览钩子（可选）
            preview_video_url = ""
            if self.ltx_video_service.is_enabled():
                preview_video_url = await self._generate_ltx_preview(
                    image_url=image_url,
                    scene=scene,
                    positive=positive,
                    negative=negative,
                )

            result = StoryboardResult(
                scene_id=scene.scene_id,
                image_url=image_url,
                prompt_used=positive,
                preview_video_url=preview_video_url,
            )

            return AgentResponse(
                success=True,
                data=result.model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"分镜生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def batch_execute(self, request: StoryboardBatchRequest) -> AgentResponse:
        """批量并行生成分镜关键帧，自动将任务分散到多个图像 GPU。"""
        start = time.time()
        results: list[StoryboardResult] = []
        failed: list[int] = []

        backend = settings.image_backend.lower()
        # 仅 SDXL 路径需要预分配 ComfyUI Worker
        workers: list[str] | None = None
        if backend == "sdxl":
            workers = await self.get_available_image_workers(len(request.scenes))

        async def _generate_one(scene: Scene, worker_url: str | None) -> StoryboardResult | None:
            resp = await self.execute(
                StoryboardRequest(
                    scene=scene,
                    characters=request.characters,
                    style=request.style,
                ),
                worker_url=worker_url,
            )
            if resp.success and resp.data:
                return StoryboardResult(**resp.data)
            logger.warning("分镜批量生成失败: scene_id=%s worker=%s error=%s", scene.scene_id, worker_url, resp.error)
            return None

        if workers:
            tasks = [_generate_one(scene, workers[idx]) for idx, scene in enumerate(request.scenes)]
        else:
            tasks = [_generate_one(scene, None) for scene in request.scenes]
        outputs = await asyncio.gather(*tasks, return_exceptions=True)

        for scene, out in zip(request.scenes, outputs):
            if isinstance(out, Exception):
                logger.warning("分镜批量生成异常: scene_id=%s error=%s", scene.scene_id, out)
                failed.append(scene.scene_id)
            elif out is None:
                failed.append(scene.scene_id)
            else:
                results.append(out)

        return AgentResponse(
            success=True,
            data=StoryboardBatchResult(
                results=results,
                failed_scenes=failed,
            ).model_dump(),
            elapsed_seconds=time.time() - start,
        )

    async def _dispatch_image_generation(
        self,
        backend: str,
        worker_url: str | None,
        positive: str,
        negative: str,
        scene: Scene,
    ) -> str:
        """按后端派发分镜图生成。

        - hunyuanimage/flux_pulid: 调用图像服务生成字节图，保存到本地并返回 /static/storyboard/ URL
        - sdxl: 走原 ComfyUI 工作流路径
        主后端失败时自动回退到 SDXL。
        """
        if backend == "sdxl":
            # 直接走 SDXL 路径（无回退，已是回退终点）
            if worker_url is None:
                worker_url = await self.get_available_image_worker()
            return await self._generate_image_via_sdxl(
                worker_url, positive, negative, scene.scene_id
            )

        # 主路径：HunyuanImage / FLUX+PuLID
        try:
            return await self._generate_image_via_service(
                backend=backend,
                prompt=positive,
                negative_prompt=negative,
                scene_id=scene.scene_id,
            )
        except Exception as primary_err:
            logger.warning(
                "分镜 scene_id=%s 主后端 %s 失败，尝试回退 SDXL: %s",
                scene.scene_id, backend, primary_err,
            )
            try:
                if worker_url is None:
                    worker_url = await self.get_available_image_worker()
                return await self._generate_image_via_sdxl(
                    worker_url, positive, negative, scene.scene_id
                )
            except Exception as fallback_err:
                logger.error(
                    "分镜 scene_id=%s SDXL 回退也失败: %s",
                    scene.scene_id, fallback_err,
                )
                raise RuntimeError(
                    f"分镜主后端 {backend} 失败 ({primary_err})，SDXL 回退也失败 ({fallback_err})"
                )

    async def _generate_image_via_service(
        self,
        backend: str,
        prompt: str,
        negative_prompt: str,
        scene_id: int,
    ) -> str:
        """通过 HunyuanImage / FLUX+PuLID 服务生成图像，保存到本地并返回 URL。"""
        full_positive = prompt + POSITIVE_SUFFIX
        full_negative = (negative_prompt if negative_prompt else DEFAULT_NEGATIVE_PROMPT) + NEGATIVE_SUFFIX

        if backend == "hunyuanimage":
            img_bytes = await self.hunyuanimage_service.generate_one(
                prompt=full_positive,
                negative_prompt=full_negative,
            )
        elif backend == "flux_pulid":
            img_bytes = await self.flux_pulid_service.generate_one(
                prompt=full_positive,
                negative_prompt=full_negative,
            )
        else:
            raise ValueError(f"不支持的图像后端: {backend}")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        import random
        seed = random.randint(0, 2**32 - 1)
        filename = f"storyboard_scene_{scene_id}_{seed}.png"
        filepath = OUTPUT_DIR / filename
        filepath.write_bytes(img_bytes)

        base_url = f"http://localhost:{settings.backend_port}"
        return f"{base_url}/static/storyboard/{filename}"

    async def _generate_image_via_sdxl(
        self,
        worker_url: str,
        positive: str,
        negative: str,
        scene_id: int,
    ) -> str:
        """提交 SDXL 工作流到 ComfyUI，返回分镜图片 URL。"""
        import random

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE))
        workflow["2"]["inputs"]["text"] = positive
        workflow["3"]["inputs"]["text"] = negative
        workflow["5"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)
        workflow["7"]["inputs"]["filename_prefix"] = f"storyboard_scene_{scene_id}"

        result = await self.call_comfyui(worker_url, workflow)
        prompt_id = result.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id: {result}")

        outputs = await self.get_comfyui_result(worker_url, prompt_id, timeout=300.0)
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                img_info = node_output["images"][0]
                filename = img_info["filename"]
                subfolder = img_info.get("subfolder", "")
                img_type = img_info.get("type", "output")
                return f"{worker_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}"

        raise RuntimeError(f"未找到生成的图片: {outputs}")

    async def _generate_ltx_preview(
        self,
        image_url: str,
        scene: Scene,
        positive: str,
        negative: str,
    ) -> str:
        """调用 LTX-Video 生成低分辨率预览视频。

        失败不影响主流程，仅记录 warning 并返回空字符串。
        """
        try:
            # 运动描述提示词：从场景运镜和角色动作提取
            motion_prompt = f"{scene.camera_movement}, {scene.character_actions or 'subtle motion'}"
            result = await self.ltx_video_service.generate_preview(
                image_url=image_url,
                prompt=motion_prompt,
                negative_prompt=negative,
            )
            video_url = result.get("video_url", "")
            if video_url:
                logger.info(
                    "分镜 scene_id=%s LTX-Video 预览完成: %s",
                    scene.scene_id, video_url,
                )
            return video_url
        except Exception as e:
            logger.warning(
                "分镜 scene_id=%s LTX-Video 预览失败，跳过预览: %s",
                scene.scene_id, e,
            )
            return ""

    async def _generate_prompts(
        self, scene: Scene, characters: list[Character], style: str, reference: str = ""
    ) -> dict[str, str]:
        """调用 GLM-5.2 生成分镜英文提示词（可注入联网搜索参考资料）。"""
        char_info = ""
        if characters:
            char_info = "\n".join(
                f"- {c.name}（{c.role}）: {c.description}" for c in characters
            )

        user_msg = (
            f"场景编号：{scene.scene_id}\n"
            f"镜头类型：{scene.shot_type}\n"
            f"画面描述：{scene.description}\n"
            f"角色动作：{scene.character_actions or '无'}\n"
            f"台词：{scene.dialogue or '无'}\n"
            f"情绪：{scene.emotion}\n"
            f"运镜：{scene.camera_movement}\n"
            f"时长：{scene.duration_seconds}秒\n"
            f"画风要求：{style}\n"
        )
        if char_info:
            user_msg += f"\n相关角色：\n{char_info}\n"
        if reference:
            user_msg += f"\n参考资料（联网搜索，供借鉴镜头语言和构图技巧）：\n{reference}\n"

        user_msg += "\n请生成分镜的英文提示词 JSON。"

        content = await self.call_llm(
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            model=settings.exo_model_glm52,
            temperature=0.7,
            max_tokens=1500,
            response_format_json=False,
        )
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = json_repair.loads(content)
        if not isinstance(data, dict):
            data = {}
        return data


storyboard_agent = StoryboardAgent()
