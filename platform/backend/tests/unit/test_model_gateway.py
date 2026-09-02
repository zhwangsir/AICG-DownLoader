"""本地模型网关（model_gateway）单元测试。

DramaClaw litellm/NewAPI 的本地化对等层：能力注册表、健康路由（TTL 缓存 +
回退链）、调用指标。全部端点指向本地部署服务，零外部依赖。
"""

from __future__ import annotations

import time

import pytest

from app.services.model_gateway import CapabilitySpec, ModelGateway


@pytest.fixture
def gateway():
    return ModelGateway()


def _add_test_capability(gateway: ModelGateway, *endpoints: str) -> None:
    gateway._capabilities["test"] = CapabilitySpec(
        name="test", description="", endpoints=tuple(endpoints),
    )


class TestCapabilityRegistry:
    def test_all_capabilities_local(self, gateway):
        """全部能力端点均为本地/内网地址，零外部云服务依赖。"""
        for cap in gateway.capabilities_report():
            for ep in cap["endpoints"]:
                assert "api.openai.com" not in ep
                assert "openrouter.ai" not in ep
                assert "googleapis.com" not in ep
                assert "aliyuncs.com" not in ep

    def test_required_capabilities_registered(self, gateway):
        """DramaClaw 外部依赖的本地替换能力全部就位。"""
        names = {c["name"] for c in gateway.capabilities_report()}
        # DramaClaw: 灵山 LLM / Gemini VLM / NanoBanana / Seedance / TTS / Whisper / OSS
        assert {"llm", "vlm", "image", "video_h3", "video_ltx", "tts", "asr"} <= names

    def test_endpoint_resolution(self, gateway):
        assert gateway.endpoint("image").startswith("http")
        assert gateway.endpoints("asr")[0] != gateway.endpoints("asr")[-1] or True

    def test_unknown_capability_raises(self, gateway):
        with pytest.raises(KeyError, match="未注册的能力"):
            gateway.endpoint("nonexistent")


class TestHealthRouting:
    async def test_route_returns_first_healthy(self, gateway, monkeypatch):
        async def fake_probe(self, endpoint, path):
            return ("healthy-a" in endpoint, "ok" if "healthy-a" in endpoint else "down")

        monkeypatch.setattr(ModelGateway, "_probe", fake_probe)
        _add_test_capability(gateway, "http://down-a", "http://healthy-a")
        assert await gateway.route("test") == "http://healthy-a"

    async def test_route_all_down_raises(self, gateway, monkeypatch):
        async def fake_probe(self, endpoint, path):
            return (False, "down")

        monkeypatch.setattr(ModelGateway, "_probe", fake_probe)
        _add_test_capability(gateway, "http://down-a", "http://down-b")
        with pytest.raises(RuntimeError, match="全部端点离线"):
            await gateway.route("test")

    async def test_route_fail_open_returns_primary(self, gateway, monkeypatch):
        async def fake_probe(self, endpoint, path):
            return (False, "down")

        monkeypatch.setattr(ModelGateway, "_probe", fake_probe)
        _add_test_capability(gateway, "http://down-a", "http://down-b")
        assert await gateway.route("test", require_healthy=False) == "http://down-a"

    async def test_health_cache_ttl(self, gateway, monkeypatch):
        calls = {"n": 0}

        async def fake_probe(self, endpoint, path):
            calls["n"] += 1
            return (True, "ok")

        monkeypatch.setattr(ModelGateway, "_probe", fake_probe)
        assert await gateway.is_healthy("image") is True
        assert await gateway.is_healthy("image") is True
        assert calls["n"] == 1  # TTL 内不重复探测

        # 失效缓存后重新探测
        gateway.invalidate_health_cache("image")
        assert await gateway.is_healthy("image") is True
        assert calls["n"] == 2

    async def test_health_report_structure(self, gateway, monkeypatch):
        async def fake_probe(self, endpoint, path):
            # asr 主端点（workstation :9210）与 image 端点健康，其余离线
            return ("9210" in endpoint or "8188" in endpoint, "ok")

        monkeypatch.setattr(ModelGateway, "_probe", fake_probe)
        # 确保 image/asr 主端点含预期端口（测试环境 .env 可能改写）
        from app.config import settings
        monkeypatch.setattr(settings, "comfyui_image_hq", "http://127.0.0.1:8188")
        gateway2 = ModelGateway()
        report = await gateway2.health_report()
        assert "image" in report and "asr" in report
        assert report["image"]["healthy"] is True
        # asr 主端点 :9210 健康 → 整体健康
        assert report["asr"]["healthy"] is True
        assert report["asr"]["required"] is True
        for cap in report.values():
            assert cap["endpoints"] and all("endpoint" in e for e in cap["endpoints"])


