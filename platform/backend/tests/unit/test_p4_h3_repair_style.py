"""P4: H3 AddGuide repair, 漫剧 style pack, DaSiWa A/B, no P2/P3 regression."""

from __future__ import annotations

import json

import pytest

from app.agents.video_agent import (
    H3_ADD_GUIDE_CLASS,
    H3RepairUnavailable,
    WORKFLOW_TEMPLATE_H3,
    WORKFLOW_TEMPLATE_H3_R2V,
    _apply_h3_turbo_to_workflow,
    apply_h3_repair_for_request,
    apply_h3_repair_to_workflow,
    apply_h3_style_anchor,
    apply_h3_turbo_for_request,
    engine_fallback_chain,
    require_h3_add_guide,
    resolve_h3_unet_names,
    route_video_engine,
    workflow_has_wan,
)
from app.config import settings
from app.models.schemas import VideoRequest
from app.services.style_anchor import (
    MANJU_VIDEO_ENGINE,
    _load_entries,
    ipadapter_weight_for_anchor,
    is_manju_style_pack,
    manju_style_pack,
    resolve_style_anchor,
    sdxl_checkpoint_for_anchor,
    video_engine_for_style,
)


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
    _load_entries.cache_clear()


class TestRepairAddGuide:
    def test_require_add_guide_fail_closed(self):
        with pytest.raises(H3RepairUnavailable, match="MiniMaxH3AddGuide"):
            require_h3_add_guide({})
        with pytest.raises(H3RepairUnavailable, match="Wan"):
            require_h3_add_guide(None)
        require_h3_add_guide({H3_ADD_GUIDE_CLASS: {"input": {}}})

    def test_repair_inserts_add_guide_and_denoise_mask_never_wan(self):
        wf = _fl2va()
        apply_h3_repair_to_workflow(wf, mask_name="mask.png", denoise=0.4)
        assert wf["112"]["class_type"] == "MiniMaxH3AddGuide"
        assert wf["111"]["class_type"] == "LoadImageMask"
        assert wf["113"]["class_type"] == "SetLatentNoiseMask"
        assert wf["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert wf["1"]["class_type"] == "UNETLoader"
        assert workflow_has_wan(wf) is False
        assert "Wan" not in json.dumps(wf)
        assert wf["32"]["inputs"]["denoise"] == 0.4
        assert wf["33"]["inputs"]["conditioning"] == ["112", 0]
        assert wf["34"]["inputs"]["latent_image"] == ["113", 0]

    def test_repair_for_request_fail_closed_without_node(self):
        wf = _fl2va()
        with pytest.raises(H3RepairUnavailable, match="MiniMaxH3AddGuide"):
            apply_h3_repair_for_request(
                wf, _req(repair=True), mask_name="m.png", object_info={}
            )
        assert "112" not in wf
        assert workflow_has_wan(wf) is False

    def test_repair_for_request_wires_when_node_present(self):
        wf = _r2v()
        apply_h3_repair_for_request(
            wf,
            _req(repair=True, inpaint_mask_url="http://x/m.png"),
            mask_name="m.png",
            object_info={H3_ADD_GUIDE_CLASS: {}},
        )
        assert wf["112"]["class_type"] == "MiniMaxH3AddGuide"
        assert wf["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert workflow_has_wan(wf) is False

    def test_repair_fallback_chain_is_h3_only(self):
        req = _req(repair=True, reference_images=["http://x/r.png"])
        assert engine_fallback_chain("h3", req) == ("h3",)
        assert engine_fallback_chain("comfyui", req) == ("h3",)
        assert "comfyui" not in engine_fallback_chain("h3", req)


class TestManjuStylePack:
    def test_manju_pack_detected_and_stays_on_h3(self):
        assert is_manju_style_pack("漫剧") is True
        assert is_manju_style_pack("style_manju") is True
        assert is_manju_style_pack("comic-drama") is True
        assert is_manju_style_pack("写实电影感") is False
        pack = manju_style_pack("漫剧")
        assert pack["video_engine"] == "h3"
        assert pack["video_modes"] == ("fl2va", "ref2va")
        assert pack["same_lane_ref2va"] is True
        assert video_engine_for_style("漫剧") == MANJU_VIDEO_ENGINE == "h3"

    def test_manju_keyframes_use_anime_checkpoint_and_stronger_ipadapter(self):
        anchor = resolve_style_anchor("漫剧")
        assert anchor.key == "style_manju"
        assert anchor.is_realistic is False
        assert sdxl_checkpoint_for_anchor(anchor) == "animagineXL40.safetensors"
        assert ipadapter_weight_for_anchor(anchor, default=0.6) == 0.85

    def test_manju_does_not_change_h3_unet_or_engine(self):
        req = _req(style="漫剧", prompt="establishing shot aerial cityscape")
        assert route_video_engine(req, settings) == "h3"
        fl2va, ref2va = resolve_h3_unet_names(nsfw=False, request=req)
        assert fl2va == settings.h3_unet_name
        assert ref2va == settings.h3_ref_unet_name
        assert "10Eros" not in fl2va
        wf = _fl2va()
        wf["1"]["inputs"]["unet_name"] = fl2va
        apply_h3_style_anchor(req.prompt, req.style)
        assert wf["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert engine_fallback_chain("h3", req) == ("h3",)
        assert "comfyui" not in engine_fallback_chain("ltx", req)

    def test_manju_stays_h3_even_if_ltx_would_match_motion(self, monkeypatch):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        req = _req(style="漫剧", prompt="aerial drone cityscape establishing shot")
        assert route_video_engine(req, settings) == "h3"


class TestDaSiWaAB:
    def test_nsfw_default_is_10eros_not_dasiwa(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.settings_service.settings_service.nsfw_status",
            lambda: {"nsfw_enabled": True, "has_pin": True},
        )
        fl2va, ref2va = resolve_h3_unet_names(nsfw=True)
        assert fl2va == settings.h3_nsfw_unet_name
        assert ref2va == settings.h3_nsfw_ref_unet_name
        assert fl2va.startswith("10Eros")
        assert "DaSiWa" not in fl2va
        assert "Remix" not in fl2va

    def test_dasiwa_opt_in_ab_only(self):
        fl2va, ref2va = resolve_h3_unet_names(nsfw=True, variant="dasiwa")
        assert fl2va == settings.h3_dasiwa_unet_name
        assert "DaSiWa" in fl2va
        # default PIN path unchanged
        pin_fl, _ = resolve_h3_unet_names(nsfw=True)
        assert pin_fl.startswith("10Eros")

    def test_request_nsfw_variant_dasiwa(self):
        req = _req(nsfw_variant="dasiwa")
        fl2va, _ = resolve_h3_unet_names(nsfw=True, request=req)
        assert "DaSiWa" in fl2va

    def test_remix_not_selected(self):
        fl2va, _ = resolve_h3_unet_names(nsfw=True, variant="remix")
        assert fl2va.startswith("10Eros")
        assert "Remix" not in fl2va
        assert "FeiHou" not in fl2va


class TestNoP2P3Regression:
    def test_last_frame_chain_still_default(self):
        assert settings.h3_last_frame_chain_enabled is True

    def test_turbo_plus_content_lora_still_refused(self):
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
        apply_h3_turbo_for_request(wf, _req(preview=True), "fl2va")
        assert "100" not in wf
