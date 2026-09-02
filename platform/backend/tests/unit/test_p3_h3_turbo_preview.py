"""P3: two-speed H3 — Turbo preview vs 20-step final.

覆盖：
- preview=true / quality=preview 插入 MiniMaxH3TurboLoRA + MiniMaxH3TurboSampler
- 成片/默认/quality=final 不插 turbo 节点，steps 保持 20
- FL2VA 预览 ~8 步；Ref2VA 预览 ~4 步
- SFW turbo LoRA 不含 10Eros；NSFW 可用 10Eros_Max_h3_TURBO_ref2va
- 禁止 turbo + 内容 LoRA 叠加载
- h3_turbo_enabled 默认 False；预览路径独立打开
"""

from __future__ import annotations

import json

import pytest

from app.agents.video_agent import (
    WORKFLOW_TEMPLATE_H3,
    WORKFLOW_TEMPLATE_H3_R2V,
    _apply_h3_turbo_to_workflow,
    apply_h3_turbo_for_request,
    is_h3_preview_request,
    resolve_h3_turbo_lora_name,
)
from app.config import settings
from app.models.schemas import VideoRequest


def _req(**kw) -> VideoRequest:
    defaults = dict(
        scene_id=1,
        image_url="http://x/i.png",
        prompt="cinematic",
        duration_seconds=3,
        episode=1,
    )
    defaults.update(kw)
    return VideoRequest(**defaults)


def _fl2va() -> dict:
    return json.loads(json.dumps(WORKFLOW_TEMPLATE_H3))


def _r2v() -> dict:
    return json.loads(json.dumps(WORKFLOW_TEMPLATE_H3_R2V))


@pytest.fixture(autouse=True)
def _sfw_pin_off(monkeypatch):
    monkeypatch.setattr(
        "app.services.settings_service.settings_service.nsfw_status",
        lambda: {"nsfw_enabled": False, "has_pin": False},
    )
    monkeypatch.setattr(settings, "h3_turbo_enabled", False)


class TestPreviewFlag:
    def test_preview_true(self):
        assert is_h3_preview_request(_req(preview=True)) is True

    def test_quality_preview(self):
        assert is_h3_preview_request(_req(quality="preview")) is True
        assert is_h3_preview_request(_req(quality="PREVIEW")) is True

    def test_quality_final_overrides_preview_true(self):
        assert is_h3_preview_request(_req(preview=True, quality="final")) is False

    def test_default_and_empty_are_final(self):
        assert is_h3_preview_request(_req()) is False
        assert is_h3_preview_request(_req(preview=False, quality="")) is False
        assert is_h3_preview_request(None) is False


class TestPreviewInsertsTurboNodes:
    def test_fl2va_preview_inserts_turbo_and_8_steps(self):
        wf = _fl2va()
        apply_h3_turbo_for_request(wf, _req(preview=True), "fl2va")
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
        assert wf["34"]["inputs"]["sampler"] == ["101", 0]
        assert wf["32"]["inputs"]["steps"] == 8
        assert wf["32"]["inputs"]["model"] == ["100", 0]
        assert "10Eros" not in wf["100"]["inputs"]["lora_name"]
        assert "10eros" not in wf["100"]["inputs"]["lora_name"].lower()

    def test_quality_preview_same_as_preview_true(self):
        wf = _fl2va()
        apply_h3_turbo_for_request(wf, _req(quality="preview"), "fl2va")
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["32"]["inputs"]["steps"] == 8

    def test_ref2va_preview_inserts_turbo_and_4_steps(self):
        wf = _r2v()
        apply_h3_turbo_for_request(wf, _req(preview=True), "ref2va")
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
        assert wf["32"]["inputs"]["steps"] == 4
        assert "10Eros" not in wf["100"]["inputs"]["lora_name"]


class TestFinalDoesNotInsertTurbo:
    def test_default_generate_keeps_native_20_steps(self):
        wf = _fl2va()
        apply_h3_turbo_for_request(wf, _req(), "fl2va")
        assert "100" not in wf
        assert "101" not in wf
        assert wf["32"]["inputs"]["steps"] == 20
        assert wf["31"]["class_type"] == "KSamplerSelect"
        assert wf["32"]["inputs"]["model"] == ["1", 0]

    def test_preview_false_is_final(self):
        wf = _fl2va()
        apply_h3_turbo_for_request(wf, _req(preview=False), "fl2va")
        assert "100" not in wf

    def test_quality_final_is_final(self):
        wf = _r2v()
        apply_h3_turbo_for_request(wf, _req(preview=True, quality="final"), "ref2va")
        assert "100" not in wf
        assert wf["32"]["inputs"]["steps"] == 20


class TestSfwTurboLoraNot10Eros:
    def test_resolve_sfw_is_product_default(self):
        name = resolve_h3_turbo_lora_name(nsfw=False)
        assert "10Eros" not in name
        assert "10eros" not in name.lower()
        assert name == settings.h3_turbo_lora_name

    def test_sfw_rejects_misconfigured_10eros(self, monkeypatch):
        monkeypatch.setattr(
            settings, "h3_turbo_lora_name", "10Eros_Max_h3_TURBO_ref2va.safetensors"
        )
        name = resolve_h3_turbo_lora_name(nsfw=False)
        assert "10eros" not in name.lower()

    def test_nsfw_preview_may_use_10eros_turbo(self):
        name = resolve_h3_turbo_lora_name(nsfw=True)
        assert name == settings.h3_nsfw_turbo_lora_name
        assert "10Eros_Max_h3_TURBO_ref2va" in name

    def test_preview_workflow_sfw_lora_not_10eros(self):
        wf = _fl2va()
        apply_h3_turbo_for_request(wf, _req(preview=True), "fl2va")
        assert "10Eros" not in wf["100"]["inputs"]["lora_name"]


class TestNoStackContentLora:
    def test_content_lora_skips_turbo(self):
        wf = _fl2va()
        wf["90"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "lora_name": "style_content.safetensors",
                "strength_model": 0.8,
            },
        }
        _apply_h3_turbo_to_workflow(wf, enabled=True, steps=8, mode="fl2va")
        assert "100" not in wf
        assert "101" not in wf
        assert wf["90"]["class_type"] == "LoraLoader"


class TestGlobalTurboDefault:
    def test_h3_turbo_enabled_default_false(self):
        # autouse fixture keeps False; product default is False
        assert settings.h3_turbo_enabled is False

    def test_global_flag_still_enables_without_preview(self, monkeypatch):
        monkeypatch.setattr(settings, "h3_turbo_enabled", True)
        monkeypatch.setattr(settings, "h3_turbo_steps", 6)
        wf = _fl2va()
        apply_h3_turbo_for_request(wf, _req(), "fl2va")
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["32"]["inputs"]["steps"] == 6
