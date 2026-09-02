"""P0: H3 Ref2VA gateway routing, PIN 10Eros UNets, hide fake 2K."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_gateway.h3_video import (
    H3_REF_UNET_NSFW,
    H3_REF_UNET_SFW,
    H3_UNET_NSFW,
    H3_UNET_SFW,
    collect_video_inputs,
    h3_resolution_scale,
    h3_unets,
    request_nsfw,
    select_h3_mode,
)
from local_gateway.main import _build_h3_r2v_workflow, _build_h3_workflow, _derive_video_size


def test_refs_route_to_r2v():
    body = {
        "prompt": "a shot",
        "model": "MiniMax-H3",
        "metadata": {"reference_images": ["http://x/a.png"]},
    }
    inputs = collect_video_inputs(body)
    assert select_h3_mode(inputs) == "r2v"


def test_ref_videos_and_audios_route_to_r2v():
    body = {"reference_videos": ["http://x/a.mp4"], "reference_audios": ["http://x/a.mp3"]}
    assert select_h3_mode(collect_video_inputs(body)) == "r2v"


def test_first_last_only_is_i2v():
    body = {"first_frame_image": "http://x/f.png", "last_frame_image": "http://x/l.png"}
    assert select_h3_mode(collect_video_inputs(body)) == "i2v"


def test_canonical_image_field_is_first_frame():
    body = {"image": "http://x/f.png"}
    inputs = collect_video_inputs(body)
    assert inputs["first"] == "http://x/f.png"
    assert select_h3_mode(inputs) == "i2v"


def test_no_frames_is_t2va():
    assert select_h3_mode(collect_video_inputs({"prompt": "empty"})) == "t2va"


def test_refs_win_over_first_last():
    body = {
        "first_frame_image": "http://x/f.png",
        "last_frame_image": "http://x/l.png",
        "reference_images": ["http://x/r.png"],
    }
    assert select_h3_mode(collect_video_inputs(body)) == "r2v"


def test_sfw_unets_are_minimax():
    fl2va, ref2va = h3_unets(False)
    assert fl2va == H3_UNET_SFW
    assert ref2va == H3_REF_UNET_SFW
    assert "10Eros" not in fl2va
    assert "10Eros" not in ref2va


def test_nsfw_unets_are_10eros():
    fl2va, ref2va = h3_unets(True)
    assert fl2va == H3_UNET_NSFW
    assert ref2va == H3_REF_UNET_NSFW
    assert fl2va.startswith("10Eros")
    assert ref2va.startswith("10Eros")


def test_request_nsfw_flag_and_metadata():
    assert request_nsfw({"nsfw": True}) is True
    assert request_nsfw({"metadata": {"nsfw": True}}) is True
    assert request_nsfw({"model": "MiniMax-H3"}) is False


def test_i2v_workflow_uses_image_to_video_and_sfw_unet():
    wf = _build_h3_workflow(
        prompt="p", width=768, height=1344, num_frames=124, seed=1,
        filename_prefix="x", first_image_name="f.png", last_image_name="l.png",
        unet_name=H3_UNET_SFW,
    )
    assert wf["20"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert wf["1"]["inputs"]["unet_name"] == H3_UNET_SFW
    assert wf["20"]["inputs"]["first_frame"] == ["10", 0]
    assert wf["20"]["inputs"]["last_frame"] == ["11", 0]


def test_t2va_omits_first_last_frames():
    wf = _build_h3_workflow(
        prompt="p", width=768, height=1344, num_frames=124, seed=1,
        filename_prefix="x",
        unet_name=H3_UNET_SFW,
    )
    assert wf["20"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert "first_frame" not in wf["20"]["inputs"]
    assert "last_frame" not in wf["20"]["inputs"]


def test_r2v_workflow_uses_reference_to_video_and_nsfw_unet():
    wf = _build_h3_r2v_workflow(
        prompt="p", width=768, height=1344, num_frames=124, seed=1,
        filename_prefix="x",
        ref_image_names=["a.png", "b.png"],
        ref_video_names=["v.mp4"],
        unet_name=H3_REF_UNET_NSFW,
    )
    assert wf["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert wf["1"]["inputs"]["unet_name"] == H3_REF_UNET_NSFW
    assert wf["20"]["inputs"]["ref_images"]["ref_image_0"] == ["10", 0]
    assert "ref_videos" in wf["20"]["inputs"]


def test_fake_2k_does_not_scale_h3_canvas():
    assert h3_resolution_scale("2k", 1.5) == 1.0
    assert h3_resolution_scale("4k", 2.0) == 1.0
    assert h3_resolution_scale("768p", 1.0) == 1.0
    w, h = _derive_video_size({"resolution": "2K", "ratio": "9:16"}, "h3")
    assert (w, h) == (768, 1344)


def test_select_backend_nsfw_forces_h3():
    from local_gateway.main import _select_video_backend
    assert _select_video_backend({"model": "seedance-2.0", "duration": 5, "nsfw": True}) == "h3"


class _FakeResp:
    def __init__(self, status=200, json_data=None, content=b""):
        self.status_code = status
        self._json = json_data or {}
        self.content = content
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, captured):
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        if "/system_stats" in url:
            return _FakeResp()
        if "/object_info" in url:
            return _FakeResp(json_data={})
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


def test_http_refs_submit_reference_to_video(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    monkeypatch.setattr(main, "_http", lambda timeout=None: _FakeClient(captured))
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "role lock",
            "duration": 5,
            "reference_images": ["http://ref.example/a.png"],
        },
    )
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert wf["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"
    assert wf["1"]["inputs"]["unet_name"] == H3_REF_UNET_SFW


def test_http_nsfw_uses_10eros_fl2va(monkeypatch):
    from fastapi.testclient import TestClient
    from local_gateway import main

    captured = {}
    monkeypatch.setattr(main, "_http", lambda timeout=None: _FakeClient(captured))
    client = TestClient(main.app)
    resp = client.post(
        "/v1/video/generations",
        json={
            "model": "MiniMax-H3",
            "prompt": "nsfw shot",
            "duration": 5,
            "nsfw": True,
            "first_frame_image": "http://ref.example/f.png",
        },
    )
    assert resp.status_code == 200
    wf = captured["workflow"]
    assert wf["20"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert wf["1"]["inputs"]["unet_name"] == H3_UNET_NSFW
