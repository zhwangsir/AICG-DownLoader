"""角色 Agent — 剧本人物 → 角色定妆照（三视图）。

P4.3 升级：HunyuanImage 2.1 / FLUX+PuLID 为主，ComfyUI SDXL 为回退。

后端选择由 settings.image_backend 控制：
- 'hunyuanimage' (默认): HunyuanImage 2.1 17B FP8，原生 2K + 中文 prompt 最强
- 'flux_pulid': FLUX.1-dev + PuLID-FLUX v0.9.1，角色 ID 一致性专用（推荐用于三视图）
- 'sdxl': ComfyUI majicMIX realistic SDXL（回退路径）

主后端失败时自动回退到 SDXL。

流程：
1. 联网搜索角色设计参考资料
2. GLM-5.2 根据角色描述 + 参考资料生成英文图像提示词
3. 调用对应图像后端生成三视图（PuLID 可选注入角色参考图保证一致性）
4. 保存图片并返回 URL
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from pathlib import Path
from typing import Any

import json_repair

from app.agents.ai_optimizer import web_search
from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    Character,
    CharacterCard,
    CharacterPreviewRequest,
    CharacterPreviewResponse,
    CharacterRequest,
)
from app.services.image_service import FluxPuLIDService, HunyuanImageService
from app.services.character_library import character_library
from app.services.rag_service import rag_service

logger = logging.getLogger(__name__)

# 输出目录：保存 HunyuanImage / FLUX+PuLID 返回的图片字节
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "character"

PROMPT_SYSTEM = """你是角色设计专家。根据中文角色描述，生成用于写实风格图像模型的英文提示词。

输出 JSON：
{
  "front_view_prompt": "正面视角的英文提示词（含画质关键词）",
  "side_view_prompt": "侧面视角的英文提示词",
  "closeup_prompt": "面部特写的英文提示词",
  "negative_prompt": "反向提示词"
}

