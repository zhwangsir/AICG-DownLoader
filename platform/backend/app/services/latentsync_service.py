"""LatentSync 1.6 唇形同步服务客户端 — 字节开源。

P4.4 设计目标：
- 将视频人物口型与配音音频对齐，消除口型脱节
- LatentSync 1.6 (512 分辨率, 最低 6GB 显存, 2-5s/秒视频)
- 失败时由 Agent 自动降级返回原视频，不影响成片流程

LatentSync 服务 API 契约（由 workstation 部署的 FastAPI wrapper 提供）：
- POST /v1/video/upload   上传视频/音频/参考图，返回 {"filename": str}
- POST /v1/lipsync/submit 提交唇形同步任务，返回 {"task_id": str}
- GET  /v1/lipsync/status/{task_id}   返回 {"status": "pending|running|succeeded|failed", "progress": int, "message": str}
- GET  /v1/lipsync/result/{task_id}   返回 {"video_url": str, "duration_seconds": float}
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


class LatentSyncServiceError(RuntimeError):
    """LatentSync 唇形同步服务调用异常。"""


class LatentSyncService:
    """LatentSync 1.6 唇形同步服务客户端。

    使用 BaseAgent 注入的 httpx.AsyncClient（trust_env=False 避免 macOS 代理拦截 IPv6）。
    所有调用经 with_retry 装饰，对瞬时网络错误自动重试。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.latentsync_endpoint
        # trust_env=False 同 base.py：避免 macOS 系统 HTTP 代理拦截 IPv6 请求
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.latentsync_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def upload_media(
        self,
        media_url: str,
        media_type: str = "video",
    ) -> str:
        """下载媒体文件并上传到 LatentSync 服务，返回服务端文件名。

        Args:
            media_url: 媒体文件 URL（视频/音频/参考图）
            media_type: 媒体类型（'video' / 'audio' / 'reference'），用于服务端分目录存储
        """
        # 1. 下载媒体文件
        resp = await self.http.get(media_url)
        resp.raise_for_status()
        media_bytes = resp.content

        # 2. 推断扩展名
        ext = "mp4" if media_type == "video" else ("mp3" if media_type == "audio" else "png")
        content_type = (
            "video/mp4" if media_type == "video"
            else ("audio/mpeg" if media_type == "audio" else "image/png")
        )

        # 3. 上传到 LatentSync 服务
        upload_resp = await self.http.post(
            f"{self.endpoint}/v1/video/upload",
            files={"media": (f"input.{ext}", media_bytes, content_type)},
            data={"type": media_type},
        )
        upload_resp.raise_for_status()
        result = upload_resp.json()
        filename = result.get("filename", f"input.{ext}")
        logger.debug("LatentSync 上传 %s: %s -> %s", media_type, media_url, filename)
        return filename

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def submit_task(
        self,
        video_filename: str,
        audio_filename: str,
        scene_id: int = 0,
        reference_image_filename: str | None = None,
    ) -> str:
        """提交唇形同步任务，返回 task_id。

        参数从 settings 读取默认值（model / resolution / seed）。
        """
        payload: dict[str, Any] = {
            "model": settings.latentsync_model,
            "video": video_filename,
            "audio": audio_filename,
            "scene_id": scene_id,
            "resolution": settings.latentsync_resolution,
            "seed": settings.latentsync_seed,
        }
        if reference_image_filename:
            payload["reference_image"] = reference_image_filename

        resp = await self.http.post(
            f"{self.endpoint}/v1/lipsync/submit", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id", "")
        if not task_id:
            raise LatentSyncServiceError(f"LatentSync 未返回 task_id: {data}")
        logger.info(
            "LatentSync 提交任务: scene_id=%s task_id=%s", scene_id, task_id
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

        失败抛出 LatentSyncServiceError；超时抛出 TimeoutError。
        """
        timeout = timeout or settings.latentsync_timeout
        deadline = time.time() + timeout
        last_progress = -1

        while time.time() < deadline:
            resp = await self.http.get(
                f"{self.endpoint}/v1/lipsync/status/{task_id}"
            )
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
                logger.info("LatentSync 任务完成: task_id=%s", task_id)
                return data
            if status == "failed":
                err = data.get("error", "LatentSync 任务执行失败")
                logger.error("LatentSync 任务失败: task_id=%s err=%s", task_id, err)
                raise LatentSyncServiceError(
                    f"LatentSync 任务 {task_id} 失败: {err}"
                )

            await asyncio.sleep(2.0)

        raise TimeoutError(f"LatentSync 任务 {task_id} 超时 {timeout}s")

    @with_retry(max_attempts=2, base_delay=0.5, max_delay=2.0)
    async def get_result(self, task_id: str) -> dict[str, Any]:
        """获取已完成任务的结果。

        返回 {"video_url": str, "duration_seconds": float, "task_id": str}
        """
        resp = await self.http.get(f"{self.endpoint}/v1/lipsync/result/{task_id}")
        resp.raise_for_status()
        data = resp.json()
        video_url = data.get("video_url", "")
        if not video_url:
            raise LatentSyncServiceError(
                f"LatentSync 结果缺少 video_url: {data}"
            )
        return {
            "video_url": video_url,
            "duration_seconds": float(data.get("duration_seconds", 0.0)),
            "task_id": task_id,
        }

    async def sync_lip(
        self,
        video_url: str,
        audio_url: str,
        scene_id: int = 0,
        reference_image_url: str | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """端到端唇形同步：上传视频+音频 → 提交任务 → 轮询 → 获取结果。

        返回 {"video_url": str, "duration_seconds": float, "task_id": str}
        """
        # 1. 上传视频
        if progress_callback:
            progress_callback(5, "上传视频到 LatentSync")
        video_filename = await self.upload_media(video_url, media_type="video")

        # 2. 上传音频
        if progress_callback:
            progress_callback(15, "上传配音音频到 LatentSync")
        audio_filename = await self.upload_media(audio_url, media_type="audio")

        # 3. 可选：上传参考图
        reference_filename: str | None = None
        if reference_image_url:
            if progress_callback:
                progress_callback(20, "上传角色参考图")
            reference_filename = await self.upload_media(
                reference_image_url, media_type="reference"
            )

        # 4. 提交生成任务
        if progress_callback:
            progress_callback(30, "提交 LatentSync 唇形同步任务")
        task_id = await self.submit_task(
            video_filename=video_filename,
            audio_filename=audio_filename,
            scene_id=scene_id,
            reference_image_filename=reference_filename,
        )

        # 5. 轮询状态（透传 progress_callback，LatentSync 上报 0-100）
        if progress_callback:
            progress_callback(40, "LatentSync 推理中")
        await self.poll_status(task_id, progress_callback=progress_callback)

        # 6. 获取结果
        if progress_callback:
            progress_callback(95, "获取唇形同步结果")
        result = await self.get_result(task_id)

        if progress_callback:
            progress_callback(100, "唇形同步完成")
        return result
