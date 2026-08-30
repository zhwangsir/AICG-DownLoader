"""角色 Agent — 剧本人物 → 角色定妆照（三视图）。

图像后端：SDXL 经 ComfyUI-LB（majicMIX 写实 / animagineXL 动漫，按画风锚定选型）。
HunyuanImage（:8600 服务损坏）/ FLUX+PuLID（:8601 从未部署）路径已于 2026-08 移除。

流程：
1. 联网搜索角色设计参考资料
2. LLM 根据角色描述 + 参考资料生成英文图像提示词
3. 调用 SDXL 生成三视图
4. 保存图片并返回 URL
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
import uuid
from typing import Any

import json_repair
from openai import AsyncOpenAI

from app.agents.ai_optimizer import web_search
from app.agents.base import BaseAgent, strip_think_tags
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    Character,
    CharacterCard,
    CharacterPreviewRequest,
    CharacterPreviewResponse,
    CharacterRequest,
)
from app.services.character_library import character_library
from app.services.rag_service import rag_service
from app.services.style_anchor import (
    StyleAnchor,
    resolve_style_anchor,
    sanitize_style_conflicts,
    sdxl_checkpoint_for_anchor,
    style_negative_tail,
    style_positive_tail,
    style_prompt_clause,
)

logger = logging.getLogger(__name__)

PROMPT_SYSTEM = """你是角色设计专家。根据中文角色描述，生成用于图像模型的英文提示词。

输出 JSON：
{
  "front_view_prompt": "正面视角的英文提示词（含画质关键词）",
  "side_view_prompt": "侧面视角的英文提示词",
  "closeup_prompt": "面部特写的英文提示词",
  "negative_prompt": "反向提示词"
}

