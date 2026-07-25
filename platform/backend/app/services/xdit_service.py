"""xDiT 推理引擎客户端 — HunyuanVideo 1.5 多卡并行视频生成。

xDiT 是独立推理引擎（非 ComfyUI 节点），通过 FastAPI 服务对外提供 HTTP API。
本模块封装 xDiT 服务的客户端调用：提交任务 → 轮询状态 → 获取结果。

P4.1 设计目标：
- 替换 Wan 2.2 单卡 ComfyUI 工作流（E2E 实测 691s/2 场景）
- HunyuanVideo 1.5 (8.3B) + xDiT 4 卡并行（cfg=2 + ulysses=2）
- 单场景 5s 视频理论 45-70s（5-8× 加速）

xDiT 服务 API 契约（由 Workstation 部署的 FastAPI wrapper 提供）：
- POST /v1/video/generate   提交任务，返回 {"task_id": str}
- GET  /v1/video/status/{task_id}   返回 {"status": "pending|running|succeeded|failed", "progress": int, "message": str}
- GET  /v1/video/result/{task_id}   返回 {"video_url": str, "duration_seconds": int}
- POST /v1/video/upload   上传分镜图片，返回 {"filename": str}
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


class XDiTServiceError(RuntimeError):
    """xDiT 服务调用异常。"""


class XDiTService:
    """xDiT 推理引擎客户端。

    使用 BaseAgent 注入的 httpx.AsyncClient（trust_env=False 避免 macOS 代理拦截 IPv6）。
    所有调用经 with_retry 装饰，对瞬时网络错误自动重试。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.xdit_endpoint
        # trust_env=False 同 base.py：避免 macOS 系统 HTTP 代理拦截 IPv6 请求
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.xdit_request_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def upload_image(self, image_url: str) -> str:
        """下载分镜图片并上传到 xDiT 服务，返回服务端文件名。

        xDiT 服务接收原始图片字节，存储到本地 input 目录供推理使用。
        """
        # 1. 下载图片
        img_resp = await self.http.get(image_url)
        img_resp.raise_for_status()
        img_bytes = img_resp.content

        # 2. 上传到 xDiT 服务
        upload_resp = await self.http.post(
            f"{self.endpoint}/v1/video/upload",
            files={"image": ("input.png", img_bytes, "image/png")},
        )
        upload_resp.raise_for_status()
        result = upload_resp.json()
        filename = result.get("filename", "input.png")
        logger.debug("xDiT 上传图片: %s -> %s", image_url, filename)
        return filename

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def submit_task(
        self,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        scene_id: int = 0,
        duration_seconds: int | None = None,
    ) -> str:
        """提交视频生成任务，返回 task_id。

        参数从 settings 读取默认值（model / num_frames / resolution / 并行策略 / 采样参数），
        允许调用方按场景覆盖 duration_seconds。
        """
        # 根据目标时长计算帧数：HunyuanVideo 1.5 原生 97 帧 ≈ 4s @ 24fps
        # 超过 4s 的视频由 RIFLEx 扩展（P4.4 后处理阶段实现）
        if duration_seconds is None:
            num_frames = settings.xdit_num_frames
        else:
            # 24fps，向上对齐到 4 的倍数 + 1（HunyuanVideo 帧数约束）
            raw = max(21, duration_seconds * 24 + 1)
            num_frames = ((raw - 1) // 4) * 4 + 1

        payload = {
            "model": settings.xdit_model,
            "image": image_filename,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "scene_id": scene_id,
            "num_frames": num_frames,
            "resolution": settings.xdit_resolution,
            "cfg_parallel": settings.xdit_cfg_parallel,
            "ulysses_degree": settings.xdit_ulysses_degree,
            "pipefusion_parallel": settings.xdit_pipefusion_parallel,
            "steps": settings.xdit_steps,
            "cfg": settings.xdit_cfg,
            "seed": settings.xdit_seed,
        }
        resp = await self.http.post(
            f"{self.endpoint}/v1/video/generate", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id", "")
        if not task_id:
            raise XDiTServiceError(f"xDiT 未返回 task_id: {data}")
        logger.info(
            "xDiT 提交任务: scene_id=%s task_id=%s frames=%s",
            scene_id, task_id, num_frames,
        )
        return task_id

    async def poll_status(
        self,
        task_id: str,
        progress_callback: Callable[[int, str], None] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """轮询任务状态直到完成或超时。

        成功返回最终 status 数据：
        {"status": "succeeded", "progress": 100, "message": str, "elapsed_seconds": float}

        失败抛出 XDiTServiceError；超时抛出 TimeoutError。
        """
        timeout = timeout or settings.xdit_request_timeout
        deadline = time.time() + timeout
        last_progress = -1

        while time.time() < deadline:
            resp = await self.http.get(f"{self.endpoint}/v1/video/status/{task_id}")
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "unknown")
            progress = int(data.get("progress", 0))
            message = data.get("message", "")

            # 仅在进度变化时回调，避免刷屏
            if progress_callback and progress != last_progress:
                progress_callback(progress, message)
                last_progress = progress

            if status == "succeeded":
                logger.info("xDiT 任务完成: task_id=%s", task_id)
                return data
            if status == "failed":
                err = data.get("error", "xDiT 任务执行失败")
                logger.error("xDiT 任务失败: task_id=%s err=%s", task_id, err)
                raise XDiTServiceError(f"xDiT 任务 {task_id} 失败: {err}")

            await asyncio.sleep(settings.xdit_poll_interval)

        raise TimeoutError(f"xDiT 任务 {task_id} 超时 {timeout}s")

    @with_retry(max_attempts=2, base_delay=0.5, max_delay=2.0)
    async def get_result(self, task_id: str) -> dict[str, Any]:
        """获取已完成任务的结果。

        返回 {"video_url": str, "duration_seconds": int, "task_id": str}
        """
        resp = await self.http.get(f"{self.endpoint}/v1/video/result/{task_id}")
        resp.raise_for_status()
        data = resp.json()
        video_url = data.get("video_url", "")
        if not video_url:
            raise XDiTServiceError(f"xDiT 结果缺少 video_url: {data}")
        return {
            "video_url": video_url,
            "duration_seconds": int(data.get("duration_seconds", 0)),
            "task_id": task_id,
        }

    async def generate_video(
        self,
        image_url: str,
        prompt: str,
        negative_prompt: str = "",
        scene_id: int = 0,
        duration_seconds: int | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """端到端视频生成：上传图片 → 提交任务 → 轮询 → 获取结果。

        返回 {"video_url": str, "duration_seconds": int, "task_id": str}
        """
        # 1. 上传分镜图片
        if progress_callback:
            progress_callback(5, "上传分镜图片到 xDiT")
        image_filename = await self.upload_image(image_url)

        # 2. 提交生成任务
        if progress_callback:
            progress_callback(15, "提交 HunyuanVideo 1.5 任务")
        task_id = await self.submit_task(
            image_filename=image_filename,
            prompt=prompt,
            negative_prompt=negative_prompt,
            scene_id=scene_id,
            duration_seconds=duration_seconds,
        )

        # 3. 轮询状态（透传 progress_callback，xDiT 上报 0-100）
        if progress_callback:
            progress_callback(25, "xDiT 4 卡并行推理中")
        await self.poll_status(task_id, progress_callback=progress_callback)

        # 4. 获取结果
        if progress_callback:
            progress_callback(95, "获取视频结果")
        result = await self.get_result(task_id)

        if progress_callback:
            progress_callback(100, "视频生成完成")
        return result
