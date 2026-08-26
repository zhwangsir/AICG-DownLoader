"""分镜 Agent — 剧本场景 → 分镜关键帧图片。

图像后端：SDXL 经 ComfyUI-LB（majicMIX 写实 / animagineXL 动漫，按画风锚定选型）。
HunyuanImage（:8600 服务损坏）/ FLUX+PuLID（:8601 从未部署）路径已于 2026-08 移除；
旧 LTX-2B 分镜预览钩子（pc01 :8700）随 LTX-2.5 升级一并移除，待重建。

流程：
1. 联网搜索镜头语言/构图参考资料
2. LLM 根据场景描述 + 角色信息 + 参考资料生成英文图像提示词
3. 提交 SDXL 工作流到 ComfyUI-LB
4. 返回分镜结果
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any

import json_repair
from openai import AsyncOpenAI

from app.agents.ai_optimizer import web_search
from app.agents.base import BaseAgent, strip_think_tags
from app.services.character_library import character_library
from app.services.failure_registry import failure_registry
from app.services.mention_service import auto_link_characters
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
from app.services.rag_service import rag_service
from app.services.style_anchor import (
    StyleAnchor,
    resolve_style_anchor,
    sanitize_style_conflicts,
    sdxl_checkpoint_for_anchor,
    strip_kb_atmosphere,
    style_negative_tail,
    style_positive_tail,
    style_prompt_clause,
)

logger = logging.getLogger(__name__)

PROMPT_SYSTEM = """你是分镜设计专家。根据中文场景描述，生成用于图像模型的英文提示词。

输出 JSON：
{
  "prompt": "英文正面提示词（含画质关键词、场景、光线、构图、角色外观、角色动作）",
  "negative_prompt": "英文反向提示词"
}

要求：
- 提示词必须完整覆盖中文 description 中的所有视觉要素：场景环境、关键道具、光线氛围、出场角色外观（发色/服装/表情）、角色动作、镜头语言
- 若输入中包含 characters 角色描述，必须将出场角色的核心外观特征（服装、发型、气质）翻译并嵌入 prompt，确保跨场景角色一致
- {style_clause}
- 彩色画面：默认生成彩色，明确写入 "full color, vivid color grading"，禁止黑白/单色
- 镜头语言：shot_type 对应的英文（close-up / medium shot / wide shot / over-the-shoulder shot）
- 竖屏 9:16 构图，主体置于画面下 2/3 区域
- 禁止出现可读文字：场景中可能出现招牌/屏幕文字时，写 "blurred illegible signage, no readable text" 并在 negative_prompt 加入 "legible text, letters, alphabet, signage with text"
- JSON 字符串值中的双引号必须用 \\" 转义
- 直接输出纯 JSON，不要用 markdown 代码块包裹

反例（过于简略，禁止）：
"cinematic 8k detailed convenience store interior high contrast"

