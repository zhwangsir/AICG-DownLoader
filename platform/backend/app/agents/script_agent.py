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

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是专业短剧编剧。根据用户的一句话创意，生成结构化 JSON 剧本。

输出 JSON 必须严格遵循以下格式：
{
  "title": "剧名",
  "genre": "题材",
  "characters": [
    {
      "character_id": "char_001",
      "name": "角色名",
      "role": "主角/配角/反派",
      "age": 25,
      "description": "外貌特征详细描述（用于图像生成）",
      "personality": "性格特征"
    }
  ],
  "scenes": [
    {
      "scene_id": 1,
      "episode": 1,
      "shot_type": "特写/近景/中景/远景",
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
4. 英文 prompt 要包含画质关键词（cinematic, 8k, detailed 等）
5. 台词要口语化、有张力
6. 节奏：开场悬念 → 冲突升级 → 高潮 → 悬念结尾
7. JSON 字符串值中的双引号必须用 \\" 转义，不要使用未转义的英文双引号
8. 如果需要引用文字，请用中文引号「」或单引号

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
                response_format_json=False,
            )

            try:
                script_data = json.loads(content)
            except json.JSONDecodeError:
                script_data = json_repair.loads(content)

            # 容错：json_repair 可能返回非字典类型
            if not isinstance(script_data, dict):
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


script_agent = ScriptAgent()
