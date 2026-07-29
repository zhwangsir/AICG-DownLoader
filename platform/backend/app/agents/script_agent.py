"""剧本 Agent — 一句话创意 → JSON 结构化剧本。

对接 EXO 集群的 GLM-5.2（1M context，支持思考），输出符合 §4.8 规范的剧本 JSON。
生成前联网搜索同题材参考资料，注入提示词提升质量。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import json_repair

from app.agents.ai_optimizer import web_search
from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import AgentResponse, Script, ScriptRequest
from app.services.rag_service import rag_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是专业短剧编剧。根据用户的一句话创意，生成结构化 JSON 剧本。

输出 JSON 必须严格遵循以下格式：
{
  "title": "剧名",
  "synopsis": "一句话剧情简介（30-60字，含核心悬念）",
  "characters": [
    {
      "character_id": "char_001",
      "name": "角色名",
      "role": "主角/配角/反派",
      "age": 25,
      "description": "外貌特征详细描述（用于图像生成，含五官、发型、服装、气质）",
      "personality": "性格特征"
    }
  ],
  "scenes": [
    {
      "scene_id": 1,
      "episode": 1,
      "shot_type": "特写/近景/中景/远景",
      "location": "场景地点（如：便利店内部/街道/卧室）",
      "description": "画面描述（中文，详细到可以画出来）",
      "prompt": "English positive prompt for image generation",
      "negative_prompt": "English negative prompt",
      "character_actions": "角色动作描述",
      "dialogue": "台词",
      "emotion": "tension/romantic/happy/sad/mysterious",
      "duration_seconds": 5,
      "camera_movement": "static/pan/tilt/zoom"
    }
  ]
}

要求：
1. 每集 {scenes_per_episode} 个分镜，共 {episodes} 集
2. 角色描述要详细到可以生成定妆照（五官、发型、服装、气质）
3. 画面描述要具体（场景、光线、构图、人物状态）
4. **英文 prompt 必须完整翻译 description 中的核心视觉元素**，包含：
   - 画质关键词：cinematic, 8k, photorealistic, highly detailed
   - 镜头语言：shot_type 对应的英文（close-up/medium shot/wide shot）
   - 场景细节：location、关键道具、光线氛围（如 dim yellow lighting, neon signs）
   - 角色外观：出场角色的核心特征（发色、服装、表情），与 characters.description 一致
   - 角色动作：character_actions 的英文翻译
   - 情绪氛围：emotion 对应的英文氛围词（eerie/tense/romantic）
   - 彩色风格：默认生成彩色画面，禁止黑白（除非剧情明确要求）
   - 反面示例（禁止）："cinematic 8k detailed store interior" （过于简略，丢失核心剧情元素）
   - 正面示例："cinematic medium shot, late-night convenience store interior, dim yellow fluorescent lighting, young Chinese female clerk with shoulder-length black wavy hair in white t-shirt and denim jacket standing behind checkout counter, staring at surveillance monitor showing her own doppelganger waving back, eerie tension, photorealistic, 8k"
5. 台词要口语化、有张力，单条不超过 30 字
6. 节奏：开场悬念 → 冲突升级 → 高潮 → 悬念结尾
7. JSON 字符串值中的双引号必须用 \\" 转义，不要使用未转义的英文双引号
8. 如果需要引用文字，请用中文引号「」或单引号
9. negative_prompt 默认包含：black and white, monochrome, blurry, low quality, deformed, cartoon, anime, text, watermark, extra fingers, bad anatomy
10. synopsis 必填，30-60字概括全剧核心悬念

直接输出纯 JSON，不要用 markdown 代码块包裹，不要输出任何解释性文字。
"""