要求：
- 必须根据角色描述填充具体内容：角色身份、年龄、五官、发型、服装、气质、光线、构图，禁止使用 [Character Identity]、[specific hairstyle] 等占位符
- {style_clause}
- 三视图保持角色一致性（同一个人不同角度）
- **每个提示词必须以 "1girl, solo, single person, only one person" 或 "1boy, solo, single person, only one person" 开头**（根据角色性别选择），确保画面中只有一个人
- 不要在提示词中加入 "multiple views", "2girls", "3girls", "group" 等多人相关词汇
- 背景强制使用中性摄影棚背景："simple neutral gray studio background, soft rim lighting"，禁止户外/街道/店铺等具体场景，避免与剧情分镜背景冲突
- 反向提示词必须具体且充实，包含：multiple people, group, crowd, low quality, worst quality, deformed, ugly, blurry, bad anatomy, bad hands, missing fingers, extra digits, text, watermark, signature, outdoor, street, shop interior, complex background{style_negative_terms}
- JSON 字符串值中的双引号必须用 \" 转义，不要使用未转义的英文双引号
- 如果需要引用文字，请用中文引号「」或单引号
- 直接输出纯 JSON，不要用 markdown 代码块包裹
"""


def _build_style_system(style: str) -> tuple[str, StyleAnchor]:
    """M15.1 画风锚定：解析画风并构建注入风格子句的系统提示词。

    替代原硬编码「写实风格图像模型 + photorealistic」，
    使角色定妆照与剧本场景 / 分镜关键帧 / H3 视频保持同一画风。

    M16.1：风格词与外貌词权重分离 — 必填收窄为风格名，KB 整串降可选，
    防止 elaborate costumes 等内容词与角色外貌描述争权重。
    """
    anchor = resolve_style_anchor(style)
    realism_tail = ", photorealistic, professional photography" if anchor.is_realistic else ""
    style_clause = style_prompt_clause(anchor, target="角色定妆照") + (
        f"\n- 画质关键词：cinematic, 8k UHD, highly detailed{realism_tail}"
    )
    system = PROMPT_SYSTEM.replace("{style_clause}", style_clause).replace(
        "{style_negative_terms}", style_negative_tail(anchor)
    )
    return system, anchor


def _fallback_view_prompt(char: Character, anchor: StyleAnchor) -> str:
    """LLM 失败/超时时的兜底正面提示词（M15.1：画风锚定，替代原硬编码 photorealistic）。"""
    gender_prefix = (
        "1boy, solo, single person, only one person"
        if "女" not in char.description and "girl" not in char.description.lower()
        else "1girl, solo, single person, only one person"
    )
    quality_tail = f", {anchor.realism_tail_en}" if anchor.realism_tail_en else ""
    return (
        f"{gender_prefix}, {char.role}, {char.description[:120]}, "
        f"{anchor.style_name_en}, cinematic lighting, best quality{quality_tail}"
    )

def _isolate_character_asset(character_id: str) -> None:
    """M18.7 拦截即隔离：三视图 QC 重试耗尽判失败时，显式删除资产库中该
    character_id 的残留资产（可能是上一轮旧剧本同 ID 资产），防止
    _collect_character_reference_images 静默命中旧资产导致 ref2va 参考与
    漂移对照基准错配（M18.6 实测教训）。隔离异常不阻断拦截主流程。"""
    try:
        deleted = character_library.delete(character_id)
        logger.warning(
            "角色 %s 三视图质检重试耗尽判失败，已隔离资产库残留资产（deleted=%s，防串戏）",
            character_id, deleted,
        )
    except Exception as e:
        logger.warning("角色 %s 资产隔离异常（不阻断质检拦截）: %s", character_id, e)


# 强制追加的正面提示词（确保单人和高质量 + 中性摄影棚背景）
# M15.1：移除写死的专业摄影词，画风关键词由 style_anchor 在提示词生成阶段注入
POSITIVE_SUFFIX = ", solo, single person, only one person, portrait, looking at viewer, simple neutral gray studio background, soft rim lighting, best quality, masterpiece, highly detailed skin, detailed facial features, sharp focus, cinematic lighting, depth of field"

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
    """角色 Agent：LLM 生成提示词 → SDXL（ComfyUI-LB）生成定妆照。"""

    def __init__(self):
        super().__init__("character_agent")
        self._vlm_client: AsyncOpenAI | None = None

    async def execute(self, request: CharacterRequest) -> AgentResponse:
        start = time.time()
        try:
            char = request.character

            # Step 1: 确定提示词
            # M15.7：提前解析画风锚定（三种提示词模式共用），供 SDXL checkpoint 选型
            anchor = resolve_style_anchor(request.style)
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
                # M15.1：搜索词随画风锚定（原硬编码「写实人像摄影」会带偏动漫类画风）
                search_query = f"{anchor.title}风格 {char.role} {char.description[:40]} 角色设计"
                reference = await web_search(search_query, max_results=3)
                if reference:
                    logger.info("角色 Agent 搜索到参考资料: %d 字符", len(reference))
                prompts = await self._generate_prompts(char, request.style, reference)

            # Step 2: SDXL 生成三视图（ComfyUI-LB 并行 + 多后端负载均衡）
            # 使用固定 base seed 保证三视图角色一致性（同一个人不同角度）
            base_seed = random.randint(0, 2**32 - 1)
            reference_images: dict[str, str] = {}
            views = [
                ("front", prompts["front_view_prompt"], base_seed),
                ("side", prompts["side_view_prompt"], base_seed + 1),
                ("closeup", prompts["closeup_prompt"], base_seed),
            ]

            worker_urls = await self.get_available_image_workers(len(views))
            tasks = [
                self._generate_image_via_sdxl(
                    worker_urls[i],
                    prompt_text,
                    prompts["negative_prompt"],
                    char.character_id,
                    view_name,
                    seed,
                    anchor=anchor,
                )
                for i, (view_name, prompt_text, seed) in enumerate(views)
            ]

            view_results = await asyncio.gather(*tasks, return_exceptions=True)
            failed_views: list[str] = []
            for (view_name, prompt_text, seed), result in zip(views, view_results):
                if isinstance(result, Exception):
                    logger.error(
                        "角色 %s 视图 %s SDXL 生成失败: %s",
                        char.character_id, view_name, result,
                    )
                    failed_views.append(view_name)
                else:
                    reference_images[view_name] = result

            if failed_views:
                raise RuntimeError(f"视图生成失败: {failed_views}")

            # M18.2: VLM 质检——拦截「生成成功但内容废品」（如无关角色/素材参考表），
            # 不合格视图换 seed 重生成；重试耗尽抛错阻断入库；VLM 不可用 fail-open 放行
            if settings.character_view_qc_enabled and settings.visual_model_url:
                reference_images = await self._qc_three_views(
                    reference_images, prompts, char, anchor
                )

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
                    # M18.7 资产血缘：写入当前剧本 project_id，供收集阶段防串戏校验；
                    # 空串（画布单角色生成）按 legacy 处理
                    source_script_id=request.project_id,
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
        anchor = resolve_style_anchor(request.style)

        async def _build_preview() -> dict[str, str]:
            nonlocal reference, error_parts
            # Step 1: 联网搜索角色设计参考资料（失败不影响后续）
            try:
                # M15.1：搜索词随画风锚定（原硬编码「写实人像摄影」会带偏动漫类画风）
                search_query = f"{anchor.title}风格 {char.role} {char.description[:40]} 角色设计"
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
                # 降级：基于角色描述构造一个简单正面提示词（画风锚定）
                base = _fallback_view_prompt(char, anchor)
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
            base = _fallback_view_prompt(char, anchor)
            prompts = {
                "front_view_prompt": base,
                "side_view_prompt": base,
                "closeup_prompt": base,
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
        # M15.1 画风锚定：系统提示词注入统一画风子句与冲突风格负面词
        system, anchor = _build_style_system(style)
        user_msg = (
            f"角色名：{char.name}\n"
            f"角色身份：{char.role}\n"
            f"年龄：{char.age or '未指定'}\n"
            f"外貌描述：{char.description}\n"
            f"性格：{char.personality}\n"
            f"画风要求：{anchor.title}（{anchor.keywords_en}）\n"
        )
        if reference:
            user_msg += f"\n参考资料（联网搜索，供借鉴人像摄影技巧和角色设计风格）：\n{reference}\n"
        user_msg += "\n请生成三视图的英文提示词 JSON。"
        content = await self.call_llm(
            messages=[
                {"role": "system", "content": system},
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
            # LLM 未返回正面提示词，构造兜底提示词（M15.1：画风锚定）
            front = _fallback_view_prompt(char, anchor)
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
            prompts = await self._rag_optimize_prompts(prompts, style, anchor)

        # M15.1 画风锚定尾巴：最后追加，保证 RAG 重写后仍带统一画风信号
        # M15.4：追加前先清洗 LLM/RAG 产出中与目标画风互斥的风格词
        pos_tail = style_positive_tail(anchor)
        for key in ("front_view_prompt", "side_view_prompt", "closeup_prompt"):
            prompts[key] = sanitize_style_conflicts(prompts[key], anchor) + pos_tail
        prompts["negative_prompt"] = sanitize_style_conflicts(
            prompts["negative_prompt"], anchor, negative=True
        ) + style_negative_tail(anchor)

        return prompts

    async def _rag_optimize_prompts(
        self,
        prompts: dict[str, str],
        style: str,
        anchor: StyleAnchor | None = None,
    ) -> dict[str, str]:
        """使用 RAG 优化角色三视图提示词，保持单人约束。"""
        if anchor is None:
            anchor = resolve_style_anchor(style)
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
                    # M15.1：原硬编码 photorealistic character，改为画风锚定风格名
                    extra_instruction=(
                        f"{view_hint}, keep solo single person only one person, "
                        f"{anchor.style_name_en} character"
                    ),
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

    async def _generate_image_via_sdxl(
        self,
        worker_url: str,
        positive: str,
        negative: str,
        character_id: str,
        view_name: str,
        seed: int,
        anchor: StyleAnchor | None = None,
    ) -> str:
        """提交 SDXL 工作流到 ComfyUI，返回图片 URL（原 _generate_image 逻辑）。"""
        # 追加强制正面/负面提示词，确保单人 + 高质量
        full_positive = positive + POSITIVE_SUFFIX
        full_negative = (negative if negative else "text, watermark, low quality") + NEGATIVE_SUFFIX

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE))
        # M15.7: 按画风写实性选 checkpoint（majicMIX 写实特化，动漫风必须用 animagine）
        workflow["1"]["inputs"]["ckpt_name"] = sdxl_checkpoint_for_anchor(anchor)
        workflow["2"]["inputs"]["text"] = full_positive
        workflow["3"]["inputs"]["text"] = full_negative
        workflow["5"]["inputs"]["seed"] = seed
        # M15.5: 唯一后缀防跨后端同名碰撞（LB /view 按后端顺序命中陈旧文件）
        workflow["7"]["inputs"]["filename_prefix"] = (
            f"character_{character_id}_{view_name}_{uuid.uuid4().hex[:8]}"
        )

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

    # ================================================================
    # M18.2: 三视图 VLM 质检
    # 背景：M18.1 帧级核验发现 char_001 side 实为无关白发少女、char_002 side
    # 实为 16 格眼睛画法参考表——视图生成「成功」但内容是废品，无质检拦截
    # 混入 ref 组。此处在三视图生成后、角色卡入库前拦截，不合格换 seed 重生成。
    # ================================================================

    def _get_vlm_client(self) -> AsyncOpenAI:
        """懒加载 VLM 客户端（与分镜外貌校验同一 Qwen3-VL 入口）。"""
        if self._vlm_client is None:
            self._vlm_client = AsyncOpenAI(
                base_url=settings.visual_model_url,
                api_key="not-needed",
                http_client=self.http,
            )
        return self._vlm_client

    async def _qc_three_views(
        self,
        reference_images: dict[str, str],
        prompts: dict[str, str],
        char: Character,
        anchor: StyleAnchor,
    ) -> dict[str, str]:
        """三视图 VLM 质检编排：front 自检 → side/closeup 与 front 双图比对。

        不合格视图换 seed 重生成并复检，最多重试 character_view_qc_max_retries 次；
        重试耗尽抛 RuntimeError（废品拦截，不入库）。单视图判定层的 VLM 异常
        一律 fail-open 放行（质检器故障不阻断生产）。
        """
        max_retries = settings.character_view_qc_max_retries
        prompt_by_view = {
            "front": prompts["front_view_prompt"],
            "side": prompts["side_view_prompt"],
            "closeup": prompts["closeup_prompt"],
        }

        # front 自检：不合格重生成后，side/closeup 的比对基准同步换成重生图
        front_url = reference_images["front"]
        for attempt in range(max_retries + 1):
            reason = await self._qc_front_view(front_url, char, anchor)
            if not reason:
                break
            if attempt >= max_retries:
                # M18.7 拦截即隔离：判失败时清除资产库残留（防止收集到旧剧本同 ID 资产）
                _isolate_character_asset(char.character_id)
                raise RuntimeError(f"front 视图质检连续 {attempt + 1} 次不合格: {reason}")
            logger.warning(
                "角色 %s front 视图质检不合格（%s），换 seed 重生成 %d/%d",
                char.character_id, reason, attempt + 1, max_retries,
            )
            front_url = await self._regenerate_view(
                prompt_by_view["front"], prompts["negative_prompt"],
                char.character_id, "front", anchor,
            )
        reference_images["front"] = front_url

        # side/closeup 并行与 front 比对，各自独立重生成
        async def _check_and_regen(view_name: str) -> tuple[str, str]:
            url = reference_images[view_name]
            for attempt in range(max_retries + 1):
                reason = await self._qc_view_consistency(view_name, url, front_url, char)
                if not reason:
                    break
                if attempt >= max_retries:
                    # M18.7 拦截即隔离：判失败时清除资产库残留（防止收集到旧剧本同 ID 资产）
                    _isolate_character_asset(char.character_id)
                    raise RuntimeError(
                        f"{view_name} 视图质检连续 {attempt + 1} 次不合格: {reason}"
                    )
                logger.warning(
                    "角色 %s %s 视图与 front 不一致（%s），换 seed 重生成 %d/%d",
                    char.character_id, view_name, reason, attempt + 1, max_retries,
                )
                url = await self._regenerate_view(
                    prompt_by_view[view_name], prompts["negative_prompt"],
                    char.character_id, view_name, anchor,
                )
            return view_name, url

        for view_name, url in await asyncio.gather(
            _check_and_regen("side"), _check_and_regen("closeup")
        ):
            reference_images[view_name] = url
        return reference_images

    async def _qc_front_view(
        self, image_url: str, char: Character, anchor: StyleAnchor
    ) -> str:
        """VLM 自检 front 定妆照。返回空串=合格/跳过，非空串=不合格原因。

        判定焦点：单人、完整人物肖像（非素材参考表/线稿/多格拼图）、
        外貌符合角色描述、画风符合锚定。任何异常 fail-open 返回空串。
        """
        if not settings.visual_model_url:
            return ""
        try:
            resp = await self.http.get(image_url, timeout=30)
            resp.raise_for_status()
            encoded = base64.b64encode(resp.content).decode("utf-8")
            text = (
                "这是一张 AI 生成的角色定妆照（正面视图）。请判定它是否为合格的角色定妆照：\n"
                "1) 画面中只有一个人物（多人、多格拼图、分割画面均不合格）；\n"
                "2) 必须是完整的人物肖像——眼睛/五官局部素材表、画法参考表、线稿、教程图均不合格；\n"
                f"3) 外貌符合角色描述（发色/发型/服装）：{char.description}\n"
                f"4) 画风为{anchor.title}（{anchor.style_name_en}）。\n"
                '只输出 JSON：{"pass": true/false, "reason": "不合格时简述具体原因，合格时填空串"}。'
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
                            "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"},
                        },
                    ],
                }],
                temperature=0.1,
                max_tokens=300,
            )
            raw = strip_think_tags(result.choices[0].message.content or "")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json_repair.loads(raw)
            if not isinstance(data, dict):
                return ""
            if data.get("pass") is False:
                return str(data.get("reason") or "front 定妆照质检不合格")
            return ""
        except Exception as e:
            logger.warning("front 视图质检异常（fail-open 放行）: %s", e)
            return ""

    async def _qc_view_consistency(
        self, view_name: str, view_url: str, front_url: str, char: Character
    ) -> str:
        """VLM 双图比对 side/closeup 与 front 是否同一角色。空串=一致/跳过。

        判定焦点：发色/发型、服装款式与颜色；姿势、视角、表情差异不算不一致。
        任何异常 fail-open 返回空串。
        """
        if not settings.visual_model_url:
            return ""
        try:
            resp_front = await self.http.get(front_url, timeout=30)
            resp_front.raise_for_status()
            resp_view = await self.http.get(view_url, timeout=30)
            resp_view.raise_for_status()
            front_b64 = base64.b64encode(resp_front.content).decode("utf-8")
            view_b64 = base64.b64encode(resp_view.content).decode("utf-8")
            view_label = {"side": "侧面", "closeup": "面部特写"}.get(view_name, view_name)
            text = (
                f"第一张图是角色「{char.name}」的定妆正面照，第二张图应是同一角色的{view_label}视图。"
                "请判定两张图是否为同一角色：发色/发型、服装款式与颜色必须一致；"
                "姿势、视角、表情差异不算不一致。\n"
                f"角色描述：{char.description}\n"
                '只输出 JSON：{"match": true/false, "reason": "不一致时简述具体差异，一致时填空串"}。'
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
                            "image_url": {"url": f"data:image/png;base64,{front_b64}", "detail": "high"},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{view_b64}", "detail": "high"},
                        },
                    ],
                }],
                temperature=0.1,
                max_tokens=300,
            )
            raw = strip_think_tags(result.choices[0].message.content or "")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json_repair.loads(raw)
            if not isinstance(data, dict):
                return ""
            if data.get("match") is False:
                return str(data.get("reason") or f"{view_name} 视图与 front 定妆照不一致")
            return ""
        except Exception as e:
            logger.warning("%s 视图一致性质检异常（fail-open 放行）: %s", view_name, e)
            return ""

    async def _regenerate_view(
        self,
        prompt: str,
        negative_prompt: str,
        character_id: str,
        view_name: str,
        anchor: StyleAnchor,
    ) -> str:
        """质检不合格后换新随机 seed 经 SDXL 重生成单视图。"""
        new_seed = random.randint(0, 2**32 - 1)
        worker_url = await self.get_available_image_worker()
        return await self._generate_image_via_sdxl(
            worker_url, prompt, negative_prompt, character_id, view_name,
            new_seed, anchor=anchor,
        )


character_agent = CharacterAgent()
