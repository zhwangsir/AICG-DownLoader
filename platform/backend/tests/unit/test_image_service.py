"""图像生成服务单元测试 — HunyuanImage 2.1 + FLUX+PuLID。

P4.3: 覆盖 OpenAI 兼容的 /images/generations 接口客户端：
- generate: 多图批量
- generate_one: 单图快捷方法
- b64_json / url 双返回格式解析
- 错误响应处理（空 data、HTTP 错误）
- FLUX+PuLID reference_image_url / reference_image_b64 注入
- id_weight 边界裁剪
"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.services.image_service import (
    FluxPuLIDService,
    HunyuanImageService,
    ImageServiceError,
)


# ============================================================================
# 工具函数
# ============================================================================


def _make_hunyuanimage(handler) -> HunyuanImageService:
    """构造使用 MockTransport 的 HunyuanImageService 实例。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return HunyuanImageService(http_client=client)


def _make_flux_pulid(handler) -> FluxPuLIDService:
    """构造使用 MockTransport 的 FluxPuLIDService 实例。"""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return FluxPuLIDService(http_client=client)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


# ============================================================================
# HunyuanImageService
# ============================================================================


class TestHunyuanImageGenerate:
    """HunyuanImageService.generate 端到端。"""

    async def test_b64_json_response(self):
        """b64_json 返回格式 → 解码为字节列表。"""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert str(request.url).endswith("/images/generations")
            payload = httpx.Response(200, json={
                "data": [
                    {"b64_json": _b64(b"img1")},
                    {"b64_json": _b64(b"img2")},
                ]
            })
            return payload

        svc = _make_hunyuanimage(handler)
        images = await svc.generate(prompt="test", num_images=2)

        assert len(images) == 2
        assert images[0] == b"img1"
        assert images[1] == b"img2"

    async def test_url_response_fallback(self):
        """无 b64_json 但有 url → 下载图片字节。"""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "POST" and url.endswith("/images/generations"):
                return httpx.Response(200, json={
                    "data": [{"url": "http://mock/img.png"}]
                })
            if request.method == "GET" and url == "http://mock/img.png":
                return httpx.Response(200, content=b"downloaded-img")
            return httpx.Response(404)

        svc = _make_hunyuanimage(handler)
        images = await svc.generate(prompt="test")

        assert images == [b"downloaded-img"]

    async def test_empty_data_raises(self):
        """data 为空 → ImageServiceError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        svc = _make_hunyuanimage(handler)
        with pytest.raises(ImageServiceError, match="返回空 data"):
            await svc.generate(prompt="test")

    async def test_no_image_data_raises(self):
        """data 中既无 b64_json 也无 url → ImageServiceError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{}]})

        svc = _make_hunyuanimage(handler)
        with pytest.raises(ImageServiceError, match="返回无图像数据"):
            await svc.generate(prompt="test")

    async def test_payload_includes_negative_prompt_and_seed(self):
        """negative_prompt 和 seed 正确注入 payload。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"x")}]})

        svc = _make_hunyuanimage(handler)
        await svc.generate(
            prompt="正向", negative_prompt="负向", seed=42, num_images=1
        )

        assert captured["payload"]["prompt"] == "正向"
        assert captured["payload"]["negative_prompt"] == "负向"
        assert captured["payload"]["seed"] == 42
        assert captured["payload"]["n"] == 1
        assert captured["payload"]["response_format"] == "b64_json"

    async def test_http_error_raises(self):
        """服务端返回 500 → HTTPStatusError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "internal error"})

        svc = _make_hunyuanimage(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.generate(prompt="test")


class TestHunyuanImageGenerateOne:
    """HunyuanImageService.generate_one 单图快捷方法。"""

    async def test_returns_single_bytes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"single")}]})

        svc = _make_hunyuanimage(handler)
        img = await svc.generate_one(prompt="test", negative_prompt="neg")
        assert img == b"single"


# ============================================================================
# FluxPuLIDService
# ============================================================================


class TestFluxPuLIDGenerate:
    """FluxPuLIDService.generate 端到端。"""

    async def test_basic_generate(self):
        """无参考图时正常生成。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"flux-img")}]})

        svc = _make_flux_pulid(handler)
        images = await svc.generate(prompt="portrait", negative_prompt="bad")

        assert images == [b"flux-img"]
        assert captured["payload"]["prompt"] == "portrait"
        assert captured["payload"]["negative_prompt"] == "bad"
        assert "reference_image" not in captured["payload"]
        assert "reference_image_b64" not in captured["payload"]

    async def test_reference_image_url_injection(self):
        """reference_image_url → 注入到 payload.reference_image。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"x")}]})

        svc = _make_flux_pulid(handler)
        await svc.generate(
            prompt="portrait",
            reference_image_url="http://mock/ref.png",
        )

        assert captured["payload"]["reference_image"] == "http://mock/ref.png"
        assert "reference_image_b64" not in captured["payload"]

    async def test_reference_image_bytes_injection(self):
        """reference_image_bytes → 转 base64 注入 payload.reference_image_b64。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"x")}]})

        svc = _make_flux_pulid(handler)
        await svc.generate(
            prompt="portrait",
            reference_image_bytes=b"raw-bytes",
        )

        assert captured["payload"]["reference_image_b64"] == _b64(b"raw-bytes")
        assert "reference_image" not in captured["payload"]

    async def test_reference_url_takes_priority_over_bytes(self):
        """同时传入 url 和 bytes → 优先使用 url。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"x")}]})

        svc = _make_flux_pulid(handler)
        await svc.generate(
            prompt="portrait",
            reference_image_url="http://mock/ref.png",
            reference_image_bytes=b"raw-bytes",
        )

        assert captured["payload"]["reference_image"] == "http://mock/ref.png"
        assert "reference_image_b64" not in captured["payload"]

    async def test_id_weight_clamped_to_range(self):
        """id_weight 越界（>1 或 <0）→ 裁剪到 [0, 1]。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = __import__("json").loads(request.content)
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"x")}]})

        svc = _make_flux_pulid(handler)
        await svc.generate(prompt="p", id_weight=1.5)
        assert captured["payload"]["id_weight"] == 1.0

        await svc.generate(prompt="p", id_weight=-0.3)
        assert captured["payload"]["id_weight"] == 0.0

    async def test_empty_data_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        svc = _make_flux_pulid(handler)
        with pytest.raises(ImageServiceError, match="返回空 data"):
            await svc.generate(prompt="p")

    async def test_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        svc = _make_flux_pulid(handler)
        with pytest.raises(httpx.HTTPStatusError):
            await svc.generate(prompt="p")


class TestFluxPuLIDGenerateOne:
    """FluxPuLIDService.generate_one 单图快捷方法。"""

    async def test_returns_single_bytes_with_reference(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"b64_json": _b64(b"pulid")}]})

        svc = _make_flux_pulid(handler)
        img = await svc.generate_one(
            prompt="portrait",
            reference_image_url="http://mock/ref.png",
            id_weight=0.8,
        )
        assert img == b"pulid"
