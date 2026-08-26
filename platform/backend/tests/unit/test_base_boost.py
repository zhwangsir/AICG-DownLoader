"""BaseAgent 基类覆盖率补全测试（boost）。

针对既有 test_base.py 未触达的基础设施分支：
- aclose 关闭连接池
- call_llm：disable_thinking extra_body、异常埋点后重抛、流式空 choices 跳过、
  非流式 content 空回退 reasoning_content
- call_comfyui：提交异常埋点后重抛
- get_comfyui_result：execution_error 状态检测（有/无异常详情两分支）
- _get_worker_loads：多设备取最大空闲显存 / 无设备回退 / 全零显存回退 /
  非 200 跳过 / 异常跳过
- _select_workers_by_load：空 loads、负载降序 + 轮询
- 图像/视频 worker 选择四个入口的成功与全不可用回退路径
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import BaseAgent, strip_think_tags
from app.config import settings
from app.services.model_gateway import model_gateway


class TestStripThinkTags:
    def test_strips_think_block(self):
        """含 </think> 的推理输出 → 只保留思维链之后的正文（L38）。"""
        assert strip_think_tags("<think>逐步推理</think>最终答案") == "最终答案"

    def test_strips_leftover_open_tag(self):
        assert strip_think_tags("<think>只有开标签") == "只有开标签"

    def test_plain_text_unchanged(self):
        assert strip_think_tags("plain") == "plain"


def _async_iter(items):
    """将列表包装为异步可迭代对象，用于 mock streaming 响应。"""
    mock = MagicMock()
    mock.__aiter__.return_value = iter(items)
    return mock


def _stream_chunk(content=None, reasoning=None):
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content
    chunk.choices[0].delta.reasoning_content = reasoning
    return chunk


class TestAclose:
    async def test_aclose_closes_http_client(self, base_agent):
        """aclose 关闭底层 httpx 连接池（L101）。"""
        assert not base_agent.http.is_closed

        await base_agent.aclose()

        assert base_agent.http.is_closed


class TestCallLLMBoost:
    async def test_disable_thinking_adds_extra_body(self, base_agent):
        """disable_thinking=True → 注入 chat_template_kwargs 关闭推理模式（L132）。"""
        create = AsyncMock(return_value=_async_iter([_stream_chunk(content="ok")]))
        base_agent.llm_client.chat.completions.create = create

        result = await base_agent.call_llm(
            messages=[{"role": "user", "content": "hi"}], disable_thinking=True
        )

        assert result == "ok"
        assert create.call_args.kwargs["extra_body"] == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    async def test_error_records_gateway_metrics_and_raises(self, base_agent):
        """LLM 调用异常 → 网关错误指标 + 节点日志埋点后原样重抛（L141-145）。"""
        base_agent.llm_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("llm boom")
        )

        with patch.object(model_gateway, "record_call") as rec:
            with pytest.raises(RuntimeError, match="llm boom"):
                await base_agent.call_llm(messages=[{"role": "user", "content": "hi"}])

        rec.assert_called_once()
        assert rec.call_args.args[0] == "llm"
        assert rec.call_args.kwargs["error"] == "llm boom"

    async def test_streaming_skips_chunks_without_choices(self, base_agent):
        """流式 chunk 无 choices（心跳/keep-alive）→ 跳过不拼内容（L169）。"""
        heartbeat = MagicMock()
        heartbeat.choices = []
        base_agent.llm_client.chat.completions.create = AsyncMock(
            return_value=_async_iter([heartbeat, _stream_chunk(content="real")])
        )

        result = await base_agent.call_llm(messages=[{"role": "user", "content": "hi"}])

        assert result == "real"

    async def test_non_streaming_falls_back_to_reasoning(self, base_agent):
        """非流式 content 为空 → 回退 reasoning_content（L185）。"""
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = ""
        fake_resp.choices[0].message.reasoning_content = "chain-of-thought"

        base_agent.llm_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        result = await base_agent.call_llm(
            messages=[{"role": "user", "content": "hi"}], stream=False
        )

        assert result == "chain-of-thought"


class TestCallComfyUIBoost:
    async def test_submit_error_logs_and_raises(self, base_agent):
        """提交异常 → 节点日志埋点后原样重抛，非可重试异常不重试（L202-207）。"""
        with patch.object(
            base_agent.http, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.side_effect = ValueError("connection reset")

            with pytest.raises(ValueError, match="connection reset"):
                await base_agent.call_comfyui("http://worker", {"1": {}})

        assert mock_post.await_count == 1

    async def test_submit_http_error_status_raises(self, base_agent):
        """4xx 状态错误同样走异常埋点分支（不可重试，立即抛出）。"""
        import httpx

        request = httpx.Request("POST", "http://worker/prompt")
        response = httpx.Response(400, request=request)
        with patch.object(
            base_agent.http, "post", new_callable=AsyncMock
        ) as mock_post:
            bad_resp = MagicMock()
            bad_resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "bad request", request=request, response=response
                )
            )
            mock_post.return_value = bad_resp

            with pytest.raises(httpx.HTTPStatusError):
                await base_agent.call_comfyui("http://worker", {"1": {}})

        assert mock_post.await_count == 1


class TestGetComfyUIResultBoost:
    async def test_execution_error_raises_with_exception_message(self, base_agent):
        """history 报 execution_error → 提取异常详情并抛出（L294-303）。"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "prompt-1": {
                "status": {
                    "status_str": "error",
                    "messages": [
                        ["execution_start", {"prompt_id": "prompt-1"}],
                        ["execution_error", {"exception_message": "CUDA out of memory"}],
                    ],
                }
            }
        }
        with patch.object(base_agent.http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            with pytest.raises(RuntimeError, match="CUDA out of memory"):
                await base_agent.get_comfyui_result(
                    "http://worker", "prompt-1", timeout=1.0
                )

    async def test_execution_error_without_detail_uses_default(self, base_agent):
        """error 状态但无 execution_error 消息 → 使用默认异常文案。"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "prompt-1": {
                "status": {
                    "status_str": "error",
                    "messages": [["execution_start", {"prompt_id": "prompt-1"}]],
                }
            }
        }
        with patch.object(base_agent.http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            with pytest.raises(RuntimeError, match="ComfyUI 执行出错"):
                await base_agent.get_comfyui_result(
                    "http://worker", "prompt-1", timeout=1.0
                )


class TestGetWorkerLoads:
    async def test_devices_max_free_and_fallbacks(self, base_agent):
        """L331-344 全分支：

        - w1：多 GPU 取 vram_free/torch_vram_free 最小值中的最大者
        - w2：无设备信息 → 中等值 1.0
        - w3：设备显存全零 → 回退 1.0
        - w4：非 200 → 跳过
        - w5：请求异常 → 跳过
        """

        def _resp(status=200, payload=None):
            r = MagicMock()
            r.status_code = status
            r.json.return_value = payload if payload is not None else {}
            return r

        async def _get(url, **kwargs):
            base = url.split("/system_stats", 1)[0]
            mapping = {
                "http://w1": _resp(
                    200,
                    {
                        "devices": [
                            {"vram_free": 5000.0, "torch_vram_free": 4000.0},
                            {"vram_free": 9000.0, "torch_vram_free": 8000.0},
                        ]
                    },
                ),
                "http://w2": _resp(200, {"devices": []}),
                "http://w3": _resp(
                    200, {"devices": [{"vram_free": 0, "torch_vram_free": 0}]}
                ),
                "http://w4": _resp(500, {}),
            }
            if base not in mapping:
                raise RuntimeError("worker down")
            return mapping[base]

        with patch.object(base_agent.http, "get", new=AsyncMock(side_effect=_get)):
            loads = await base_agent._get_worker_loads(
                ["http://w1", "http://w2", "http://w3", "http://w4", "http://w5"]
            )

        assert loads == {"http://w1": 8000.0, "http://w2": 1.0, "http://w3": 1.0}


class TestSelectWorkersByLoad:
    def test_empty_loads_returns_empty(self, base_agent):
        """空 loads → 空列表（L352）。"""
        assert base_agent._select_workers_by_load({}, 3) == []

    def test_load_desc_then_url_stable_with_round_robin(self, base_agent):
        """空闲显存降序，并列按 URL 稳定排序；n 超候选数时轮询复用。"""
        loads = {"http://a": 100.0, "http://b": 200.0, "http://c": 200.0}

        out = base_agent._select_workers_by_load(loads, 5)

        assert out == ["http://b", "http://c", "http://a", "http://b", "http://c"]


class TestWorkerSelectionEntrypoints:
    """图像/视频 worker 选择入口（L372-373, L381, L393-394, L398-402）。"""

    async def test_image_worker_picks_max_free(self, base_agent, monkeypatch):
        monkeypatch.setattr(
            base_agent,
            "_get_worker_loads",
            AsyncMock(return_value={"http://w1": 10.0, "http://w2": 99.0}),
        )

        assert await base_agent.get_available_image_worker() == "http://w2"

    async def test_image_worker_all_down_returns_primary(self, base_agent, monkeypatch):
        """全部图像 worker 不可用 → 回退主端点让调用方快速失败。"""
        monkeypatch.setattr(
            base_agent, "_get_worker_loads", AsyncMock(return_value={})
        )

        assert (
            await base_agent.get_available_image_worker() == settings.comfyui_image_hq
        )

    async def test_image_workers_selected_by_load(self, base_agent, monkeypatch):
        monkeypatch.setattr(
            base_agent,
            "_get_worker_loads",
            AsyncMock(return_value={"http://w1": 50.0, "http://w2": 100.0}),
        )

        workers = await base_agent.get_available_image_workers(3)

        assert workers == ["http://w2", "http://w1", "http://w2"]

    async def test_image_workers_all_down_returns_primary_copies(
        self, base_agent, monkeypatch
    ):
        monkeypatch.setattr(
            base_agent, "_get_worker_loads", AsyncMock(return_value={})
        )

        workers = await base_agent.get_available_image_workers(2)

        assert workers == [settings.comfyui_image_hq] * 2

    async def test_video_worker_picks_max_free(self, base_agent, monkeypatch):
        monkeypatch.setattr(
            base_agent,
            "_get_worker_loads",
            AsyncMock(return_value={"http://v1": 5.0, "http://v2": 500.0}),
        )

        assert await base_agent.get_available_video_worker() == "http://v2"

    async def test_video_worker_all_down_returns_primary(self, base_agent, monkeypatch):
        monkeypatch.setattr(
            base_agent, "_get_worker_loads", AsyncMock(return_value={})
        )

        assert (
            await base_agent.get_available_video_worker() == settings.comfyui_video_a
        )

    async def test_video_workers_all_down_returns_primary_copies(
        self, base_agent, monkeypatch
    ):
        monkeypatch.setattr(
            base_agent, "_get_worker_loads", AsyncMock(return_value={})
        )

        workers = await base_agent.get_available_video_workers(2)

        assert workers == [settings.comfyui_video_a] * 2

    async def test_video_workers_selected_by_load(self, base_agent, monkeypatch):
        monkeypatch.setattr(
            base_agent,
            "_get_worker_loads",
            AsyncMock(return_value={"http://v1": 300.0, "http://v2": 900.0}),
        )

        workers = await base_agent.get_available_video_workers(3)

        assert workers == ["http://v2", "http://v1", "http://v2"]


class TestUploadMediaToComfyUI:
    """upload_media_to_comfyui 通用媒体上传（L259-275）。"""

    def _mock_http(self, base_agent, post_payload):
        get_resp = MagicMock()
        get_resp.content = b"media-bytes"
        get_resp.raise_for_status = MagicMock()
        post_resp = MagicMock()
        post_resp.json.return_value = post_payload
        post_resp.raise_for_status = MagicMock()
        get = patch.object(base_agent.http, "get", AsyncMock(return_value=get_resp))
        post = patch.object(base_agent.http, "post", AsyncMock(return_value=post_resp))
        return get, post

    async def test_success_preserves_source_extension(self, base_agent):
        """视频 URL（带 query 串）→ 去掉 query 取源文件名，统一走 /upload/image。"""
        get, post = self._mock_http(base_agent, {"name": "clip.mp4"})
        with get, post as mock_post:
            name = await base_agent.upload_media_to_comfyui(
                "http://worker", "http://x/path/clip.mp4?token=abc"
            )

        assert name == "clip.mp4"
        assert mock_post.call_args.args[0] == "http://worker/upload/image"
        files = mock_post.call_args.kwargs["files"]
        assert files["image"][0] == "clip.mp4"
        assert files["image"][1] == b"media-bytes"
        assert files["image"][2] == "application/octet-stream"

    async def test_no_extension_uses_fallback_name(self, base_agent):
        """URL 路径无扩展名 → 用 fallback_name；响应缺 name 时返回该文件名。"""
        get, post = self._mock_http(base_agent, {})
        with get, post as mock_post:
            name = await base_agent.upload_media_to_comfyui(
                "http://worker", "http://x/download", fallback_name="input.bin"
            )

        assert name == "input.bin"
        assert mock_post.call_args.kwargs["files"]["image"][0] == "input.bin"

    async def test_url_without_path_uses_fallback_name(self, base_agent):
        """URL 无路径段（裸域名）→ 同样回退 fallback_name。"""
        get, post = self._mock_http(base_agent, {"name": "stored.bin"})
        with get, post:
            name = await base_agent.upload_media_to_comfyui(
                "http://worker", "http://x", fallback_name="input.bin"
            )

        assert name == "stored.bin"


class TestExecuteNotImplementedBoost:
    async def test_base_execute_raises(self):
        """基类 execute 必须由子类实现。"""
        agent = BaseAgent("bare")
        with pytest.raises(NotImplementedError):
            await agent.execute()
        await agent.aclose()
