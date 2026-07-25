"""图像生成服务客户端 — HunyuanImage 2.1 + FLUX+PuLID。

P4.3 设计目标：
- 替换 ComfyUI SDXL（majicMIX realistic）作为图像生成主后端
- HunyuanImage 2.1（17B FP8, 24GB 显存，原生 2K + 中文 prompt 最强）用于场景/分镜
- FLUX.1-dev + PuLID-FLUX v0.9.1（角色 ID 一致性专用）用于角色定妆照
- ComfyUI SDXL 降级为回退路径

两个服务均采用 OpenAI 兼容的 /images/generations 接口：
- POST /v1/images/generations  返回 {"data": [{"url": str, "b64_json": str}]}
- FLUX+PuLID 扩展 reference_image 字段（角色参考图 URL，用于 ID 一致性）
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from app.config import settings
from app.core.retry import with_retry

logger = logging.getLogger(__name__)


class ImageServiceError(RuntimeError):
    """图像生成服务调用异常。"""


def _parse_resolution(resolution: str) -> tuple[int, int]:
    """解析 '1024x1024' 格式为 (width, height)。"""
    try:
        w, h = resolution.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 1024, 1024


class HunyuanImageService:
    """HunyuanImage 2.1 客户端 — 原生 2K + 中文 prompt 最强。

    使用 BaseAgent 注入的 httpx.AsyncClient（trust_env=False 避免 macOS 代理拦截 IPv6）。
    所有调用经 with_retry 装饰，对瞬时网络错误自动重试。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.hunyuanimage_endpoint
        # trust_env=False 同 base.py：避免 macOS 系统 HTTP 代理拦截 IPv6 请求
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.hunyuanimage_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        resolution: str | None = None,
        num_images: int = 1,
        seed: int | None = None,
    ) -> list[bytes]:
        """生成图像，返回 PNG 字节列表。

        参数：
            prompt: 正面提示词（支持中文，HunyuanImage 2.1 原生中文 prompt 最强）
            negative_prompt: 负面提示词
            resolution: 'WxH' 格式，如 '1024x1024'，None 时用默认值
            num_images: 生成图片数量
            seed: 随机种子，None 表示随机
        """
        res = resolution or settings.hunyuanimage_default_resolution
        width, height = _parse_resolution(res)

        payload: dict[str, Any] = {
            "model": settings.hunyuanimage_model,
            "prompt": prompt,
            "n": max(1, num_images),
            "size": f"{width}x{height}",
            "response_format": "b64_json",  # 使用 base64 避免二次下载
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed

        resp = await self.http.post(
            f"{self.endpoint}/images/generations", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise ImageServiceError(f"HunyuanImage 返回空 data: {data}")

        images: list[bytes] = []
        for item in items:
            # 优先 b64_json，回退 url 下载
            b64 = item.get("b64_json")
            if b64:
                images.append(base64.b64decode(b64))
                continue
            url = item.get("url")
            if url:
                img_resp = await self.http.get(url)
                img_resp.raise_for_status()
                images.append(img_resp.content)
        if not images:
            raise ImageServiceError(f"HunyuanImage 返回无图像数据: {data}")
        logger.info("HunyuanImage 生成 %d 张图像: prompt=%s", len(images), prompt[:50])
        return images

    async def generate_one(
        self,
        prompt: str,
        negative_prompt: str = "",
        resolution: str | None = None,
        seed: int | None = None,
    ) -> bytes:
        """生成单张图像，返回 PNG 字节。"""
        images = await self.generate(
            prompt=prompt,
            negative_prompt=negative_prompt,
            resolution=resolution,
            num_images=1,
            seed=seed,
        )
        return images[0]


class FluxPuLIDService:
    """FLUX.1-dev + PuLID-FLUX v0.9.1 客户端 — 角色 ID 一致性专用。

    PuLID 通过 reference_image 提取角色 ID 特征，保证同一角色在不同视角/场景下的
    面部一致性（适合角色定妆照三视图生成）。

    使用 BaseAgent 注入的 httpx.AsyncClient。
    """

    def __init__(
        self,
        endpoint: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.endpoint = endpoint or settings.flux_pulid_endpoint
        self.http = http_client or httpx.AsyncClient(
            timeout=settings.flux_pulid_timeout, trust_env=False
        )

    @with_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        reference_image_url: str | None = None,
        reference_image_bytes: bytes | None = None,
        resolution: str | None = None,
        num_images: int = 1,
        seed: int | None = None,
        id_weight: float = 0.6,
    ) -> list[bytes]:
        """生成图像，返回 PNG 字节列表。

        参数：
            prompt: 正面提示词（英文，FLUX.1-dev 原生英文）
            negative_prompt: 负面提示词
            reference_image_url: 角色参考图 URL（用于 PuLID ID 提取）
            reference_image_bytes: 角色参考图字节（与 URL 二选一）
            resolution: 'WxH' 格式
            num_images: 生成图片数量
            seed: 随机种子
            id_weight: PuLID ID 权重 (0.0-1.0)，值越大 ID 一致性越强但多样性降低
        """
        res = resolution or settings.flux_pulid_default_resolution
        width, height = _parse_resolution(res)

        payload: dict[str, Any] = {
            "model": settings.flux_pulid_model,
            "prompt": prompt,
            "n": max(1, num_images),
            "size": f"{width}x{height}",
            "response_format": "b64_json",
            "id_weight": max(0.0, min(1.0, id_weight)),
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None:
            payload["seed"] = seed
        if reference_image_url:
            payload["reference_image"] = reference_image_url
        elif reference_image_bytes:
            # 字节上传：转 base64 内联
            payload["reference_image_b64"] = base64.b64encode(
                reference_image_bytes
            ).decode("ascii")

        resp = await self.http.post(
            f"{self.endpoint}/images/generations", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise ImageServiceError(f"FLUX+PuLID 返回空 data: {data}")

        images: list[bytes] = []
        for item in items:
            b64 = item.get("b64_json")
            if b64:
                images.append(base64.b64decode(b64))
                continue
            url = item.get("url")
            if url:
                img_resp = await self.http.get(url)
                img_resp.raise_for_status()
                images.append(img_resp.content)
        if not images:
            raise ImageServiceError(f"FLUX+PuLID 返回无图像数据: {data}")
        logger.info(
            "FLUX+PuLID 生成 %d 张图像: prompt=%s ref=%s",
            len(images), prompt[:50],
            "yes" if (reference_image_url or reference_image_bytes) else "no",
        )
        return images

    async def generate_one(
        self,
        prompt: str,
        negative_prompt: str = "",
        reference_image_url: str | None = None,
        reference_image_bytes: bytes | None = None,
        resolution: str | None = None,
        seed: int | None = None,
        id_weight: float = 0.6,
    ) -> bytes:
        """生成单张图像，返回 PNG 字节。"""
        images = await self.generate(
            prompt=prompt,
            negative_prompt=negative_prompt,
            reference_image_url=reference_image_url,
            reference_image_bytes=reference_image_bytes,
            resolution=resolution,
            num_images=1,
            seed=seed,
            id_weight=id_weight,
        )
        return images[0]
