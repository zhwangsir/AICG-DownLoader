"""M25.9 C2 失败模式注册表单元测试 + API 测试。

DramaClaw failure_registry 的本地化对等：四元组（detection/prevention/
correction/negative_clause）+ 分层 + 门禁开关 + 命中计数。种子来自我方
M15/M16/M18 实测失败史，非 DramaClaw 火柴人模式。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import failure_registry as fr_module
from app.services.failure_registry import (
    SEED_FAILURE_MODES,
    FailureModeRegistry,
)


@pytest.fixture
def registry(tmp_path):
    return FailureModeRegistry(store_path=tmp_path / "fm.json")


class TestSeeding:
    def test_seeds_idempotent(self, registry):
        codes_first = {m.code for m in registry.list_active()}
        registry.ensure_seeded()
        registry.ensure_seeded()
        assert {m.code for m in registry.list_active()} == codes_first
        assert len(codes_first) == len(SEED_FAILURE_MODES)

    def test_seed_codes_from_measured_history(self, registry):
        """种子来自实测失败史（M16.2 拼贴/文字泄漏/黑白漂移/M18 三视图），非 DramaClaw 火柴人。"""
        codes = {m.code for m in registry.list_active()}
        assert "collage_mismatch" in codes      # M16.2
        assert "legible_text_leak" in codes     # 全链路 negative
        assert "black_and_white_drift" in codes # 彩色硬性要求
        assert "three_view_fail" in codes       # M18.2
        assert "character_appearance_drift" in codes  # M18

    def test_gate_enabled_conservative(self, registry):
        """仅高置信模式进门禁（DramaClaw unsure=pass 哲学：宁放勿杀）。"""
        gate_codes = {m.code for m in registry.list_active(gate_only=True)}
        all_codes = {m.code for m in registry.list_active()}
        assert 0 < len(gate_codes) < len(all_codes)
        # 门禁模式必须有非空 detection 问句
        for m in registry.list_active(gate_only=True):
            assert m.detection.strip()

    def test_persistence_roundtrip(self, registry, tmp_path):
        registry.bump_hit("collage_mismatch")
        registry2 = FailureModeRegistry(store_path=tmp_path / "fm.json")
        assert registry2.hits()["collage_mismatch"] == 1


class TestQuery:
    def test_layer_filter(self, registry):
        gen = {m.code for m in registry.list_active(layer="generator")}
        cor = {m.code for m in registry.list_active(layer="correction")}
        director = {m.code for m in registry.list_active(layer="director")}
        assert gen and cor and director
        assert not (gen & cor)

    def test_get_by_code(self, registry):
        m = registry.get("collage_mismatch")
        assert m is not None and m.layer == "correction"
        assert registry.get("ghost") is None


class TestUpsert:
    def test_create_new_requires_layer_and_detection(self, registry):
        with pytest.raises(ValueError, match="layer 与 detection"):
            registry.upsert("new_mode", prevention_rule="x")

    def test_create_and_update(self, registry):
        m = registry.upsert("new_mode", layer="generator", detection="Is X visible?")
        assert m.code == "new_mode" and m.gate_enabled is False
        m2 = registry.upsert("new_mode", gate_enabled=True, negative_prompt_clause="no X")
        assert m2.gate_enabled is True and m2.negative_prompt_clause == "no X"
        assert m2.layer == "generator"  # 未触碰字段保留

    def test_upsert_ignores_unknown_fields(self, registry):
        m = registry.upsert("collage_mismatch", evil_field="hack")
        assert not hasattr(m, "evil_field")


class TestHits:
    def test_bump_hit_accumulates(self, registry):
        assert registry.bump_hit("collage_mismatch") == 1
        assert registry.bump_hit("collage_mismatch") == 2
        assert registry.hits()["collage_mismatch"] == 2
        # hit_count 同步到 mode
        assert registry.get("collage_mismatch").hit_count == 2

    def test_bump_unknown_code_records_hit(self, registry):
        # 未注册 code 也计数（防御：门禁可能报出种子外模式）
        assert registry.bump_hit("mystery") == 1


class TestNegativeClause:
    def test_generator_clause(self, registry):
        clause = registry.build_negative_prompt_clause("generator")
        assert "NEGATIVE CONSTRAINTS (generator layer" in clause
        # legible_text_leak 是 generator 层且有子句
        assert "legible text" in clause

    def test_correction_clause(self, registry):
        clause = registry.build_negative_prompt_clause("correction")
        assert "correction layer" in clause
        assert "black and white" in clause  # black_and_white_drift

    def test_empty_layer_returns_empty(self, registry):
        assert registry.build_negative_prompt_clause("director") == "" or \
            "director layer" in registry.build_negative_prompt_clause("director")


class TestAPI:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        # drama.py 直接 import 了 failure_registry 对象，必须 patch 路由模块的绑定
        # （patch services 模块属性对已建立的 from-import 绑定无效）
        monkeypatch.setattr("app.routers.drama.failure_registry", registry)
        return TestClient(app)

    def test_list_modes(self, client):
        resp = client.get("/api/drama/verification/failure-modes")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["modes"]) == len(SEED_FAILURE_MODES)
        assert "hits" in body

    def test_list_gate_only(self, client):
        resp = client.get("/api/drama/verification/failure-modes?gate_only=true")
        modes = resp.json()["modes"]
        assert all(m["gate_enabled"] for m in modes)

    def test_list_by_layer(self, client):
        resp = client.get("/api/drama/verification/failure-modes?layer=generator")
        modes = resp.json()["modes"]
        assert all(m["layer"] == "generator" for m in modes)

    def test_bump_hit_api(self, client):
        resp = client.post("/api/drama/verification/failure-modes/collage_mismatch/hit")
        assert resp.status_code == 200
        assert resp.json()["hit_count"] == 1

    def test_bump_hit_404(self, client):
        resp = client.post("/api/drama/verification/failure-modes/ghost/hit")
        assert resp.status_code == 404

    def test_upsert_api(self, client):
        resp = client.put(
            "/api/drama/verification/failure-modes/custom_mode",
            json={"layer": "generator", "detection": "Is Y visible?", "gate_enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["gate_enabled"] is True

    def test_upsert_422(self, client):
        resp = client.put(
            "/api/drama/verification/failure-modes/bad_mode",
            json={"prevention_rule": "x"},
        )
        assert resp.status_code == 422
