"""LTX-Video 分镜预览服务客户端 — 5s 视频约 20s 生成。

P4.3 设计目标：
- 不替代 xDiT/HunyuanVideo 1.5 正式视频生成
- 作为分镜预览加速：用户快速判断分镜效果，再决定是否生成正式视频
- LTX-Video 2B（8GB 显存，pc01 RTX 5090），低分辨率预览

LTX-Video 服务 API 契约（由 pc01 部署的 FastAPI wrapper 提供）：
- POST /v1/video/preview   提交预览任务，返回 {"task_id": str}
- GET  /v1/video/status/{task_id}   返回 {"status": "pending|running|succeeded|failed", "progress": int}
- GET  /v1/video/result/{task_id}   返回 {"video_url": str, "duration_seconds": float}
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import httpx

from app.config import settings
from app.core.retry import with_retry

logger = logging.getLogger(__name__)


class LTXVideoServiceError(RuntimeError):
    """LTX-Video 预览服务调用异常。"""


class LTXVideoService:
    """LTX-Video 2B 分镜预览服务客户端。

    使用 BaseAgent 注入的 httpx.AsyncClient（trust_env=False 避免 macOS 代理拦截 IPv6）。
    所有调用经 with_retry 装饰，对瞬时网络错误自动重试。

    设计为轻量预览路径：
    - 默认 65 帧（约 2.7s @ 24fps），足够判断分镜构图
    - 默认 512x320 低分辨率，加速生成
    - 不参与正式视频生成，仅用于 storyboard 预览
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.ltx_video_endpoint
        # trust_env=False 同 base.py：避免 macOS 系统 HTTP 代理拦截 IPv6 请求
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.ltx_video_timeout, trust_env=False
        )

    @with_retry(max_attempts=2, base_delay=0.5, max_delay=3.0)
    async def submit_preview(
        self,
        image_url: str,
        prompt: str = "",
        negative_prompt: str = "",
        num_frames: int | None = None,
        resolution: str | None = None,
    ) -> str:
        """提交分镜预览任务，返回 task_id。

        参数：
            image_url: 分镜关键帧图片 URL
            prompt: 运动描述提示词（英文，描述镜头运动/角色动作）
            negative_prompt: 负面提示词
            num_frames: 帧数，None 时用默认值 65
            resolution: 'WxH' 格式，None 时用默认值 512x320
        """
        frames = num_frames or settings.ltx_video_default_num_frames
        res = resolution or settings.ltx_video_default_resolution

        payload = {
            "model": settings.ltx_video_model,
            "image": image_url,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "num_frames": frames,
            "resolution": res,
        }
        resp = await self.http.post(
            f"{self.endpoint}/v1/video/preview", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id", "")
        if not task_id:
            raise LTXVideoServiceError(f"LTX-Video 未返回 task_id: {data}")
        logger.info("LTX-Video 提交预览任务: task_id=%s frames=%s", task_id, frames)
        return task_id

    async def poll_status(
        self,
        task_id: str,
        progress_callback: Callable[[int, str], None] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """轮询任务状态直到完成或超时。

        成功返回最终 status 数据：
        {"status": "succeeded", "progress": 100, "message": str}
        """
        timeout = timeout or settings.ltx_video_timeout
        deadline = time.time() + timeout
        last_progress = -1

        while time.time() < deadline:
            resp = await self.http.get(f"{self.endpoint}/v1/video/status/{task_id}")
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "unknown")
            progress = int(data.get("progress", 0))
            message = data.get("message", "")

            if progress_callback and progress != last_progress:
                progress_callback(progress, message)
                last_progress = progress

            if status == "succeeded":
                logger.info("LTX-Video 预览完成: task_id=%s", task_id)
                return data
            if status == "failed":
                err = data.get("error", "LTX-Video 预览任务失败")
                logger.error("LTX-Video 预览失败: task_id=%s err=%s", task_id, err)
                raise LTXVideoServiceError(f"LTX-Video 预览 {task_id} 失败: {err}")

            await asyncio.sleep(2.0)

        raise TimeoutError(f"LTX-Video 预览 {task_id} 超时 {timeout}s")

    @with_retry(max_attempts=2, base_delay=0.5, max_delay=2.0)
    async def get_result(self, task_id: str) -> dict[str, Any]:
        """获取已完成预览任务的结果。

        返回 {"video_url": str, "duration_seconds": float, "task_id": str}
        """
        resp = await self.http.get(f"{self.endpoint}/v1/video/result/{task_id}")
        resp.raise_for_status()
        data = resp.json()
        video_url = data.get("video_url", "")
        if not video_url:
            raise LTXVideoServiceError(f"LTX-Video 结果缺少 video_url: {data}")
        return {
            "video_url": video_url,
            "duration_seconds": float(data.get("duration_seconds", 0.0)),
            "task_id": task_id,
        }

    async def generate_preview(
        self,
        image_url: str,
        prompt: str = "",
        negative_prompt: str = "",
        num_frames: int | None = None,
        resolution: str | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """端到端分镜预览：提交 → 轮询 → 获取结果。

        返回 {"video_url": str, "duration_seconds": float, "task_id": str}
        """
        if progress_callback:
            progress_callback(10, "提交 LTX-Video 预览任务")
        task_id = await self.submit_preview(
            image_url=image_url,
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_frames=num_frames,
            resolution=resolution,
        )

        if progress_callback:
            progress_callback(30, "LTX-Video 推理中")
        await self.poll_status(task_id, progress_callback=progress_callback)

        if progress_callback:
            progress_callback(95, "获取预览结果")
        result = await self.get_result(task_id)

        if progress_callback:
            progress_callback(100, "分镜预览完成")
        return result

    def is_enabled(self) -> bool:
        """检查 LTX-Video 预览是否启用。

        受 settings.ltx_video_enabled 控制，默认关闭，需部署后显式开启。
        """
        return bool(settings.ltx_video_enabled)
