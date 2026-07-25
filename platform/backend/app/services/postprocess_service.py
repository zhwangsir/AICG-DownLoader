"""后处理服务客户端 — RealBasicVSR / RIFE / ProPainter / DeepFilterNet3 统一接口。

P4.4 设计目标：
- RealBasicVSR x4: 1080p → 4K 超分（workstation GPU, FP16 6GB）
- RIFE: 24fps → 60fps 插帧（workstation GPU, FP16 4GB）
- ProPainter: 去水印/去穿帮（workstation GPU, FP16 8-10GB, ICCV 2023 SOTA）
- DeepFilterNet3: 音频人声降噪（Mac 集群, Rust 原生 CPU 实时）

后处理服务 API 契约：
- workstation ComfyUI 包装（端口 8290）:
  - POST /v1/video/upload          上传视频/音频，返回 {"filename": str}
  - POST /v1/postprocess/super_res  RealBasicVSR 超分，返回 {"task_id": str}
  - POST /v1/postprocess/interp     RIFE 插帧，返回 {"task_id": str}
  - POST /v1/postprocess/inpaint    ProPainter 修复，返回 {"task_id": str}
  - GET  /v1/postprocess/status/{task_id}  统一轮询状态
  - GET  /v1/postprocess/result/{task_id}  统一获取结果
- Mac DeepFilterNet3 服务（端口 8301）:
  - POST /v1/audio/denoise  上传音频字节，返回降噪后的音频字节
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


class PostprocessServiceError(RuntimeError):
    """后处理服务调用异常。"""


class PostprocessService:
    """后处理统一客户端 — RealBasicVSR / RIFE / ProPainter。

    使用 BaseAgent 注入的 httpx.AsyncClient（trust_env=False 避免 macOS 代理拦截 IPv6）。
    所有调用经 with_retry 装饰，对瞬时网络错误自动重试。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.postprocess_endpoint
        self.http = http_client or httpx.AsyncClient(
            timeout=600.0, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def upload_video(self, video_url: str) -> str:
        """下载视频并上传到后处理服务，返回服务端文件名。"""
        # 1. 下载视频
        resp = await self.http.get(video_url)
        resp.raise_for_status()
        video_bytes = resp.content

        # 2. 上传到后处理服务
        upload_resp = await self.http.post(
            f"{self.endpoint}/v1/video/upload",
            files={"video": ("input.mp4", video_bytes, "video/mp4")},
        )
        upload_resp.raise_for_status()
        result = upload_resp.json()
        filename = result.get("filename", "input.mp4")
        logger.debug("后处理服务上传视频: %s -> %s", video_url, filename)
        return filename

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def submit_super_resolution(
        self,
        video_filename: str,
        scale: int | None = None,
        scene_id: int = 0,
    ) -> str:
        """提交 RealBasicVSR 超分任务，返回 task_id。"""
        payload = {
            "model": settings.realbasicvsr_model,
            "video": video_filename,
            "scale": scale or settings.realbasicvsr_scale,
            "scene_id": scene_id,
        }
        resp = await self.http.post(
            f"{self.endpoint}/v1/postprocess/super_res", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id", "")
        if not task_id:
            raise PostprocessServiceError(
                f"RealBasicVSR 未返回 task_id: {data}"
            )
        logger.info(
            "RealBasicVSR 提交超分: scene_id=%s task_id=%s scale=%s",
            scene_id, task_id, payload["scale"],
        )
        return task_id

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def submit_frame_interpolation(
        self,
        video_filename: str,
        target_fps: int | None = None,
        scene_id: int = 0,
    ) -> str:
        """提交 RIFE 插帧任务，返回 task_id。"""
        payload = {
            "model": settings.rife_model,
            "video": video_filename,
            "target_fps": target_fps or settings.rife_target_fps,
            "scene_id": scene_id,
        }
        resp = await self.http.post(
            f"{self.endpoint}/v1/postprocess/interp", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id", "")
        if not task_id:
            raise PostprocessServiceError(
                f"RIFE 未返回 task_id: {data}"
            )
        logger.info(
            "RIFE 提交插帧: scene_id=%s task_id=%s target_fps=%s",
            scene_id, task_id, payload["target_fps"],
        )
        return task_id

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def submit_inpainting(
        self,
        video_filename: str,
        mask_url: str | None = None,
        scene_id: int = 0,
    ) -> str:
        """提交 ProPainter 修复任务，返回 task_id。

        Args:
            mask_url: 可选修复掩码 URL（None 时服务端自动检测水印区域）
        """
        payload = {
            "model": settings.propainter_model,
            "video": video_filename,
            "scene_id": scene_id,
        }
        if mask_url:
            payload["mask"] = mask_url

        resp = await self.http.post(
            f"{self.endpoint}/v1/postprocess/inpaint", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id", "")
        if not task_id:
            raise PostprocessServiceError(
                f"ProPainter 未返回 task_id: {data}"
            )
        logger.info(
            "ProPainter 提交修复: scene_id=%s task_id=%s",
            scene_id, task_id,
        )
        return task_id

    async def poll_status(
        self,
        task_id: str,
        step_name: str = "postprocess",
        progress_callback: Callable[[int, str], None] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """轮询后处理任务状态直到完成或超时。

        成功返回最终 status 数据：
        {"status": "succeeded", "progress": 100, "message": str}
        """
        timeout = timeout or 600.0
        deadline = time.time() + timeout
        last_progress = -1

        while time.time() < deadline:
            resp = await self.http.get(
                f"{self.endpoint}/v1/postprocess/status/{task_id}"
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "unknown")
            progress = int(data.get("progress", 0))
            message = data.get("message", "")

            if progress_callback and progress != last_progress:
                progress_callback(progress, f"{step_name}: {message}")
                last_progress = progress

            if status == "succeeded":
                logger.info("%s 任务完成: task_id=%s", step_name, task_id)
                return data
            if status == "failed":
                err = data.get("error", f"{step_name} 任务执行失败")
                logger.error("%s 任务失败: task_id=%s err=%s", step_name, task_id, err)
                raise PostprocessServiceError(
                    f"{step_name} 任务 {task_id} 失败: {err}"
                )

            await asyncio.sleep(2.0)

        raise TimeoutError(f"{step_name} 任务 {task_id} 超时 {timeout}s")

    @with_retry(max_attempts=2, base_delay=0.5, max_delay=2.0)
    async def get_result(self, task_id: str) -> dict[str, Any]:
        """获取已完成任务的结果。

        返回 {"video_url": str, "duration_seconds": float, "task_id": str}
        """
        resp = await self.http.get(
            f"{self.endpoint}/v1/postprocess/result/{task_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        video_url = data.get("video_url", "")
        if not video_url:
            raise PostprocessServiceError(
                f"后处理结果缺少 video_url: {data}"
            )
        return {
            "video_url": video_url,
            "duration_seconds": float(data.get("duration_seconds", 0.0)),
            "task_id": task_id,
        }

    async def run_super_resolution(
        self,
        video_url: str,
        scene_id: int = 0,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> str:
        """端到端 RealBasicVSR 超分：上传 → 提交 → 轮询 → 获取结果，返回新视频 URL。"""
        if progress_callback:
            progress_callback(10, "上传视频到 RealBasicVSR")
        filename = await self.upload_video(video_url)

        if progress_callback:
            progress_callback(20, "提交超分任务")
        task_id = await self.submit_super_resolution(filename, scene_id=scene_id)

        if progress_callback:
            progress_callback(30, "RealBasicVSR 超分推理中")
        await self.poll_status(
            task_id, step_name="RealBasicVSR", progress_callback=progress_callback
        )

        if progress_callback:
            progress_callback(95, "获取超分结果")
        result = await self.get_result(task_id)

        if progress_callback:
            progress_callback(100, "超分完成")
        return result["video_url"]

    async def run_frame_interpolation(
        self,
        video_url: str,
        scene_id: int = 0,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> str:
        """端到端 RIFE 插帧：上传 → 提交 → 轮询 → 获取结果，返回新视频 URL。"""
        if progress_callback:
            progress_callback(10, "上传视频到 RIFE")
        filename = await self.upload_video(video_url)

        if progress_callback:
            progress_callback(20, "提交插帧任务")
        task_id = await self.submit_frame_interpolation(filename, scene_id=scene_id)

        if progress_callback:
            progress_callback(30, "RIFE 插帧推理中")
        await self.poll_status(
            task_id, step_name="RIFE", progress_callback=progress_callback
        )

        if progress_callback:
            progress_callback(95, "获取插帧结果")
        result = await self.get_result(task_id)

        if progress_callback:
            progress_callback(100, "插帧完成")
        return result["video_url"]

    async def run_inpainting(
        self,
        video_url: str,
        mask_url: str | None = None,
        scene_id: int = 0,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> str:
        """端到端 ProPainter 修复：上传 → 提交 → 轮询 → 获取结果，返回新视频 URL。"""
        if progress_callback:
            progress_callback(10, "上传视频到 ProPainter")
        filename = await self.upload_video(video_url)

        if progress_callback:
            progress_callback(20, "提交修复任务")
        task_id = await self.submit_inpainting(
            filename, mask_url=mask_url, scene_id=scene_id
        )

        if progress_callback:
            progress_callback(30, "ProPainter 修复推理中")
        await self.poll_status(
            task_id, step_name="ProPainter", progress_callback=progress_callback
        )

        if progress_callback:
            progress_callback(95, "获取修复结果")
        result = await self.get_result(task_id)

        if progress_callback:
            progress_callback(100, "修复完成")
        return result["video_url"]


class DeepFilterNetService:
    """DeepFilterNet3 音频降噪客户端 — Mac 集群 Rust 原生。

    DeepFilterNet3 服务 API 契约（由 Mac Studio studio01 部署的 FastAPI wrapper 提供）：
    - POST /v1/audio/denoise  上传音频字节，返回降噪后的音频字节
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.deepfilternet_endpoint
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.deepfilternet_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=0.5, max_delay=3.0)
    async def denoise(self, audio_url: str) -> bytes:
        """下载音频并调用 DeepFilterNet3 降噪，返回降噪后的音频字节。

        Args:
            audio_url: 待降噪的音频 URL

        Returns:
            降噪后的音频字节（MP3 格式）
        """
        # 1. 下载音频
        resp = await self.http.get(audio_url)
        resp.raise_for_status()
        audio_bytes = resp.content

        # 2. 推断扩展名
        url_lower = audio_url.lower()
        if url_lower.endswith(".wav"):
            ext, content_type = "wav", "audio/wav"
        elif url_lower.endswith(".m4a"):
            ext, content_type = "m4a", "audio/mp4"
        else:
            ext, content_type = "mp3", "audio/mpeg"

        # 3. 调用 DeepFilterNet3 降噪
        denoise_resp = await self.http.post(
            f"{self.endpoint}/v1/audio/denoise",
            files={"audio": (f"input.{ext}", audio_bytes, content_type)},
        )
        denoise_resp.raise_for_status()

        # 返回字节流：可能是音频字节或 JSON 包装
        content_type_resp = denoise_resp.headers.get("content-type", "")
        if "application/json" in content_type_resp:
            data = denoise_resp.json()
            audio_b64 = data.get("audio_b64", "")
            if not audio_b64:
                raise PostprocessServiceError(
                    f"DeepFilterNet3 返回缺少 audio_b64: {data}"
                )
            import base64
            return base64.b64decode(audio_b64)

        # 直接返回音频字节
        if not denoise_resp.content:
            raise PostprocessServiceError("DeepFilterNet3 返回空音频数据")

        logger.info("DeepFilterNet3 降噪完成: %s", audio_url)
        return denoise_resp.content