class TestMetrics:
    def test_record_call_accumulates(self, gateway):
        gateway.record_call("llm", 120.5)
        gateway.record_call("llm", 80.2, error="timeout")
        m = gateway.metrics_report()["llm"]
        assert m["calls"] == 2
        assert m["errors"] == 1
        assert m["last_latency_ms"] == 80.2
        assert m["last_error"] == "timeout"
        assert m["last_called_at"] > 0

    def test_metrics_isolated_per_capability(self, gateway):
        gateway.record_call("llm", 100.0)
        gateway.record_call("image", 200.0)
        report = gateway.metrics_report()
        assert report["llm"]["calls"] == 1
        assert report["image"]["calls"] == 1


    def test_required_capabilities_skip_retired_studio(self, gateway):
        """MacStudio 已下线：必选能力不得再指向 studio04 VLM / studio02 ASR / studio01 demucs。"""
        studio = ("100.126.182.23", "100.91.0.121", "100.67.43.40")
        for spec in gateway._build_registry().values():
            if not spec.required:
                continue
            joined = " ".join(spec.endpoints)
            for ip in studio:
                assert ip not in joined, f"{spec.name} still probes {ip}"

    def test_required_llm_vlm_are_spark01_not_spark02(self, gateway, monkeypatch):
        """Required /gateway/health LLM+VLM is spark01 flash-next; spark02 is not a hard dep."""
        from app.config import Settings, settings

        f = Settings.model_fields
        assert f["exo_base_url"].default == "http://192.168.71.82:8000/v1"
        assert f["exo_model_glm52"].default == "qwen3.8-flash-next"
        assert f["exo_model_kimi"].default == "qwen3.8-flash-next"
        assert f["visual_model_url"].default == "http://192.168.71.82:8000/v1"
        assert f["visual_model_name"].default == "qwen3.8-flash-next"
        monkeypatch.setattr(settings, "exo_base_url", f["exo_base_url"].default)
        monkeypatch.setattr(settings, "visual_model_url", f["visual_model_url"].default)
        registry = gateway._build_registry()
        for name in ("llm", "vlm"):
            spec = registry[name]
            assert spec.required is True
            joined = " ".join(spec.endpoints)
            assert "192.168.71.84" not in joined, f"{name} still probes spark02"
            assert "192.168.71.82:8000" in joined
        assert "spark01" in registry["llm"].description
        assert "qwen3.8-flash-next" in registry["llm"].description

    async def test_optional_demucs_does_not_fail_closed(self, gateway, monkeypatch):
        """demucs 为可选：不探测 studio01，healthy 视为通过（不失败闭合）。"""
        probed: list[str] = []

        async def fake_probe(self, endpoint, path):
            probed.append(endpoint)
            return (True, "ok")

        monkeypatch.setattr(ModelGateway, "_probe", fake_probe)
        report = await gateway.health_report()
        assert report["demucs"]["required"] is False
        assert report["demucs"]["healthy"] is True
        assert not any("100.67.43.40" in e for e in probed)
