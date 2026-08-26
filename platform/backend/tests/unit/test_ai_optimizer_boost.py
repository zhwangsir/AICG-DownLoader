"""AI 优化模块覆盖率补强测试 — web_search 全网路与 optimize_content 成功路径。

conftest 的 autouse fixture 会把 app.agents.ai_optimizer.web_search 替换为 AsyncMock，
因此本文件在模块导入期绑定真实函数引用（fixture 只 patch 模块属性，不影响已绑定的
本地名称），httpx.AsyncClient 与 get_shared_llm_client 全部 mock，不发起真实网络请求。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.ai_optimizer import optimize_content
from app.agents.ai_optimizer import web_search as real_web_search
from app.config import settings


def _make_http_client(*, get_return: MagicMock | None = None, get_side_effect=None) -> MagicMock:
    """构造支持 async with 协议的 httpx.AsyncClient 替身。"""
    client = MagicMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=get_side_effect)
    else:
        client.get = AsyncMock(return_value=get_return)
    return client


def _json_response(status_code: int, payload: dict) -> MagicMock:
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = payload
    return resp


class TestWebSearch:
    async def test_ddg_and_wiki_success(self):
        """DuckDuckGo 摘要 + 相关主题 + Wikipedia 条目全部拼接（32-81 主路径）。"""
        ddg_resp = _json_response(200, {
            "Abstract": "DDG 摘要文本",
            "RelatedTopics": [
                {"Text": "相关主题一"},
                {"Text": "相关主题二"},
                {"NoText": True},  # 缺 Text 字段被跳过
            ],
        })
        wiki_resp = _json_response(200, {
            "query": {
                "search": [
                    {"title": "条目A", "snippet": '含<span class="searchmatch">关键词</span>的片段'},
                    {"title": "", "snippet": "无标题应跳过"},
                    {"title": "条目B", "snippet": ""},  # 空片段应跳过
                ]
            }
        })
        ddg_client = _make_http_client(get_return=ddg_resp)
        wiki_client = _make_http_client(get_return=wiki_resp)

        with patch(
            "app.agents.ai_optimizer.httpx.AsyncClient",
            side_effect=[ddg_client, wiki_client],
        ):
            result = await real_web_search("短剧剧情设计", max_results=5)

        assert "[DuckDuckGo] DDG 摘要文本" in result
        assert "[参考] 相关主题一" in result
        assert "[参考] 相关主题二" in result
        # Wikipedia 片段内 searchmatch 标签被剥离
        assert "[Wiki] 条目A: 含关键词的片段" in result
        assert "无标题应跳过" not in result
        assert "条目B" not in result
        # 两个数据源各自发起一次请求且带查询参数
        assert ddg_client.get.await_count == 1
        assert ddg_client.get.call_args.kwargs["params"]["q"] == "短剧剧情设计"
        assert wiki_client.get.await_count == 1
        assert wiki_client.get.call_args.kwargs["params"]["srsearch"] == "短剧剧情设计"

    async def test_both_sources_raise_returns_empty(self):
        """两个数据源都异常 → 告警后返回空串（51-52, 75-76, 78-79）。"""
        ddg_client = _make_http_client(get_side_effect=RuntimeError("DDG 网络不可达"))
        wiki_client = _make_http_client(get_side_effect=RuntimeError("Wiki 网络不可达"))

        with patch(
            "app.agents.ai_optimizer.httpx.AsyncClient",
            side_effect=[ddg_client, wiki_client],
        ):
            result = await real_web_search("x")

        assert result == ""

    async def test_non_200_status_skipped(self):
        """非 200 响应不取 JSON：DDG 503 跳过，Wiki 200 正常拼接（41/68 分支）。"""
        ddg_resp = _json_response(503, {})
        wiki_resp = _json_response(200, {
            "query": {"search": [{"title": "条目C", "snippet": "片段C"}]}
        })
        ddg_client = _make_http_client(get_return=ddg_resp)
        wiki_client = _make_http_client(get_return=wiki_resp)

        with patch(
            "app.agents.ai_optimizer.httpx.AsyncClient",
            side_effect=[ddg_client, wiki_client],
        ):
            result = await real_web_search("x")

        assert result == "[Wiki] 条目C: 片段C"
        ddg_resp.json.assert_not_called()

    async def test_empty_payloads_return_empty_string(self):
        """200 但无摘要/无相关主题/无搜索结果 → 空串（78-79）。"""
        ddg_resp = _json_response(200, {"Abstract": "", "RelatedTopics": None})
        wiki_resp = _json_response(200, {"query": {"search": None}})
        ddg_client = _make_http_client(get_return=ddg_resp)
        wiki_client = _make_http_client(get_return=wiki_resp)

        with patch(
            "app.agents.ai_optimizer.httpx.AsyncClient",
            side_effect=[ddg_client, wiki_client],
        ):
            result = await real_web_search("x")

        assert result == ""


def _make_llm_client(content: str | None) -> MagicMock:
    """构造 get_shared_llm_client 替身：chat.completions.create 返回固定 content。"""
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


class TestOptimizeContent:
    async def test_success_strips_think_tags(self):
        """优化成功：剥离 <think> 思维链后返回（130-132）。"""
        client = _make_llm_client("<think>推理过程</think>优化后的内容")

        with patch("app.agents.ai_optimizer.get_shared_llm_client", return_value=client):
            result = await optimize_content("原始内容", "script")

        assert result == "优化后的内容"
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == settings.exo_model_glm52
        assert kwargs["temperature"] == 0.6
        assert kwargs["max_tokens"] == 3000
        # script 任务类型命中编剧系统提示词
        assert kwargs["messages"][0]["role"] == "system"
        assert "资深编剧" in kwargs["messages"][0]["content"]

    async def test_reference_and_extra_instruction_injected(self):
        """传入参考资料与额外要求 → 注入 user 消息（114, 116）。"""
        client = _make_llm_client("优化结果")

        with patch("app.agents.ai_optimizer.get_shared_llm_client", return_value=client):
            result = await optimize_content(
                "原始内容",
                "dialogue",
                reference="参考XYZ",
                extra_instruction="保持口语化",
            )

        assert result == "优化结果"
        user_msg = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "原始内容" in user_msg
        assert "参考资料（来自联网搜索）" in user_msg
        assert "参考XYZ" in user_msg
        assert "额外要求：保持口语化" in user_msg

    async def test_empty_optimized_returns_original(self):
        """LLM 返回空串 → 回退原始内容（130-131 假分支 → 137）。"""
        client = _make_llm_client("")

        with patch("app.agents.ai_optimizer.get_shared_llm_client", return_value=client):
            result = await optimize_content("原始内容", "script")

        assert result == "原始内容"

    async def test_none_content_returns_original(self):
        """LLM message.content 为 None → 按空串处理，回退原始内容。"""
        client = _make_llm_client(None)

        with patch("app.agents.ai_optimizer.get_shared_llm_client", return_value=client):
            result = await optimize_content("原始内容", "script")

        assert result == "原始内容"

    async def test_llm_exception_returns_original(self):
        """LLM 调用异常 → 告警并回退原始内容（133-137）。"""
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("LLM 超时"))

        with patch("app.agents.ai_optimizer.get_shared_llm_client", return_value=client):
            result = await optimize_content("原始内容", "subtitle")

        assert result == "原始内容"

    async def test_unknown_task_type_uses_default_prompt(self):
        """未知任务类型 → 默认系统提示词（110 的 get 兜底分支）。"""
        client = _make_llm_client("优化结果")

        with patch("app.agents.ai_optimizer.get_shared_llm_client", return_value=client):
            result = await optimize_content("原始内容", "unknown_task")

        assert result == "优化结果"
        system_msg = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert system_msg == "请优化以下内容，提升质量和专业性。"
