"""P4: H3 AddGuide repair, 漫剧 does not apply here, DaSiWa A/B, no Wan."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from local_gateway import h3_context_ir as h3_ir
from local_gateway.h3_video import (
    H3_ADD_GUIDE_CLASS,
    H3_REF_UNET_NSFW,
    H3_REF_UNET_NSFW_DASIWA,
    H3_UNET_NSFW,
    H3_UNET_NSFW_DASIWA,
    H3RepairUnavailable,
    apply_h3_repair_guide,
    h3_unets,
    request_nsfw_variant,
    request_repair,
    require_h3_add_guide,
    workflow_has_wan,
)
from local_gateway.main import _build_h3_workflow


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
        unet_name=H3_UNET_NSFW,
        **kw,
    )


def test_nsfw_default_unets_are_10eros_not_dasiwa():
    fl2va, ref2va = h3_unets(True)
    assert fl2va == H3_UNET_NSFW
    assert ref2va == H3_REF_UNET_NSFW
    assert fl2va.startswith("10Eros")
    assert "DaSiWa" not in fl2va
    assert "Remix" not in fl2va
    assert "Remix" not in ref2va


def test_dasiwa_is_opt_in_ab_only():
    fl2va, ref2va = h3_unets(True, "dasiwa")
    assert fl2va == H3_UNET_NSFW_DASIWA
    assert ref2va == H3_REF_UNET_NSFW_DASIWA
    assert "10Eros" not in fl2va
    # PIN default still 10Eros when variant omitted
    assert h3_unets(True)[0].startswith("10Eros")


def test_remix_variant_does_not_swap_default():
    fl2va, ref2va = h3_unets(True, "remix")
    assert fl2va == H3_UNET_NSFW
    assert ref2va == H3_REF_UNET_NSFW


def test_request_repair_and_variant_flags():
    assert request_repair({"repair": True}) is True
    assert request_repair({"inpaint": True}) is True
    assert request_repair({"metadata": {"repair": True}}) is True
    assert request_repair({"prompt": "x"}) is False
    assert request_nsfw_variant({"nsfw_variant": "dasiwa"}) == "dasiwa"
    assert request_nsfw_variant({"nsfw": True}) == ""


def test_require_add_guide_fail_closed_without_node():
    with pytest.raises(H3RepairUnavailable, match="MiniMaxH3AddGuide"):
        require_h3_add_guide({})
    with pytest.raises(H3RepairUnavailable, match="Wan"):
        require_h3_add_guide(None)
    require_h3_add_guide({H3_ADD_GUIDE_CLASS: {}})


def test_repair_path_uses_add_guide_and_denoise_mask_never_wan():
    wf = _fl2va()
    apply_h3_repair_guide(wf, mask_name="mask.png", denoise=0.45)
    assert wf["112"]["class_type"] == "MiniMaxH3AddGuide"
    assert wf["111"]["class_type"] == "LoadImageMask"
    assert wf["113"]["class_type"] == "SetLatentNoiseMask"
    assert wf["113"]["inputs"]["mask"] == ["111", 0]
    assert wf["33"]["inputs"]["conditioning"] == ["112", 0]
    assert wf["34"]["inputs"]["latent_image"] == ["113", 0]
    assert wf["32"]["inputs"]["denoise"] == 0.45
    assert wf["20"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert workflow_has_wan(wf) is False
    dumped = json.dumps(wf)
    assert "Wan" not in dumped
    assert "WanImageToVideo" not in dumped


def test_repair_without_mask_still_add_guide_never_wan():
    wf = _fl2va()
    apply_h3_repair_guide(wf)
    assert wf["112"]["class_type"] == "MiniMaxH3AddGuide"
    assert "111" not in wf
    assert workflow_has_wan(wf) is False


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


def test_http_repair_fail_closed_when_add_guide_missing(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    main._object_info_cache.clear()
    monkeypatch.setattr(
        main, "_http", lambda timeout=None: _FakeClient(captured, object_info={})
    )
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "fix the hand",
            "duration": 5,
            "repair": True,
            "first_frame_image": "http://ref.example/f.png",
        },
    )
    assert resp.status_code == 502
    msg = resp.json()["error"]["message"]
    assert "MiniMaxH3AddGuide" in msg
    assert "Wan" in msg
    assert "workflow" not in captured


def test_http_repair_submits_add_guide_when_node_present(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    main._object_info_cache.clear()
    object_info = {
        H3_ADD_GUIDE_CLASS: {},
        "UNETLoader": {},
        "MiniMaxH3ImageToVideo": {},
    }
    monkeypatch.setattr(
        main, "_http", lambda timeout=None: _FakeClient(captured, object_info=object_info)
    )
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "fix the hand",
            "duration": 5,
            "repair": True,
            "inpaint_mask_url": "http://ref.example/m.png",
            "first_frame_image": "http://ref.example/f.png",
        },
    )
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert wf["112"]["class_type"] == "MiniMaxH3AddGuide"
    assert wf["113"]["class_type"] == "SetLatentNoiseMask"
    assert workflow_has_wan(wf) is False
    assert "Wan" not in json.dumps(wf)
