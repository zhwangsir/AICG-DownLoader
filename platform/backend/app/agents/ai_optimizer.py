"""AI 优化模块 — 联网搜索参考资料 + LLM 润色优化。

每个 Agent 在生成内容前后可调用此模块：
1. 生成前：web_search() 联网搜索相关题材参考资料
2. 生成后：optimize_content() 用 LLM 对结果二次润色

搜索使用 DuckDuckGo Instant Answer API + Wikipedia API，无需 API key。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.agents.base import get_shared_llm_client, strip_think_tags
from app.config import settings

logger = logging.getLogger(__name__)

# 搜索超时（秒），避免网络问题阻塞太久
SEARCH_TIMEOUT = 8.0


async def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索参考资料，返回拼接的摘要文本。

    依次尝试 DuckDuckGo Instant Answer API 和 Wikipedia API，
    任何一个失败不影响整体流程。
    """
    snippets: list[str] = []

    # 1. DuckDuckGo Instant Answer API
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, trust_env=False) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )
            if resp.status_code == 200:
                data: dict[str, Any] = resp.json()
                # 抽象摘要
                abstract = data.get("Abstract", "")
                if abstract:
                    snippets.append(f"[DuckDuckGo] {abstract}")
                # 相关主题
                for topic in (data.get("RelatedTopics") or [])[:max_results]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        snippets.append(f"[参考] {topic['Text'][:200]}")
    except Exception as e:
        logger.warning("DuckDuckGo 搜索失败: %s", e)

    # 2. Wikipedia API（中文）
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, trust_env=False) as client:
            resp = await client.get(
                "https://zh.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": max_results,
                    "format": "json",
                    "utf8": "1",
                },
            )
            if resp.status_code == 200:
                wdata = resp.json()
                for item in (wdata.get("query", {}).get("search") or [])[:max_results]:
                    title = item.get("title", "")
                    snippet = item.get("snippet", "").replace("<span class=\"searchmatch\">", "").replace("</span>", "")
                    if title and snippet:
                        snippets.append(f"[Wiki] {title}: {snippet[:200]}")
    except Exception as e:
        logger.warning("Wikipedia 搜索失败: %s", e)

    if not snippets:
        return ""

    return "\n".join(snippets)


async def optimize_content(
    content: str,
    task_type: str,
    reference: str = "",
    extra_instruction: str = "",
) -> str:
    """用 LLM 对生成内容进行二次优化润色。

    Args:
        content: 原始生成内容
        task_type: 任务类型（script/character/storyboard/prompt 等）
        reference: 联网搜索到的参考资料
        extra_instruction: 额外优化指令

    Returns:
        优化后的内容（如果优化失败则返回原始内容）
    """
    task_prompts = {
        "script": "你是资深编剧。请优化以下剧本内容，改善剧情节奏、对白自然度和角色一致性。",
        "character_prompt": "你是角色设计专家。请优化以下图像提示词，使角色描述更精准、画面质量更高，保持英文输出。",
        "storyboard": "你是分镜导演。请优化以下分镜描述，改善镜头语言、构图和情绪表达。",
        "video_prompt": "你是视频生成提示词专家。请优化以下提示词，使画面更符合叙事需求。",
        "subtitle": "你是字幕编辑。请修正以下字幕中的错别字、语法问题，保持时间轴不变。",
        "dialogue": "你是对白编辑。请优化以下对白，使其更自然、符合角色性格。",
    }

    system_prompt = task_prompts.get(task_type, "请优化以下内容，提升质量和专业性。")

    user_msg = f"原始内容：\n{content}\n"
    if reference:
        user_msg += f"\n参考资料（来自联网搜索）：\n{reference}\n"
    if extra_instruction:
        user_msg += f"\n额外要求：{extra_instruction}\n"
    user_msg += "\n请直接输出优化后的内容，不要解释。"

    try:
        client = get_shared_llm_client()
        resp = await client.chat.completions.create(
            model=settings.exo_model_glm52,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.6,
            max_tokens=3000,
        )
        optimized = strip_think_tags(resp.choices[0].message.content or "")
        if optimized:
            return optimized
    except Exception as e:
        logger.warning("AI 优化失败 (task=%s): %s", task_type, e)

    # 优化失败时返回原始内容
    return content