class ScriptAgent(BaseAgent):
    """剧本 Agent：GLM-5.2 生成结构化剧本。"""

    def __init__(self):
        super().__init__("script_agent")

    async def execute(self, request: ScriptRequest) -> AgentResponse:
        start = time.time()
        try:
            # AI 优化 step 1：联网搜索同题材参考资料
            search_query = f"短剧 {request.genre} {request.premise[:30]} 剧情设计 角色塑造"
            reference = await web_search(search_query, max_results=3)
            if reference:
                logger.info("剧本 Agent 搜索到参考资料: %d 字符", len(reference))

            system = (
                SYSTEM_PROMPT
                .replace("{episodes}", str(request.episodes))
                .replace("{scenes_per_episode}", str(request.scenes_per_episode))
            )
            user_msg = (
                f"创意：{request.premise}\n"
                f"题材：{request.genre}\n"
                f"集数：{request.episodes}\n"
                f"每集分镜数：{request.scenes_per_episode}\n"
                f"画幅：9:16（竖屏短剧）\n"
            )
            if reference:
                user_msg += f"\n参考资料（联网搜索，供借鉴剧情节奏和角色设计手法）：\n{reference}\n"
            user_msg += "\n请生成完整的 JSON 剧本。"

            content = await self.call_llm(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                model=settings.exo_model_glm52,
                temperature=0.85,
                max_tokens=16000,
                response_format_json=True,
            )

            script_data = self._parse_llm_json(content)

            if not isinstance(script_data, dict):
                logger.error("剧本 LLM 返回无法解析为 JSON 对象，原始内容前 1000 字：%s", content[:1000])
                raise RuntimeError(f"剧本 LLM 返回格式异常: {type(script_data)}")

            raw_chars = script_data.get("characters", [])
            clean_chars = [
                c for c in raw_chars
                if isinstance(c, dict) and c.get("character_id") and c.get("name")
            ]

            raw_scenes = script_data.get("scenes", [])
            clean_scenes = []
            for i, s in enumerate(raw_scenes):
                if not isinstance(s, dict):
                    continue
                if "scene_id" not in s:
                    s["scene_id"] = i + 1
                if "description" not in s:
                    continue
                clean_scenes.append(s)

            # RAG 增强：基于知识库优化每个场景的生成提示词
            if settings.rag_optimize_enabled:
                await self._rag_enhance_scenes(clean_scenes, request.genre)

            script = Script(
                project_id=str(uuid.uuid4()),
                title=script_data.get("title", "未命名"),
                genre=request.genre,
                aspect_ratio="9:16",
                total_episodes=request.episodes,
                characters=clean_chars,
                scenes=clean_scenes,
            )

            return AgentResponse(
                success=True,
                data=script.model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"剧本生成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    @staticmethod
    def _parse_llm_json(content: str) -> Any:
        """多层容错解析 LLM 返回的 JSON。

        1. 先尝试标准 json.loads
        2. 失败则用 json_repair 修复
        3. 若 json_repair 返回字符串（双重转义 / 含多余文本），尝试二次解析
        4. 仍失败时从文本中提取 { ... } / [ ... ] 片段再解析
        """
        if not content or not content.strip():
            return None

        cleaned = content.strip()

        # 1) 标准解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 2) json_repair 修复
        try:
            parsed = json_repair.loads(cleaned)
        except Exception as e:
            logger.warning("json_repair 解析失败: %s", e)
            parsed = None

        # 3) 处理双重转义字符串
        if isinstance(parsed, str):
            second = parsed.strip()
            try:
                return json.loads(second)
            except json.JSONDecodeError:
                pass
            try:
                return json_repair.loads(second)
            except Exception as e:
                logger.warning("二次 json_repair 解析失败: %s", e)
                parsed = second

        # 4) 如果还是字符串，尝试从文本中截取 JSON 片段
        if isinstance(parsed, str):
            # 找第一个 { 或 [ 到最后一个 } 或 ]
            start_idx = -1
            for ch in ("{", "["):
                idx = parsed.find(ch)
                if idx != -1 and (start_idx == -1 or idx < start_idx):
                    start_idx = idx
            end_idx = -1
            for ch in ("}", "]"):
                idx = parsed.rfind(ch)
                if idx != -1 and idx > end_idx:
                    end_idx = idx
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                snippet = parsed[start_idx : end_idx + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    try:
                        return json_repair.loads(snippet)
                    except Exception as e:
                        logger.warning("JSON 片段解析失败: %s", e)

        return parsed

    async def _rag_enhance_scenes(self, scenes: list[dict[str, Any]], genre: str) -> None:
        """使用 RAG 优化每个场景的正向/负向提示词。"""
        for scene in scenes:
            description = scene.get("description", "").strip()
            if not description:
                continue
            try:
                result = await rag_service.optimize_prompt(
                    user_prompt=description,
                    domain="video",
                    style_hint=genre or None,
                    extra_instruction="根据短剧场景描述生成高质量英文图像/视频生成提示词",
                )
                if result.get("optimized_positive"):
                    scene["prompt"] = result["optimized_positive"]
                if result.get("optimized_negative"):
                    scene["negative_prompt"] = result["optimized_negative"]
            except Exception as e:
                logger.warning("场景 %s RAG 优化失败，保留原提示词: %s", scene.get("scene_id"), e)


script_agent = ScriptAgent()