正例（细节完整；画风词仅为示意，实际以画风统一子句为准）：
"cinematic medium shot, late-night convenience store interior, dim yellow fluorescent lighting, young Chinese female clerk with shoulder-length black wavy hair in white t-shirt and blue denim jacket standing behind checkout counter, staring at surveillance monitor, eerie tension, full color, {style_name_en}, 8k UHD, blurred illegible signage, no readable text"
"""


def _build_style_system(style: str) -> tuple[str, StyleAnchor]:
    """M15.1 画风锚定：解析画风并构建注入风格子句的系统提示词。

    替代原硬编码「写实风格图像模型 + photorealistic」，
    使分镜关键帧与剧本场景 / 角色定妆照 / H3 视频保持同一画风。

    M16.1：风格词与外貌词权重分离 — 必填收窄为风格名，KB 整串降可选，
    防止 elaborate costumes 等内容词与出场角色外貌描述争权重。
    """
    anchor = resolve_style_anchor(style)
    realism_tail = (
        ", photorealistic, professional photography, film still" if anchor.is_realistic else ""
    )
    style_clause = style_prompt_clause(anchor, target="分镜画面") + (
        f"\n- 画质关键词：cinematic, 8k UHD, highly detailed{realism_tail}"
    )
    system = (
        PROMPT_SYSTEM
        .replace("{style_clause}", style_clause)
        .replace("{style_name_en}", anchor.style_name_en or "cinematic realistic")
    )
    return system, anchor

# 强制追加的正面提示词（确保高质量与彩色风格）
# M15.1：移除写死的写实摄影词，画风关键词由 style_anchor 在 execute 阶段强制追加
POSITIVE_SUFFIX = ", cinematic, 8k UHD, best quality, masterpiece, highly detailed, sharp focus, depth of field, full color, vivid color grading"

# 强制追加的负面提示词
NEGATIVE_SUFFIX = ", text, watermark, signature, logo, legible text, letters, alphabet, black and white, monochrome, grayscale, low quality, worst quality, deformed, ugly, blurry, bad anatomy, bad hands, missing fingers, extra digits, cropped, out of frame, duplicate, clone"

# 默认反向提示词（当 LLM 未返回或返回空时使用）
DEFAULT_NEGATIVE_PROMPT = (
    "text, watermark, signature, logo, legible text, letters, alphabet, "
    "black and white, monochrome, grayscale, "
    "low quality, worst quality, deformed, ugly, blurry, "
    "bad anatomy, bad hands, missing fingers, extra digits, cropped, out of frame, "
    "duplicate, clone, bad proportions, malformed limbs"
)

# 叙事节拍 → 分镜视觉指令（与剧本层 narrative_beat 联动，引导关键帧画面的情绪表达）
BEAT_VISUAL_HINTS = {
    "hook": "强钩子镜头：画面必须有第一眼的视觉冲击力——高对比戏剧光、压迫感构图、主体表情/动作极具张力",
    "escalation": "冲突升级镜头：画面张力递进——更紧的构图、更强的明暗对比、肢体语言对抗感",
    "reversal": "反转镜头：画面要有戏剧性落差——预期违背的瞬间，表情特写或局势翻盘的视觉定格",
    "cliffhanger": "悬念镜头：画面悬而未决——信息只给一半，留白构图，制造追更冲动",
    "emotional_beat": "情绪落点镜头：画面放缓——柔光、浅景深、表情细腻特写，让观众共情",
    "transition": "过渡镜头：画面承担衔接——环境交代或视线引导，构图平稳不过载信息",
}

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

# M18.3: 关键帧定妆照 IPAdapter 锚定节点模板
#   8: IPAdapterModelLoader - SDXL 外观适配器（ip-adapter-plus-face_sdxl_vit-h）
#   9: CLIPVisionLoader - 参考图视觉编码器（ViT-H）
#  11: LoadImage - 角色定妆照 front（运行时替换为上传文件名）
#  12: IPAdapterAdvanced - 参考图外观特征注入 UNet；KSampler model 重定向到 12
IPADAPTER_ANCHOR_NODES = {
    "8": {
        "class_type": "IPAdapterModelLoader",
        "inputs": {
            "ipadapter_file": "ip-adapter-plus-face_sdxl_vit-h.safetensors"
        }
    },
    "9": {
        "class_type": "CLIPVisionLoader",
        "inputs": {
            "clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
        }
    },
    "11": {
        "class_type": "LoadImage",
        "inputs": {
            "image": ""
        }
    },
    "12": {
        "class_type": "IPAdapterAdvanced",
        "inputs": {
            "model": ["1", 0],
            "ipadapter": ["8", 0],
            "clip_vision": ["9", 0],
            "image": ["11", 0],
            "weight": 0.6,
            "weight_type": "linear",
            "combine_embeds": "concat",
            "start_at": 0.0,
            "end_at": 1.0,
            "embeds_scaling": "V only"
        }
    }
}


class StoryboardAgent(BaseAgent):
    """分镜 Agent：LLM 生成提示词 → SDXL（ComfyUI-LB）生成关键帧。"""

    def __init__(self):
        super().__init__("storyboard_agent")
        self._vlm_client: AsyncOpenAI | None = None

    async def execute(
        self,
        request: StoryboardRequest,
        worker_url: str | None = None,
    ) -> AgentResponse:
        start = time.time()
        try:
            # M25.2 AutoLink：提示词装配前扫描场景文本，文本提及的资产库角色
            # 自动并入出场角色（外观锁定卡注入 + 定妆照锚定图源）
            request = self._apply_auto_link(request)
            scene = request.scene

            # AI 优化 step 1：联网搜索镜头语言参考资料
            search_query = f"电影分镜 {scene.shot_type} {scene.emotion} {scene.camera_movement} 构图技巧"
            reference = await web_search(search_query, max_results=3)
            if reference:
                logger.info("分镜 Agent 搜索到参考资料: %d 字符", len(reference))

            # Step 2: 始终通过 LLM 重写英文提示词
            # 原因：剧本 LLM 生成的 scene.prompt 通常过于简略（只有画质关键词），
            # 缺少场景细节与角色外观，直接喂给图像模型会丢失剧情要素。
            # 这里强制融合 scene.description + characters + scene.prompt 重新构造，
            # 确保分镜图与剧本描述一致。
            prompts = await self._generate_prompts(
                scene, request.characters, request.style, reference
            )
            positive = prompts.get("prompt") or scene.prompt or ""
            negative = (
                prompts.get("negative_prompt")
                or scene.negative_prompt
                or DEFAULT_NEGATIVE_PROMPT
            )
            if not positive:
                raise RuntimeError("LLM 未返回分镜提示词")

            # M15.1 画风锚定：强制追加统一画风关键词与冲突风格负面词，
            # 保证分镜关键帧与剧本场景 / 角色定妆照 / H3 视频全链路画风一致
            # M15.4：追加前先清洗 LLM 重写结果中与目标画风互斥的风格词
            # M16.2：确定性剥离 KB 氛围填充词（多角色长 prompt 下稀释 CLIP
            # 注意力且与锁定外貌冲突，core E2E 实测外貌被模型先验覆盖）
            anchor = resolve_style_anchor(request.style)
            positive = sanitize_style_conflicts(positive, anchor)
            negative = sanitize_style_conflicts(negative, anchor, negative=True)
            positive = strip_kb_atmosphere(positive, anchor)
            positive += style_positive_tail(anchor)
            negative += style_negative_tail(anchor)

            # M25.9 C2 失败模式注册表：generator 层反向子句注入（失败经验资产化，
            # DramaClaw failure_registry 本地化对等）；注册表读取异常不阻断生成
            try:
                registry_clause = failure_registry.build_negative_prompt_clause("generator")
                if registry_clause and registry_clause not in negative:
                    negative = f"{negative}\n{registry_clause}"
            except Exception as e:
                logger.warning("失败模式注册表子句注入失败（跳过）: %s", e)

            # M18.3 关键帧定妆照锚定：解析首个有定妆照 front 的角色参考图，
            # SDXL 路径注入 IPAdapter 节点从源头锚定角色外观/服饰一致性
            anchor_image_url = ""
            if settings.storyboard_keyframe_anchor_enabled and request.characters:
                anchor_image_url = await self._resolve_keyframe_anchor_url(
                    request.characters
                )

            # M25.9 C1 线稿先行：草图模式低步数/低CFG/小尺寸快速出构图；
            # refine_seed 非空 = 精渲染复用草图 seed（同 seed 防构图漂移）
            sketch = request.sketch_mode and settings.sketch_mode_enabled

            # Step 3: SDXL 生成关键帧
            image_url, used_seed = await self._dispatch_image_generation(
                worker_url=worker_url,
                positive=positive,
                negative=negative,
                scene=scene,
                anchor=anchor,
                anchor_image_url=anchor_image_url,
                sketch=sketch,
                seed_override=request.refine_seed,
            )

            # M16.2 拼贴检测+重试：出场角色存在且 VLM 可用时校验外貌一致性，
            # 失真则 LLM 重构短 prompt（外貌前置、禁氛围词）并重生成一次。
            # 草图阶段跳过：低步数粗图构图确认即可，外貌校验在精渲染阶段执行
            if not sketch and request.characters and settings.storyboard_appearance_check:
                image_url, _ = await self._verify_and_retry_appearance(
                    image_url=image_url,
                    scene=scene,
                    characters=request.characters,
                    style=request.style,
                    worker_url=worker_url,
                    negative=negative,
                    anchor=anchor,
                    anchor_image_url=anchor_image_url,
                )

            result = StoryboardResult(
                scene_id=scene.scene_id,
                image_url=image_url,
                prompt_used=positive,
                is_sketch=sketch,
                sketch_seed=used_seed if sketch else None,
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

        # SDXL 路径：预分配 ComfyUI-LB Worker
        workers: list[str] | None = await self.get_available_image_workers(len(request.scenes))

        async def _generate_one(scene: Scene, worker_url: str | None) -> StoryboardResult | None:
            resp = await self.execute(
                StoryboardRequest(
                    scene=scene,
                    characters=request.characters,
                    style=request.style,
                    auto_link_assets=request.auto_link_assets,
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

    def _apply_auto_link(self, request: StoryboardRequest) -> StoryboardRequest:
        """M25.2 AutoLink：扫描场景文本，文本提及的资产库角色自动并入出场角色。

        扫描 scene.description/character_actions/dialogue；命中角色随
        request.characters 走既有链路（外观锁定卡注入见 _generate_prompts →
        character_library.resolve_characters，定妆照锚定见 M18.3
        _resolve_keyframe_anchor_url）。仅精确/CI 包含匹配，不做 fuzzy
        （宁缺毋滥）；已在出场列表的角色（按 character_id / 角色名判重）不重复
        挂接。任何异常回退原请求（AutoLink 是增强不是阻断）。
        """
        enabled = (
            request.auto_link_assets
            if request.auto_link_assets is not None
            else settings.auto_link_assets_enabled
        )
        if not enabled:
            return request
        scene = request.scene
        text = "\n".join(
            part
            for part in (scene.description, scene.character_actions, scene.dialogue)
            if part
        )
        if not text.strip():
            return request
        try:
            matched = auto_link_characters(text)
        except Exception as e:
            logger.warning("AutoLink 自动资产匹配异常，回退原角色列表: %s", e)
            return request
        if not matched:
            return request
        existing_ids = {c.character_id for c in request.characters}
        existing_names = {c.name for c in request.characters}
        merged = list(request.characters)
        added: list[str] = []
        for asset in matched:
            if asset.character_id in existing_ids or asset.name in existing_names:
                continue
            merged.append(Character(
                character_id=asset.character_id,
                name=asset.name,
                role=asset.role,
                age=asset.age,
                description=asset.description,
                personality=asset.personality,
                reference_views=[u for u in asset.reference_images.values() if u],
            ))
            added.append(asset.name)
        if not added:
            return request
        logger.info("AutoLink 自动挂接角色: scene_id=%s added=%s", scene.scene_id, added)
        return request.model_copy(update={"characters": merged})

    async def _dispatch_image_generation(
        self,
        worker_url: str | None,
        positive: str,
        negative: str,
        scene: Scene,
        anchor: StyleAnchor | None = None,
        anchor_image_url: str = "",
        sketch: bool = False,
        seed_override: int | None = None,
    ) -> tuple[str, int]:
        """SDXL 生成分镜图（ComfyUI 工作流路径，M18.3 定妆照锚定在此生效）。

        M25.9 C1：sketch=True 时走草图参数（低步数/低CFG/小尺寸）；
        seed_override 非空时复用该 seed（精渲染复用草图 seed 防构图漂移）。
        返回 (图片 URL, 实际使用的 seed)。
        """
        if worker_url is None:
            worker_url = await self.get_available_image_worker()
        return await self._generate_image_via_sdxl(
            worker_url, positive, negative, scene.scene_id,
            anchor=anchor, anchor_image_url=anchor_image_url,
            sketch=sketch, seed_override=seed_override,
        )

    async def _generate_image_via_sdxl(
        self,
        worker_url: str,
        positive: str,
        negative: str,
        scene_id: int,
        anchor: StyleAnchor | None = None,
        anchor_image_url: str = "",
        sketch: bool = False,
        seed_override: int | None = None,
    ) -> tuple[str, int]:
        """提交 SDXL 工作流到 ComfyUI，返回 (分镜图片 URL, 使用的 seed)。"""
        import random
        import uuid

        workflow = json.loads(json.dumps(WORKFLOW_TEMPLATE))
        # M15.7: 按画风写实性选 checkpoint（与 character_agent 同因）
        workflow["1"]["inputs"]["ckpt_name"] = sdxl_checkpoint_for_anchor(anchor)
        workflow["2"]["inputs"]["text"] = positive
        workflow["3"]["inputs"]["text"] = negative
        # M25.9 C1：refine_seed 非空时复用（同 seed 防漂移），否则随机
        seed = seed_override if seed_override is not None else random.randint(0, 2**32 - 1)
        workflow["5"]["inputs"]["seed"] = seed
        # M25.9 C1 草图参数：低步数/低 CFG/小尺寸（返工成本卡在最便宜阶段）
        if sketch:
            workflow["4"]["inputs"]["width"] = settings.sketch_width
            workflow["4"]["inputs"]["height"] = settings.sketch_height
            workflow["5"]["inputs"]["steps"] = settings.sketch_steps
            workflow["5"]["inputs"]["cfg"] = settings.sketch_cfg
        # M15.5: 唯一后缀防跨后端同名碰撞（与 character_agent 同因）
        workflow["7"]["inputs"]["filename_prefix"] = (
            f"storyboard_scene_{scene_id}_{uuid.uuid4().hex[:8]}"
        )

        # M18.3: 定妆照 IPAdapter 锚定 — 上传参考图并注入节点；
        # 上传/装配异常回退原工作流（锚定是增强不是阻断）
        if anchor_image_url and settings.storyboard_keyframe_anchor_enabled:
            workflow = await self._inject_ipadapter_anchor(
                workflow, worker_url, anchor_image_url
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
                return (
                    f"{worker_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}",
                    seed,
                )

        raise RuntimeError(f"未找到生成的图片: {outputs}")

    # ------------------------------------------------------------------
    # M18.3 关键帧定妆照 IPAdapter 锚定
    # ------------------------------------------------------------------

    async def _resolve_keyframe_anchor_url(self, characters: list[Character]) -> str:
        """解析关键帧定妆照锚定图 — 取首个有定妆照 front 的角色参考图。

        无出场角色 / 资产库无参考图 / 解析异常 → 返回空串（不锚定）。
        """
        if not characters:
            return ""
        try:
            resolved = character_library.resolve_characters(characters)
        except Exception as e:
            logger.warning("关键帧锚定参考图解析失败，跳过锚定: %s", e)
            return ""
        for r in resolved:
            front = r.get("reference_front", "")
            if front:
                return front
        return ""

    async def _inject_ipadapter_anchor(
        self, workflow: dict[str, Any], worker_url: str, anchor_image_url: str
    ) -> dict[str, Any]:
        """向 SDXL 工作流注入定妆照 IPAdapter 锚定节点。

        定妆照上传或节点装配任何异常都回退原工作流（锚定是增强不是阻断），
        保留 KSampler 原 model 输入。

        M18.3.1：配置 comfyui_lb_backend_urls 时，定妆照以同一文件名直连复制到
        LB 全部后端 — LB /upload/image 轮询单实例而 /prompt 按负载选实例，只传
        单点会导致 LoadImage 跨后端找不到文件（400 prompt_outputs_failed_validation）。
        部分后端失败仍注入（LB 健康检查会避开故障后端）；全部失败回退原工作流。
        """
        try:
            backends = [
                u.strip()
                for u in settings.comfyui_lb_backend_urls.split(",")
                if u.strip()
            ]
            if backends:
                filename = f"{self.name}_{uuid.uuid4().hex[:8]}.png"
                ok = 0
                for backend in backends:
                    try:
                        await self.upload_image_to_comfyui(
                            backend, anchor_image_url, filename=filename
                        )
                        ok += 1
                    except Exception as e:
                        logger.warning("定妆照复制到后端 %s 失败: %s", backend, e)
                if ok == 0:
                    raise RuntimeError("定妆照复制到全部 LB 后端均失败")
                image_name = filename
            else:
                image_name = await self.upload_image_to_comfyui(worker_url, anchor_image_url)
            nodes = json.loads(json.dumps(IPADAPTER_ANCHOR_NODES))
            nodes["8"]["inputs"]["ipadapter_file"] = settings.ipadapter_sdxl_model_name
            nodes["9"]["inputs"]["clip_name"] = settings.ipadapter_clip_vision_name
            nodes["11"]["inputs"]["image"] = image_name
            nodes["12"]["inputs"]["weight"] = settings.storyboard_keyframe_anchor_weight
            workflow.update(nodes)
            workflow["5"]["inputs"]["model"] = ["12", 0]
            logger.info("分镜关键帧已注入定妆照 IPAdapter 锚定: %s", anchor_image_url)
            return workflow
        except Exception as e:
            logger.warning("定妆照锚定注入失败，回退原 SDXL 工作流: %s", e)
            return workflow

    async def _generate_prompts(
        self, scene: Scene, characters: list[Character], style: str, reference: str = ""
    ) -> dict[str, str]:
        """调用 GLM-5.2 生成分镜英文提示词（可注入联网搜索参考资料）。"""
        char_info = ""
        if characters:
            # 角色资产库解析：锁定角色强制注入外观锁定卡（跨集/跨镜一致性）
            try:
                resolved = character_library.resolve_characters(characters)
            except Exception as e:
                logger.warning("角色资产库解析失败，回退请求内角色描述: %s", e)
                resolved = [
                    {"name": c.name, "role": c.role, "description": c.description, "appearance_lock": ""}
                    for c in characters
                ]
            char_lines: list[str] = []
            for r in resolved:
                line = f"- {r['name']}（{r['role']}）: {r['description']}"
                if r.get("appearance_lock"):
                    line += (
                        "\n  外观锁定（跨集一致性，必须原样保留以下英文外观关键词）: "
                        f"{r['appearance_lock']}"
                    )
                char_lines.append(line)
            char_info = "\n".join(char_lines)

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
        if scene.prompt:
            user_msg += f"\n剧本 LLM 已给出的英文 prompt（仅供参考，请在此基础上补全场景细节与角色外观，不要直接复用）：\n{scene.prompt}\n"
        beat_hint = BEAT_VISUAL_HINTS.get(scene.narrative_beat.strip(), "")
        if beat_hint:
            user_msg += f"\n叙事节拍：{scene.narrative_beat}——{beat_hint}，构图与光影必须体现该节拍情绪\n"
        if char_info:
            user_msg += f"\n相关角色（必须将出场角色的外观特征嵌入 prompt）：\n{char_info}\n"
        if reference:
            user_msg += f"\n参考资料（联网搜索，供借鉴镜头语言和构图技巧）：\n{reference}\n"

        user_msg += "\n请生成分镜的英文提示词 JSON。"

        # M15.1 画风锚定：系统提示词注入统一画风子句（替代原硬编码写实关键词）
        system, _anchor = _build_style_system(style)
        content = await self.call_llm(
            messages=[
                {"role": "system", "content": system},
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

        # RAG 增强：基于影视分镜知识库优化提示词
        if settings.rag_optimize_enabled:
            data = await self._rag_optimize_storyboard_prompts(data, style)

        return data

    # ------------------------------------------------------------------
    # M16.2 拼贴检测 + 短 prompt 重构重试
    # ------------------------------------------------------------------

    def _get_vlm_client(self) -> AsyncOpenAI:
        """懒加载 VLM 客户端（与视觉质检同一 Qwen3-VL 入口）。"""
        if self._vlm_client is None:
            self._vlm_client = AsyncOpenAI(
                base_url=settings.visual_model_url,
                api_key="not-needed",
                http_client=self.http,
            )
        return self._vlm_client

    async def _check_appearance_mismatch(
        self, image_url: str, characters: list[Character]
    ) -> str:
        """VLM 校验关键帧中出场角色外貌与角色描述的一致性。

        返回空串表示一致或跳过（VLM 未配置）；非空串为失真原因，
        供短 prompt 重构参考。判定焦点为发色/发型/服装款式与颜色，
        画风、姿势、表情、视角差异不算失真。
        """
        if not settings.visual_model_url:
            return ""
        resp = await self.http.get(image_url, timeout=30)
        resp.raise_for_status()
        encoded = base64.b64encode(resp.content).decode("utf-8")
        char_lines = "\n".join(
            f"- {c.name}（{c.role}）: {c.description}" for c in characters
        )
        text = (
            "这是一张短剧分镜关键帧。请核对图中出场角色的外貌是否与下列角色描述一致"
            "（重点：发色/发型、服装款式与颜色；画风差异、姿势、表情、视角差异不算失真）。\n"
            f"{char_lines}\n"
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
        if data.get("match") is False:
            return str(data.get("reason") or "外貌与角色描述不一致")
        return ""

    async def _rebuild_short_prompt(
        self,
        scene: Scene,
        characters: list[Character],
        style: str,
        mismatch_reason: str,
    ) -> str:
        """外貌失真后的 LLM 短 prompt 重构：外貌前置、禁氛围词、≤80 词。"""
        char_lines = "\n".join(
            f"- {c.name}（{c.role}）: {c.description}" for c in characters
        )
        system = (
            "你是分镜提示词专家。上一版英文提示词生成的图片与角色外貌不符"
            f"（{mismatch_reason}）。请重构英文短提示词，硬性规则：\n"
            "1. 以镜头类型开头（如 medium shot / extreme close-up）；\n"
            "2. 紧随其后立即写出每个出场角色的核心外貌（发色/发型/服装款式与颜色，"
            "必须从给定中文描述原样翻译，不得更改）；\n"
            "3. 之后只保留场景最核心的动作与环境要素（不超过 2 个）；\n"
            "4. 总长度不超过 80 个英文单词；\n"
            "5. 禁止使用氛围填充词：vibrant colors, detailed line art, dramatic expressions, "
            "dynamic poses, fantasy elements, elaborate costumes, particle effects；\n"
            "6. 只输出 JSON：{\"prompt\": \"...\"}，不要 markdown 代码块。"
        )
        user_msg = (
            f"场景编号：{scene.scene_id}\n"
            f"镜头类型：{scene.shot_type}\n"
            f"画面描述：{scene.description}\n"
            f"角色动作：{scene.character_actions or '无'}\n"
            f"画风要求：{style}\n"
            f"出场角色：\n{char_lines}\n"
        )
        content = await self.call_llm(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            model=settings.exo_model_glm52,
            temperature=0.4,
            max_tokens=400,
            response_format_json=False,
        )
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = json_repair.loads(content)
        if not isinstance(data, dict):
            return ""
        return str(data.get("prompt") or "").strip()

    async def _verify_and_retry_appearance(
        self,
        *,
        image_url: str,
        scene: Scene,
        characters: list[Character],
        style: str,
        worker_url: str | None,
        negative: str,
        anchor: StyleAnchor,
        anchor_image_url: str = "",
    ) -> tuple[str, int | None]:
        """拼贴检测+重试主流程：校验失真时短 prompt 重构并重生成一次。

        任何环节异常都保留原图，不影响分镜主流程。
        M18.3：重试重生成时锚定参考图一并透传（重试不丢锚定）。
        返回 (最终图片 URL, 重试使用的 seed 或 None 表示未重试)。
        """
        try:
            mismatch = await self._check_appearance_mismatch(image_url, characters)
        except Exception as e:
            logger.warning("分镜外貌校验异常（跳过重试）: scene_id=%s error=%s", scene.scene_id, e)
            return image_url, None
        if not mismatch:
            return image_url, None
        logger.warning(
            "分镜外貌失真，短 prompt 重构重试: scene_id=%s reason=%s", scene.scene_id, mismatch
        )
        # M25.9 C2：失真命中回写注册表（重复犯错者上浮，供提示词治理优先级排序）；
        # 回写失败不影响重试主流程
        try:
            failure_registry.bump_hit("collage_mismatch")
        except Exception as e:
            logger.warning("失败模式命中回写失败（跳过）: %s", e)
        try:
            retry_positive = await self._rebuild_short_prompt(scene, characters, style, mismatch)
        except Exception as e:
            logger.warning("分镜短 prompt 重构失败（保留原图）: scene_id=%s error=%s", scene.scene_id, e)
            return image_url, None
        if not retry_positive:
            return image_url, None
        retry_positive = sanitize_style_conflicts(retry_positive, anchor)
        retry_positive = strip_kb_atmosphere(retry_positive, anchor)
        retry_positive += style_positive_tail(anchor)
        # M25.9 C2：重试负面提示词追加 correction 层注册表子句
        try:
            correction_clause = failure_registry.build_negative_prompt_clause("correction")
            if correction_clause and correction_clause not in negative:
                negative = f"{negative}\n{correction_clause}"
        except Exception as e:
            logger.warning("失败模式修正子句注入失败（跳过）: %s", e)
        try:
            return await self._dispatch_image_generation(
                worker_url=worker_url,
                positive=retry_positive,
                negative=negative,
                scene=scene,
                anchor=anchor,
                anchor_image_url=anchor_image_url,
            )
        except Exception as e:
            logger.warning("分镜外貌重试生成失败（保留原图）: scene_id=%s error=%s", scene.scene_id, e)
            return image_url, None

    async def _rag_optimize_storyboard_prompts(
        self,
        prompts: dict[str, str],
        style: str,
    ) -> dict[str, str]:
        """使用 RAG 优化分镜英文提示词，失败则保留原结果。"""
        positive = prompts.get("prompt", "").strip()
        if not positive:
            return prompts

        try:
            result = await rag_service.optimize_prompt(
                user_prompt=positive,
                domain="image",
                style_hint=style or None,
                extra_instruction="cinematic storyboard keyframe, keep vertical 9:16 composition",
            )
            optimized = dict(prompts)
            if result.get("optimized_positive"):
                optimized["prompt"] = result["optimized_positive"]
            if result.get("optimized_negative"):
                optimized["negative_prompt"] = result["optimized_negative"]
            return optimized
        except Exception as e:
            logger.warning("分镜 RAG 优化失败，保留原提示词: %s", e)
            return prompts


storyboard_agent = StoryboardAgent()
