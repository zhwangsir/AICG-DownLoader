"""xDiT 推理引擎客户端 — HunyuanVideo-I2V 视频生成。

xDiT 是独立推理引擎（非 ComfyUI 节点），通过 FastAPI 服务对外提供 HTTP API。
本模块封装 xDiT 服务的客户端调用：上传图片 → 提交任务 → 轮询状态 → 获取结果。

xDiT 服务 API 契约（Workstation 192.168.71.127:8288 部署，OpenAI 风格）：
- POST /v1/upload                       multipart 上传分镜图片，返回 {"image_url": str, "filename": str, "size": int}
- POST /v1/videos/generations?async_mode=true
  提交异步生成任务，body: GenerationRequest{model, prompt, image_url, num_frames,
  num_inference_steps, cfg, seed, size}，202 返回 {"task_id": str, "status": "pending"}
- GET  /v1/tasks/{task_id}
  返回 {"task_id", "status": "pending|running|succeeded|failed", ...}；
  succeeded 时含 "result": {"url", "elapsed", "num_frames", "size", "seed"}；
  failed 时含 "error": str
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

# 视频帧率（与服务端 VIDEO_FPS 一致），用于由帧数推算时长
_VIDEO_FPS = 24
# 推理预估时长（秒），仅用于轮询期间的进度估算
_ESTIMATED_INFERENCE_SECONDS = 240.0
# 服务端 GenerationRequest 契约：num_frames ∈ [5, 257] 且对齐 4k+1
_MAX_NUM_FRAMES = 257


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
        """下载分镜图片并上传到 xDiT 服务，返回服务端可访问的 image_url。

        xDiT /v1/upload 接收 multipart 字段 `file`，返回的 image_url 可直接
        传给 /v1/videos/generations（服务端从本地 upload 目录读取，避免回环下载）。
        """
        # 1. 下载图片
        img_resp = await self.http.get(image_url)
        img_resp.raise_for_status()
        img_bytes = img_resp.content

        # 2. 上传到 xDiT 服务
        upload_resp = await self.http.post(
            f"{self.endpoint}/v1/upload",
            files={"file": ("input.png", img_bytes, "image/png")},
        )
        upload_resp.raise_for_status()
        result = upload_resp.json()
        uploaded_url = result.get("image_url", "")
        if not uploaded_url:
            raise XDiTServiceError(f"xDiT 上传未返回 image_url: {result}")
        logger.debug("xDiT 上传图片: %s -> %s", image_url, uploaded_url)
        return uploaded_url

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def submit_task(
        self,
        image_url: str,
        prompt: str,
        negative_prompt: str = "",
        scene_id: int = 0,
        duration_seconds: int | None = None,
    ) -> str:
        """提交视频生成任务，返回 task_id。

        远程 GenerationRequest 不接收 negative_prompt / scene_id / 并行策略参数，
        这些仅用于后端内部记录；negative_prompt 保留在签名中以兼容调用方。
        """
        # 根据目标时长计算帧数：HunyuanVideo 帧数需对齐 4k+1
        if duration_seconds is None:
            num_frames = settings.xdit_num_frames
        else:
            # 钳制到服务端契约上限 257（超出会触发 422 Validation Error）
            raw = min(max(21, duration_seconds * _VIDEO_FPS + 1), _MAX_NUM_FRAMES)
            num_frames = ((raw - 1) // 4) * 4 + 1

        payload = {
            "model": settings.xdit_model,
            "image_url": image_url,
            "prompt": prompt,
            "num_frames": num_frames,
            "num_inference_steps": settings.xdit_steps,
            "cfg": settings.xdit_cfg,
            "seed": settings.xdit_seed,
            "size": settings.xdit_resolution,
        }
        resp = await self.http.post(
            f"{self.endpoint}/v1/videos/generations",
            params={"async_mode": "true"},
            json=payload,
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

        成功返回最终任务数据（含 result 字段）。远程状态机无百分比进度，
        running 期间按已用时长做线性估算（上限 90%），仅在估算值变化时回调。

        失败抛出 XDiTServiceError；超时抛出 TimeoutError。
        """
        timeout = timeout or settings.xdit_request_timeout
        deadline = time.time() + timeout
        last_progress = -1

        while time.time() < deadline:
            resp = await self.http.get(f"{self.endpoint}/v1/tasks/{task_id}")
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "unknown")

            if status == "succeeded":
                logger.info("xDiT 任务完成: task_id=%s", task_id)
                return data
            if status == "failed":
                err = data.get("error", "xDiT 任务执行失败")
                logger.error("xDiT 任务失败: task_id=%s err=%s", task_id, err)
                raise XDiTServiceError(f"xDiT 任务 {task_id} 失败: {err}")

            # pending/running：按已用时长估算进度（25 → 90）
            started_at = data.get("started_at") or data.get("created_at")
            if status == "running" and started_at:
                elapsed = max(0.0, time.time() - float(started_at))
                ratio = min(elapsed / _ESTIMATED_INFERENCE_SECONDS, 1.0)
                progress = 25 + int(ratio * 65)
                message = "xDiT 推理中"
            else:
                progress = 25
                message = "xDiT 任务排队中"

            # 仅在进度变化时回调，避免刷屏
            if progress_callback and progress != last_progress:
                progress_callback(progress, message)
                last_progress = progress

            await asyncio.sleep(settings.xdit_poll_interval)

        raise TimeoutError(f"xDiT 任务 {task_id} 超时 {timeout}s")

    @with_retry(max_attempts=2, base_delay=0.5, max_delay=2.0)
    async def get_result(self, task_id: str) -> dict[str, Any]:
        """获取已完成任务的结果。

        返回 {"video_url": str, "duration_seconds": int, "task_id": str}
        duration_seconds 由服务端返回的 num_frames / 24fps 推算。
        """
        resp = await self.http.get(f"{self.endpoint}/v1/tasks/{task_id}")
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result") or {}
        video_url = result.get("url", "")
        if not video_url:
            raise XDiTServiceError(f"xDiT 结果缺少 video_url: {data}")
        num_frames = int(result.get("num_frames", 0))
        duration = round(num_frames / _VIDEO_FPS) if num_frames else 0
        return {
            "video_url": video_url,
            "duration_seconds": duration,
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
        uploaded_url = await self.upload_image(image_url)

        # 2. 提交生成任务
        if progress_callback:
            progress_callback(15, "提交 HunyuanVideo-I2V 任务")
        task_id = await self.submit_task(
            image_url=uploaded_url,
            prompt=prompt,
            negative_prompt=negative_prompt,
            scene_id=scene_id,
            duration_seconds=duration_seconds,
        )

        # 3. 轮询状态（状态变化时回调，running 期间按已用时长估算进度）
        if progress_callback:
            progress_callback(25, "xDiT 推理中")
        await self.poll_status(task_id, progress_callback=progress_callback)

        # 4. 获取结果
        if progress_callback:
            progress_callback(95, "获取视频结果")
        result = await self.get_result(task_id)

        if progress_callback:
            progress_callback(100, "视频生成完成")
        return result