要求：
- 必须根据角色描述填充具体内容：角色身份、年龄、五官、发型、服装、气质、光线、构图，禁止使用 [Character Identity]、[specific hairstyle] 等占位符
- 画质关键词：cinematic, 8k UHD, photorealistic, professional photography, highly detailed
- 三视图保持角色一致性（同一个人不同角度）
- **每个提示词必须以 "1girl, solo, single person, only one person" 或 "1boy, solo, single person, only one person" 开头**（根据角色性别选择），确保画面中只有一个人
- 不要在提示词中加入 "multiple views", "2girls", "3girls", "group" 等多人相关词汇
- 背景强制使用中性摄影棚背景："simple neutral gray studio background, soft rim lighting"，禁止户外/街道/店铺等具体场景，避免与剧情分镜背景冲突
- 反向提示词必须具体且充实，包含：multiple people, group, crowd, low quality, worst quality, deformed, ugly, blurry, bad anatomy, bad hands, missing fingers, extra digits, text, watermark, signature, outdoor, street, shop interior, complex background
- JSON 字符串值中的双引号必须用 \" 转义，不要使用未转义的英文双引号
- 如果需要引用文字，请用中文引号「」或单引号
- 直接输出纯 JSON，不要用 markdown 代码块包裹
"""

# 强制追加的正面提示词（确保单人和高质量 + 中性摄影棚背景）
POSITIVE_SUFFIX = ", solo, single person, only one person, portrait, looking at viewer, simple neutral gray studio background, soft rim lighting, best quality, masterpiece, highly detailed skin, detailed facial features, sharp focus, professional photography, cinematic lighting, depth of field"

# 强制追加的负面提示词（排除多人和低质量 + 排除复杂背景）
NEGATIVE_SUFFIX = ", multiple people, group, crowd, 2girls, 3girls, 4girls, multiple views, split screen, text, watermark, signature, outdoor, street, shop interior, complex background, cluttered background, low quality, worst quality, deformed, ugly, blurry, bad anatomy, bad hands, missing fingers, extra digits, cropped, out of frame, duplicate, clone"

# 默认反向提示词（当 LLM 未返回或返回空时使用）
DEFAULT_NEGATIVE_PROMPT = (
    "multiple people, group, crowd, 2girls, 3girls, 4girls, multiple views, split screen, "
    "text, watermark, signature, outdoor, street, shop interior, complex background, "
    "low quality, worst quality, deformed, ugly, blurry, "
    "bad anatomy, bad hands, missing fingers, extra digits, cropped, out of frame, "
    "duplicate, clone, bad proportions, malformed limbs"
)

# SDXL 写实工作流模板（majicMIX realistic）
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
            "width": 832,
            "height": 1216,
            "batch_size": 1
        }
    },
    "5": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 35,
            "cfg": 7.5,
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
            "filename_prefix": "character_{character_id}"
        }
    }
}


class CharacterAgent(BaseAgent):
    """角色 Agent：LLM 生成提示词 → 图像后端生成定妆照。

    后端选择由 settings.image_backend 控制：
    - 'hunyuanimage' (默认): HunyuanImage 2.1 17B FP8，原生 2K + 中文 prompt 最强
    - 'flux_pulid': FLUX.1-dev + PuLID-FLUX v0.9.1，角色 ID 一致性专用
    - 'sdxl': ComfyUI majicMIX realistic SDXL（回退路径）

    主后端失败时自动回退到 SDXL。
    """

    def __init__(self):
        super().__init__("character_agent")
        self._hunyuanimage: HunyuanImageService | None = None
        self._flux_pulid: FluxPuLIDService | None = None

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

    async def execute(self, request: CharacterRequest) -> AgentResponse:
        start = time.time()
        try:
            char = request.character
            backend = settings.image_backend.lower()

            # Step 1: 确定提示词
            if request.preview_positive_prompt.strip():
                # 用户预览确认后的提示词：直接使用，跳过搜索和 LLM
                logger.info("角色 Agent 使用预览确认后的提示词生成: %s", char.character_id)
                prompts = {
                    "front_view_prompt": request.preview_positive_prompt,
                    "side_view_prompt": request.preview_positive_prompt,
                    "closeup_prompt": request.preview_positive_prompt,
                    "negative_prompt": request.preview_negative_prompt,
                }
            elif request.custom_positive_prompt.strip():
                # 用户自定义提示词模式：直接使用，三视图用同一正面提示词
                prompts = {
                    "front_view_prompt": request.custom_positive_prompt,
                    "side_view_prompt": request.custom_positive_prompt,
                    "closeup_prompt": request.custom_positive_prompt,
                    "negative_prompt": request.custom_negative_prompt,
                }
            else:
                # 默认模式：联网搜索 + LLM 生成三视图英文提示词
                search_query = f"写实人像摄影 {char.role} {char.description[:40]} 角色设计"
                reference = await web_search(search_query, max_results=3)
                if reference:
                    logger.info("角色 Agent 搜索到参考资料: %d 字符", len(reference))
                prompts = await self._generate_prompts(char, request.style, reference)

            # Step 2: 按后端派发图像生成
            # 使用固定 base seed 保证三视图角色一致性（同一个人不同角度）
            base_seed = random.randint(0, 2**32 - 1)
            reference_images: dict[str, str] = {}
            views = [
                ("front", prompts["front_view_prompt"], base_seed),
                ("side", prompts["side_view_prompt"], base_seed + 1),
                ("closeup", prompts["closeup_prompt"], base_seed),
            ]

            if backend == "sdxl":
                # ComfyUI SDXL 原路径：三视图并行 + 多 GPU 负载均衡
                worker_urls = await self.get_available_image_workers(len(views))
                tasks = [
                    self._generate_image_via_sdxl(
                        worker_urls[i],
                        prompt_text,
                        prompts["negative_prompt"],
                        char.character_id,
                        view_name,
                        seed,
                    )
                    for i, (view_name, prompt_text, seed) in enumerate(views)
                ]
            else:
                # HunyuanImage / FLUX+PuLID 主路径：保存字节到本地输出目录
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                # FLUX+PuLID 可选注入参考图（来自 front 视图作为 ID 参照）
                # 但三视图并行生成时无参考图可用，因此首次生成不注入；
                # 后续可通过二次调用注入 front 视图提升一致性
                tasks = [
                    self._generate_image_via_service(
                        backend=backend,
                        prompt=prompt_text,
                        negative_prompt=prompts["negative_prompt"],
                        character_id=char.character_id,
                        view_name=view_name,
                        seed=seed,
                    )
                    for i, (view_name, prompt_text, seed) in enumerate(views)
                ]

            view_results = await asyncio.gather(*tasks, return_exceptions=True)
            failed_views: list[str] = []
            for (view_name, prompt_text, seed), result in zip(views, view_results):
                if isinstance(result, Exception):
                    # 主后端单视图失败不立即抛出，尝试回退 SDXL
                    logger.warning(
                        "角色 %s 视图 %s 主后端 %s 失败，尝试回退 SDXL: %s",
                        char.character_id, view_name, backend, result,
                    )
                    try:
                        fallback_url = await self._fallback_to_sdxl(
                            prompt_text,
                            prompts["negative_prompt"],
                            char.character_id,
                            view_name,
                            seed,
                        )
                        reference_images[view_name] = fallback_url
                    except Exception as fallback_err:
                        logger.error(
                            "角色 %s 视图 %s SDXL 回退也失败: %s",
                            char.character_id, view_name, fallback_err,
                        )
                        failed_views.append(view_name)
                else:
                    reference_images[view_name] = result

            if failed_views:
                raise RuntimeError(f"视图生成失败（主+回退均失败）: {failed_views}")

            # Step 3: 构建角色卡（含使用的提示词，供前端编辑）
            card = CharacterCard(
                character_id=char.character_id,
                name=char.name,
                anchor_points=200,
                reference_images=reference_images,
                consistency_level=request.consistency_level,
                used_prompts={
                    "positive_prompt": prompts["front_view_prompt"],
                    "negative_prompt": prompts["negative_prompt"],
                },
            )

            # Step 4: 自动登记角色资产库（外观锁定卡，跨集一致性强制引用）
            try:
                character_library.register_from_card(
                    character=char,
                    reference_images=reference_images,
                    used_prompts=card.used_prompts,
                    consistency_level=request.consistency_level,
                )
            except Exception as e:
                # 资产库登记失败不阻断角色生成主流程
                logger.warning("角色资产库登记失败（不影响生成结果）: %s", e)

            return AgentResponse(
                success=True,
                data=card.model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"角色生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def preview(self, request: CharacterPreviewRequest) -> AgentResponse:
        """角色生成预览：联网搜索 + 生成提示词，不生成图片。

        返回数据供前端展示、编辑，用户确认后再调用 execute 生成图片。
        搜索、LLM 任一环节失败均降级返回可编辑的默认提示词，保证流程不中断。
        整体超时 45 秒，避免 LLM 无响应导致前端长时间处于 searching 状态。
        """
        start = time.time()
        char = request.character
        reference = ""
        error_parts: list[str] = []

        async def _build_preview() -> dict[str, str]:
            nonlocal reference, error_parts
            # Step 1: 联网搜索角色设计参考资料（失败不影响后续）
            try:
                search_query = f"写实人像摄影 {char.role} {char.description[:40]} 角色设计"
                reference = await web_search(search_query, max_results=3)
                if reference:
                    logger.info("角色 Agent 预览搜索到参考资料: %d 字符", len(reference))
            except Exception as e:
                logger.warning("角色预览搜索失败，继续生成默认提示词: %s", e)
                error_parts.append("联网搜索失败")

            # Step 2: LLM 生成三视图英文提示词（注入搜索参考资料）
            try:
                return await self._generate_prompts(char, request.style, reference)
            except Exception as e:
                logger.exception("角色预览 LLM 生成提示词失败，使用默认提示词")
                error_parts.append("提示词生成失败")
                # 降级：基于角色描述构造一个简单正面提示词
                gender_prefix = (
                    "1boy, solo, single person, only one person"
                    if "女" not in char.description and "girl" not in char.description.lower()
                    else "1girl, solo, single person, only one person"
                )
                base = f"{gender_prefix}, {char.role}, {char.description[:120]}, photorealistic, cinematic lighting, best quality"
                return {
                    "front_view_prompt": base,
                    "side_view_prompt": base,
                    "closeup_prompt": base,
                    "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
                }

        try:
            prompts = await asyncio.wait_for(_build_preview(), timeout=45.0)
        except asyncio.TimeoutError:
            logger.warning("角色预览整体超时，返回默认提示词")
            error_parts.append("预览超时")
            gender_prefix = (
                "1boy, solo, single person, only one person"
                if "女" not in char.description and "girl" not in char.description.lower()
                else "1girl, solo, single person, only one person"
            )
            prompts = {
                "front_view_prompt": f"{gender_prefix}, {char.role}, {char.description[:120]}, photorealistic, cinematic lighting, best quality",
                "side_view_prompt": f"{gender_prefix}, {char.role}, {char.description[:120]}, photorealistic, cinematic lighting, best quality",
                "closeup_prompt": f"{gender_prefix}, {char.role}, {char.description[:120]}, photorealistic, cinematic lighting, best quality",
                "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            }

        preview_data = CharacterPreviewResponse(
            character_id=char.character_id,
            character=char,
            style=request.style,
            search_reference=reference,
            prompts=prompts,
        )

        return AgentResponse(
            success=True,
            data=preview_data.model_dump(),
            elapsed_seconds=time.time() - start,
            error="; ".join(error_parts) if error_parts else None,
        )

    async def _generate_prompts(self, char: Character, style: str, reference: str = "") -> dict[str, str]:
        """调用 GLM-5.2 生成三视图英文提示词（可注入联网搜索参考资料）。"""
        user_msg = (
            f"角色名：{char.name}\n"
            f"角色身份：{char.role}\n"
            f"年龄：{char.age or '未指定'}\n"
            f"外貌描述：{char.description}\n"
            f"性格：{char.personality}\n"
            f"画风要求：{style}\n"
        )
        if reference:
            user_msg += f"\n参考资料（联网搜索，供借鉴人像摄影技巧和角色设计风格）：\n{reference}\n"
        user_msg += "\n请生成三视图的英文提示词 JSON。"
        content = await self.call_llm(
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            model=settings.exo_model_glm52,
            temperature=0.7,
            max_tokens=2000,
            response_format_json=False,
        )
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = json_repair.loads(content)

        # 容错：json_repair 可能返回字符串而非字典
        if not isinstance(data, dict):
            data = {}

        front = data.get("front_view_prompt", "")
        if not front:
            # LLM 未返回正面提示词，构造兜底提示词
            gender_prefix = (
                "1boy, solo, single person, only one person"
                if "女" not in char.description and "girl" not in char.description.lower()
                else "1girl, solo, single person, only one person"
            )
            front = f"{gender_prefix}, {char.role}, {char.description[:120]}, photorealistic, cinematic lighting, best quality"
        negative = data.get("negative_prompt", "")
        if not negative or not negative.strip():
            negative = DEFAULT_NEGATIVE_PROMPT

        prompts = {
            "front_view_prompt": front,
            "side_view_prompt": data.get("side_view_prompt") or front,
            "closeup_prompt": data.get("closeup_prompt") or front,
            "negative_prompt": negative,
        }

        # RAG 增强：基于角色设计知识库优化三视图提示词
        if settings.rag_optimize_enabled:
            prompts = await self._rag_optimize_prompts(prompts, style)

        return prompts

    async def _rag_optimize_prompts(
        self,
        prompts: dict[str, str],
        style: str,
    ) -> dict[str, str]:
        """使用 RAG 优化角色三视图提示词，保持单人约束。"""
        views = [
            ("front_view_prompt", "front view character portrait"),
            ("side_view_prompt", "side profile character portrait"),
            ("closeup_prompt", "close-up face portrait"),
        ]
        optimized_negative = ""
        result = dict(prompts)

        for key, view_hint in views:
            positive = prompts.get(key, "").strip()
            if not positive:
                continue
            try:
                opt = await rag_service.optimize_prompt(
                    user_prompt=positive,
                    domain="image",
                    style_hint=style or None,
                    extra_instruction=f"{view_hint}, keep solo single person only one person, photorealistic character",
                )
                if opt.get("optimized_positive"):
                    result[key] = opt["optimized_positive"]
                if not optimized_negative and opt.get("optimized_negative"):
                    optimized_negative = opt["optimized_negative"]
            except Exception as e:
                logger.warning("角色 %s RAG 优化失败，保留原提示词: %s", key, e)

        if optimized_negative:
            result["negative_prompt"] = optimized_negative

        return result

    async def _generate_image_via_service(
        self,
        backend: str,
        prompt: str,
        negative_prompt: str,
        character_id: str,
        view_name: str,
        seed: int,
    ) -> str:
        """通过 HunyuanImage / FLUX+PuLID 服务生成图像，保存到本地并返回 URL。

        返回的 URL 指向本后端静态资源（/static/character/）。
        """
        # 追加强制正面/负面提示词（保持与 SDXL 路径一致的提示词约束）
        full_positive = prompt + POSITIVE_SUFFIX
        full_negative = (
            negative_prompt if negative_prompt else "text, watermark, low quality"
        ) + NEGATIVE_SUFFIX

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

        # 保存到本地输出目录
        filename = f"character_{character_id}_{view_name}_{seed}.png"
        filepath = OUTPUT_DIR / filename
        filepath.write_bytes(img_bytes)

        base_url = f"http://localhost:{settings.backend_port}"
        return f"{base_url}/static/character/{filename}"

    async def _fallback_to_sdxl(
        self,
        prompt: str,
        negative_prompt: str,
        character_id: str,
        view_name: str,
        seed: int,
    ) -> str:
        """主后端失败时回退到 ComfyUI SDXL 生成单张图像。"""
        worker_url = await self.get_available_image_worker()
        return await self._generate_image_via_sdxl(
            worker_url,
            prompt,
            negative_prompt,
            character_id,
            view_name,
            seed,
        )

    async def _generate_image_via_sdxl(
        self,
        worker_url: str,
        positive: str,
        negative: str,
        character_id: str,
        view_name: str,
        seed: int,
    ) -> str:
        """提交 SDXL 工作流到 ComfyUI，返回图片 URL（原 _generate_image 逻辑）。"""
        # 追加强制正面/负面提示词，确保单人 + 高质量
        full_positive = positive + POSITIVE_SUFFIX
        full_negative = (negative if negative else "text, watermark, low quality") + NEGATIVE_SUFFIX

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE))
        workflow["2"]["inputs"]["text"] = full_positive
        workflow["3"]["inputs"]["text"] = full_negative
        workflow["5"]["inputs"]["seed"] = seed
        workflow["7"]["inputs"]["filename_prefix"] = f"character_{character_id}_{view_name}"

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


character_agent = CharacterAgent()
