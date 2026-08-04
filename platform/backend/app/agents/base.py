"""Agent 基类 — 对接 EXO LLM 和 ComfyUI Worker 池。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, TypeVar

import httpx
from openai import AsyncOpenAI

from app.config import settings
from app.core.retry import with_retry


T = TypeVar("T")


def _strip_markdown(text: str) -> str:
    """去除 LLM 输出中可能包裹的 markdown 代码块。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def strip_think_tags(text: str) -> str:
    """剥离推理模型内联进 content 的思维链（<think>...</think>）。"""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.replace("<think>", "").strip()


class BaseAgent:
    """所有 Agent 的基类，提供 LLM 调用和 ComfyUI 调用能力。

    LLM 走 spark01 vLLM（qwen3.6-35b-a3b-awq，双机 TP2）。
    ComfyUI 走 workstation LB 入口 8188（4×RTX PRO 6000 集群）。
    """

    def __init__(self, name: str):
        self.name = name
        # trust_env=False 避免 macOS 系统 HTTP 代理（如 127.0.0.1:7890）
        # 拦截内网 IPv6 / Tailscale 地址请求导致 502
        self.http = httpx.AsyncClient(timeout=600.0, trust_env=False)
        self.llm_client = AsyncOpenAI(
            base_url=settings.exo_base_url,
            api_key=settings.exo_api_key,
            http_client=self.http,
        )

    async def aclose(self) -> None:
        """关闭底层 httpx 连接池（应用关闭时调用）。"""
        await self.http.aclose()

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def call_llm(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.8,
        max_tokens: int = 8000,
        response_format_json: bool = False,
        stream: bool = True,
        disable_thinking: bool = False,
    ) -> str:
        """调用 EXO 集群的 LLM（OpenAI 兼容 API）。

        GLM-5.2 默认启用思考模式，reasoning_content 和 content 分离。
        使用 streaming 模式避免长时间等待超时；若 content 为空则回退到 reasoning_content。
        当 response_format_json=True 时自动去除 markdown 代码块包裹。
        disable_thinking=True 时通过 chat_template_kwargs 关闭 Nemotron 推理模式，
        适用于结构化 JSON 输出场景（提示词重写、质检），避免推理链耗尽 token。
        """
        kwargs: dict[str, Any] = dict(
            model=model or settings.exo_model_glm52,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )
        if response_format_json:
            kwargs["response_format"] = {"type": "json_object"}
        if disable_thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

        if stream:
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            resp = await self.llm_client.chat.completions.create(**kwargs)
            async for chunk in resp:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
            content = "".join(content_parts)
            if not content:
                content = "".join(reasoning_parts)
        else:
            kwargs["stream"] = False
            resp = await self.llm_client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            content = msg.content or ""
            if not content:
                content = getattr(msg, "reasoning_content", "") or ""

        # Nemotron 等推理模型会把思考过程内联进 content（<think>...</think>），
        # 下游 JSON 解析前必须剥离；GLM 走 reasoning_content 字段不受影响
        content = strip_think_tags(content)

        if response_format_json:
            content = _strip_markdown(content)
        return content

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def call_comfyui(
        self,
        worker_url: str,
        workflow_json: dict,
    ) -> dict[str, Any]:
        """提交工作流到 ComfyUI Worker，返回 prompt_id。"""
        resp = await self.http.post(
            f"{worker_url}/prompt",
            json={"prompt": workflow_json},
        )
        resp.raise_for_status()
        return resp.json()

    async def upload_image_to_comfyui(
        self,
        worker_url: str,
        image_url: str,
    ) -> str:
        """下载图片并上传到 ComfyUI 的 input 目录，返回文件名。"""
        img_resp = await self.http.get(image_url)
        img_resp.raise_for_status()
        img_bytes = img_resp.content

        upload_resp = await self.http.post(
            f"{worker_url}/upload/image",
            files={"image": ("input.png", img_bytes, "image/png")},
            data={"type": "input", "overwrite": "true"},
        )
        upload_resp.raise_for_status()
        result = upload_resp.json()
        return result.get("name", "input.png")

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def get_comfyui_result(
        self,
        worker_url: str,
        prompt_id: str,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """轮询 ComfyUI 执行结果，检测到执行错误立即抛出。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = await self.http.get(f"{worker_url}/history/{prompt_id}")
            data = resp.json()
            entry = data.get(prompt_id, {})
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                # 提取第一个异常信息
                exception_msg = "ComfyUI 执行出错"
                for msg in status.get("messages", []):
                    if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "execution_error":
                        exception_msg = msg[1].get("exception_message", exception_msg)
                        break
                raise RuntimeError(f"ComfyUI {prompt_id} 执行失败: {exception_msg}")
            outputs = entry.get("outputs", {})
            if outputs:
                return outputs
            await asyncio.sleep(2.0)
        raise TimeoutError(f"ComfyUI {prompt_id} 超时 {timeout}s")

    async def _get_worker_loads(self, candidates: list[str]) -> dict[str, float]:
        """检测候选 Worker，返回可用 Worker 及其空闲显存（MB）。

        ComfyUI /system_stats 返回的 devices 中包含 vram_free / torch_vram_free。
        取两者中的最小值作为当前可用显存，值越大表示负载越低。
        """
        loads: dict[str, float] = {}
        for url in candidates:
            try:
                resp = await self.http.get(f"{url}/system_stats", timeout=5.0)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                devices = data.get("devices", [])
                if not devices:
                    # 无设备信息时认为可用，但负载未知，给中等值
                    loads[url] = 1.0
                    continue
                # 取所有 GPU 中最大的空闲显存（多卡机器取最空的那张）
                max_free = 0.0
                for dev in devices:
                    vram_free = float(dev.get("vram_free", 0) or 0)
                    torch_vram_free = float(dev.get("torch_vram_free", 0) or 0)
                    free = min(vram_free, torch_vram_free)
                    if free > max_free:
                        max_free = free
                loads[url] = max_free if max_free > 0 else 1.0
            except Exception:
                continue
        return loads

    def _select_workers_by_load(self, loads: dict[str, float], n: int) -> list[str]:
        """根据空闲显存从大到小选择 n 个 Worker；负载相同时用轮询打破平衡。"""
        if not loads:
            return []
        # 按空闲显存降序，相同则按 URL 稳定排序以保证确定性
        sorted_urls = sorted(loads.keys(), key=lambda u: (-loads[u], u))
        return [sorted_urls[i % len(sorted_urls)] for i in range(n)]

    async def get_available_image_worker(self) -> str:
        """返回当前可用的图像生成 ComfyUI Worker URL，并按 GPU 负载均衡。

        注意：图像任务只应使用专用图像 Worker（image_hq / image_fast）。
        视频 Worker 通常启用 sageattention 等针对视频模型的优化，对 SD 1.5
        等 head_dim=40 的图像模型会触发 `headdim should be in [64, 96, 128]`
        断言失败，因此不再 fallback 到视频 Worker。
        """
        candidates = [
            settings.comfyui_image_hq,
            settings.comfyui_image_fast,
        ]
        loads = await self._get_worker_loads(candidates)
        if not loads:
            # 若图像 Worker 都不可用，返回默认 hq，让调用方快速失败
            return settings.comfyui_image_hq
        workers = self._select_workers_by_load(loads, 1)
        return workers[0]

    async def get_available_image_workers(self, n: int) -> list[str]:
        """返回 n 个图像 Worker URL，按 GPU 空闲显存优先分配。"""
        candidates = [
            settings.comfyui_image_hq,
            settings.comfyui_image_fast,
        ]
        loads = await self._get_worker_loads(candidates)
        if not loads:
            return [settings.comfyui_image_hq] * n
        return self._select_workers_by_load(loads, n)

    async def get_available_video_worker(self) -> str:
        """返回当前可用的视频生成 Worker URL，并按 GPU 负载均衡。"""
        candidates = [
            settings.comfyui_video_a,
            settings.comfyui_video_b,
        ]
        loads = await self._get_worker_loads(candidates)
        if not loads:
            # 若都不可用，返回默认 video_a，让调用方自行报错
            return settings.comfyui_video_a
        workers = self._select_workers_by_load(loads, 1)
        return workers[0]

    async def get_available_video_workers(self, n: int) -> list[str]:
        """返回 n 个视频 Worker URL，按 GPU 空闲显存优先分配。"""
        candidates = [
            settings.comfyui_video_a,
            settings.comfyui_video_b,
        ]
        loads = await self._get_worker_loads(candidates)
        if not loads:
            return [settings.comfyui_video_a] * n
        return self._select_workers_by_load(loads, n)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """子类实现具体逻辑。"""
        raise NotImplementedError
