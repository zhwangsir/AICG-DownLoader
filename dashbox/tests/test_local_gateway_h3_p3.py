"""P3: gateway preview uses H3 Turbo sampler, not 20-step BasicScheduler."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from local_gateway import h3_context_ir as h3_ir
from local_gateway.h3_video import (
    H3_ADD_GUIDE_CLASS,
    H3_TURBO_LORA_SFW,
    apply_h3_repair_guide,
    apply_h3_turbo_to_workflow,
    request_h3_preview,
    resolve_h3_turbo_lora_name,
    workflow_has_wan,
)
from local_gateway.main import _build_h3_r2v_workflow, _build_h3_workflow


@pytest.fixture(autouse=True)
def _disable_h3_context_ir_rewrite(monkeypatch):
    monkeypatch.setattr(h3_ir, "REWRITE_ENABLED", False)


def _fl2va(**kw):
    return _build_h3_workflow(
        prompt="p",
        width=768,
        height=1344,
        num_frames=124,
        seed=1,
        filename_prefix="x",
        first_image_name="f.png",
        last_image_name="l.png",
        **kw,
    )


def _r2v(**kw):
    return _build_h3_r2v_workflow(
        prompt="p",
        width=768,
        height=1344,
        num_frames=124,
        seed=1,
        filename_prefix="x",
        ref_image_names=["r.png"],
        **kw,
    )


class TestPreviewFlag:
    def test_preview_true(self):
        assert request_h3_preview({"preview": True}) is True

    def test_quality_preview(self):
        assert request_h3_preview({"quality": "preview"}) is True
        assert request_h3_preview({"quality": "PREVIEW"}) is True
        assert request_h3_preview({"metadata": {"quality": "preview"}}) is True

    def test_quality_final_overrides_preview_true(self):
        assert request_h3_preview({"preview": True, "quality": "final"}) is False

    def test_omit_and_false_are_final(self):
        assert request_h3_preview({"prompt": "x"}) is False
        assert request_h3_preview({"preview": False}) is False
        assert request_h3_preview({"preview": False, "quality": ""}) is False
        assert request_h3_preview(None) is False
        assert request_h3_preview({"quality": "final"}) is False


class TestPreviewInsertsTurbo:
    def test_fl2va_preview_has_turbo_lora_sampler_and_8_steps(self):
        wf = _fl2va()
        apply_h3_turbo_to_workflow(wf, mode="fl2va", nsfw=False)
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
        assert wf["34"]["inputs"]["sampler"] == ["101", 0]
        assert wf["32"]["inputs"]["steps"] == 8
        assert wf["32"]["inputs"]["model"] == ["100", 0]
        assert wf["33"]["inputs"]["model"] == ["100", 0]
        name = wf["100"]["inputs"]["lora_name"]
        assert name == H3_TURBO_LORA_SFW
        assert "10eros" not in name.lower()

    def test_ref2va_preview_has_turbo_and_4_steps(self):
        wf = _r2v()
        apply_h3_turbo_to_workflow(wf, mode="ref2va", nsfw=False)
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
        assert wf["32"]["inputs"]["steps"] == 4
        assert "10eros" not in wf["100"]["inputs"]["lora_name"].lower()


class TestFinalDoesNotInsertTurbo:
    def test_native_graph_is_20_step_no_turbo(self):
        wf = _fl2va()
        assert "100" not in wf
        assert "101" not in wf
        assert wf["32"]["class_type"] == "BasicScheduler"
        assert wf["32"]["inputs"]["steps"] == 20
        assert wf["31"]["class_type"] == "KSamplerSelect"
        assert wf["34"]["inputs"]["sampler"] == ["31", 0]


class TestSfwTurboLoraNot10Eros:
    def test_resolve_sfw_is_product_default(self):
        name = resolve_h3_turbo_lora_name(nsfw=False)
        assert name == H3_TURBO_LORA_SFW
        assert "10eros" not in name.lower()

    def test_sfw_rejects_misconfigured_10eros(self, monkeypatch):
        monkeypatch.setenv("LOCAL_H3_TURBO_LORA", "10Eros_Max_h3_TURBO_ref2va.safetensors")
        name = resolve_h3_turbo_lora_name(nsfw=False)
        assert "10eros" not in name.lower()
        assert name == H3_TURBO_LORA_SFW


class TestNoStackContentLora:
    def test_turbo_skips_content_lora(self):
        wf = _fl2va()
        wf["90"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "lora_name": "style_content.safetensors",
                "strength_model": 0.8,
            },
        }
        wf["32"]["inputs"]["model"] = ["90", 0]
        apply_h3_turbo_to_workflow(wf, mode="fl2va", nsfw=False)
        assert "90" not in wf
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
        dumped = json.dumps(wf)
        assert "style_content.safetensors" not in dumped
        assert "LoraLoader" not in dumped
        assert wf["32"]["inputs"]["model"] == ["100", 0]


class TestRepairPlusPreview:
    def test_repair_and_preview_still_add_guide_not_wan(self):
        wf = _fl2va()
        apply_h3_repair_guide(wf, mask_name="mask.png", denoise=0.45)
        apply_h3_turbo_to_workflow(wf, mode="fl2va", nsfw=False)
        assert wf["112"]["class_type"] == "MiniMaxH3AddGuide"
        assert wf["113"]["class_type"] == "SetLatentNoiseMask"
        assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
        assert wf["34"]["inputs"]["sampler"] == ["101", 0]
        assert wf["32"]["inputs"]["steps"] == 8
        assert wf["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert workflow_has_wan(wf) is False
        dumped = json.dumps(wf)
        assert "Wan" not in dumped
        assert "WanImageToVideo" not in dumped


class _FakeResp:
    def __init__(self, status=200, json_data=None, content=b""):
        self.status_code = status
        self._json = json_data or {}
        self.content = content
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, captured, object_info):
        self.captured = captured
        self.object_info = object_info

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        if "/system_stats" in url:
            return _FakeResp()
        if "/object_info" in url:
            return _FakeResp(json_data=self.object_info)
        if url.startswith("http://ref.example"):
            return _FakeResp(content=b"\x89PNG\r\nref")
        return _FakeResp()

    async def post(self, url, **kwargs):
        if "/upload/image" in url:
            return _FakeResp(json_data={"name": "up.png"})
        if "/prompt" in url:
            self.captured["workflow"] = kwargs["json"]["prompt"]
            return _FakeResp(json_data={"prompt_id": "pid-h3"})
        return _FakeResp()


def _object_info():
    return {
        H3_ADD_GUIDE_CLASS: {},
        "UNETLoader": {},
        "MiniMaxH3ImageToVideo": {},
        "MiniMaxH3TurboLoRA": {},
        "MiniMaxH3TurboSampler": {},
    }


def test_http_preview_true_submits_turbo_not_20_step(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    main._object_info_cache.clear()
    monkeypatch.setattr(main, "_http", lambda timeout=None: _FakeClient(captured, _object_info()))
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "preview shot",
            "duration": 5,
            "preview": True,
            "quality": "preview",
            "first_frame_image": "http://ref.example/f.png",
            "last_frame_image": "http://ref.example/l.png",
        },
    )
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
    assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
    assert wf["32"]["inputs"]["steps"] == 8
    assert wf["34"]["inputs"]["sampler"] == ["101", 0]
    assert "10eros" not in wf["100"]["inputs"]["lora_name"].lower()


def test_http_quality_final_stays_20_step_no_turbo(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    main._object_info_cache.clear()
    monkeypatch.setattr(main, "_http", lambda timeout=None: _FakeClient(captured, _object_info()))
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "final shot",
            "duration": 5,
            "preview": False,
            "quality": "final",
            "first_frame_image": "http://ref.example/f.png",
        },
    )
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert "100" not in wf
    assert "101" not in wf
    assert wf["32"]["class_type"] == "BasicScheduler"
    assert wf["32"]["inputs"]["steps"] == 20
    assert wf["34"]["inputs"]["sampler"] == ["31", 0]


def test_http_omit_preview_stays_20_step(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    main._object_info_cache.clear()
    monkeypatch.setattr(main, "_http", lambda timeout=None: _FakeClient(captured, _object_info()))
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "default shot",
            "duration": 5,
            "first_frame_image": "http://ref.example/f.png",
        },
    )
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert "100" not in wf
    assert wf["32"]["inputs"]["steps"] == 20


def test_http_repair_plus_preview_add_guide_not_wan(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    main._object_info_cache.clear()
    monkeypatch.setattr(main, "_http", lambda timeout=None: _FakeClient(captured, _object_info()))
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "fix the hand",
            "duration": 5,
            "preview": True,
            "quality": "preview",
            "repair": True,
            "inpaint_mask_url": "http://ref.example/m.png",
            "first_frame_image": "http://ref.example/f.png",
        },
    )
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert wf["112"]["class_type"] == "MiniMaxH3AddGuide"
    assert wf["100"]["class_type"] == "MiniMaxH3TurboLoRA"
    assert wf["101"]["class_type"] == "MiniMaxH3TurboSampler"
    assert workflow_has_wan(wf) is False
    assert "Wan" not in json.dumps(wf)
