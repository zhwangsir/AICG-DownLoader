"""BaseAgent 基类单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import BaseAgent, _strip_markdown


class TestStripMarkdown:
    def test_no_markdown(self):
        assert _strip_markdown("hello") == "hello"

    def test_with_markdown_block(self):
        assert _strip_markdown("```json\n{\"a\": 1}\n```") == '{"a": 1}'

    def test_with_language_tag(self):
        assert _strip_markdown("```python\nprint(1)\n```") == "print(1)"


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
