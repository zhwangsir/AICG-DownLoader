"""BaseAgent 基类单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agents.base as base_module
from app.agents.base import BaseAgent, _strip_markdown


class TestStripMarkdown:
    def test_no_markdown(self):
        assert _strip_markdown("hello") == "hello"

    def test_with_markdown_block(self):
        assert _strip_markdown("```json\n{\"a\": 1}\n```") == '{"a": 1}'

    def test_with_language_tag(self):
        assert _strip_markdown("```python\nprint(1)\n```") == "print(1)"


class TestSharedLLMClient:
    """M9.7 回归：游离调用点（ai_optimizer/rag_service/智能体辅助）必须复用
    进程级共享 AsyncOpenAI 客户端，而非每次请求新建连接池（socket 泄漏 + TCP 重建延迟）。
    close 后必须可重新懒加载，避免 lifespan shutdown 后残留失效引用。"""

    def teardown_method(self):
        # 每个用例后重置单例，避免跨测试污染
        base_module._shared_http = None
        base_module._shared_llm = None

    def test_get_shared_client_returns_singleton(self):
        c1 = base_module.get_shared_llm_client()
        c2 = base_module.get_shared_llm_client()
        assert c1 is c2
        assert base_module._shared_http is not None

    async def test_close_resets_singleton(self):
        c1 = base_module.get_shared_llm_client()
        await base_module.close_shared_llm_client()
        assert base_module._shared_http is None
        assert base_module._shared_llm is None
        # 关闭后可重新懒加载新实例
        c2 = base_module.get_shared_llm_client()
        assert c2 is not c1
        await base_module.close_shared_llm_client()

    async def test_close_is_idempotent_when_never_used(self):
        # 未初始化时调用 close 不应抛错
        await base_module.close_shared_llm_client()
        assert base_module._shared_llm is None


class TestBaseAgentInit:
    def test_name(self, base_agent):
        assert base_agent.name == "test_agent"
        assert base_agent.llm_client is not None
        assert base_agent.http is not None

    def test_execute_not_implemented(self, base_agent):
        with pytest.raises(NotImplementedError):
            # asyncio.run 不能直接用，因为 execute 是 async
            import asyncio

            asyncio.run(base_agent.execute())


def _async_iter(items):
    """将列表包装为异步可迭代对象，用于 mock streaming 响应。"""
    mock = MagicMock()
    mock.__aiter__.return_value = iter(items)
    return mock


class TestCallLLM:
    async def test_streaming_content(self, base_agent):
        """测试 streaming 模式下正常返回 content。"""
        fake_chunk1 = MagicMock()
        fake_chunk1.choices = [MagicMock()]
        fake_chunk1.choices[0].delta.content = "Hello"
        fake_chunk1.choices[0].delta.reasoning_content = None

        fake_chunk2 = MagicMock()
        fake_chunk2.choices = [MagicMock()]
        fake_chunk2.choices[0].delta.content = " World"
        fake_chunk2.choices[0].delta.reasoning_content = None

        base_agent.llm_client.chat.completions.create = AsyncMock(
            return_value=_async_iter([fake_chunk1, fake_chunk2])
        )

        result = await base_agent.call_llm(messages=[{"role": "user", "content": "hi"}])
        assert result == "Hello World"

    async def test_streaming_fallback_to_reasoning(self, base_agent):
        """测试 content 为空时回退到 reasoning_content。"""
        fake_chunk = MagicMock()
        fake_chunk.choices = [MagicMock()]
        fake_chunk.choices[0].delta.content = None
        fake_chunk.choices[0].delta.reasoning_content = "thinking"

        base_agent.llm_client.chat.completions.create = AsyncMock(
            return_value=_async_iter([fake_chunk])
        )

        result = await base_agent.call_llm(messages=[{"role": "user", "content": "hi"}])
        assert result == "thinking"

    async def test_non_streaming(self, base_agent):
        """测试非 streaming 模式。"""
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = "non-stream"
        fake_resp.choices[0].message.reasoning_content = None

        base_agent.llm_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        result = await base_agent.call_llm(
            messages=[{"role": "user", "content": "hi"}], stream=False
        )
        assert result == "non-stream"

    async def test_response_format_json_strips_markdown(self, base_agent):
        fake_chunk = MagicMock()
        fake_chunk.choices = [MagicMock()]
        fake_chunk.choices[0].delta.content = "```json\n{\"a\": 1}\n```"
        fake_chunk.choices[0].delta.reasoning_content = None

        base_agent.llm_client.chat.completions.create = AsyncMock(
            return_value=_async_iter([fake_chunk])
        )

        result = await base_agent.call_llm(
            messages=[{"role": "user", "content": "hi"}], response_format_json=True
        )
        assert result == '{"a": 1}'


class TestCallComfyUI:
    async def test_success(self, base_agent):
        with patch.object(base_agent.http, "post", new_callable=AsyncMock) as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"prompt_id": "abc-123"}
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = await base_agent.call_comfyui("http://worker", {"1": {}})
            assert result["prompt_id"] == "abc-123"


class TestUploadImageToComfyUI:
    async def test_success(self, base_agent, mock_httpx_get):
        with patch.object(base_agent.http, "post", new_callable=AsyncMock) as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"name": "uploaded.png"}
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = await base_agent.upload_image_to_comfyui(
                "http://worker", "http://x/image.png"
            )
            assert result == "uploaded.png"


class TestGetComfyUIResult:
    async def test_success(self, base_agent):
        with patch.object(base_agent.http, "get", new_callable=AsyncMock) as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "prompt-1": {"outputs": {"node1": {"images": []}}}
            }
            mock_get.return_value = mock_response

            result = await base_agent.get_comfyui_result(
                "http://worker", "prompt-1", timeout=0.1
            )
            assert "node1" in result

    async def test_timeout(self, base_agent):
        with patch.object(base_agent.http, "get", new_callable=AsyncMock) as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {}
            mock_get.return_value = mock_response

            with pytest.raises(TimeoutError):
                await base_agent.get_comfyui_result(
                    "http://worker", "prompt-1", timeout=0.05
                )
