from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_gateway.main import _select_video_backend  # noqa: E402


def test_minimax_h3_model_routes_to_h3():
    assert _select_video_backend({"model": "MiniMax-H3", "duration": 5}) == "h3"
    assert _select_video_backend({"model": "minimax-h3-local", "duration": 5}) == "h3"


def test_happyhorse_model_routes_to_h3():
    assert _select_video_backend({"model": "happyhorse-1.0", "duration": 5}) == "h3"


def test_seedance_models_route_to_ltx_by_default():
    assert _select_video_backend({"model": "seedance-2.0", "duration": 5}) == "ltx"
    assert _select_video_backend({"model": "seedance-1.0-pro-fast", "duration": 5}) == "ltx"


def test_long_duration_and_audio_route_to_h3():
    assert _select_video_backend({"model": "seedance-2.0", "duration": 16}) == "h3"
    assert (
        _select_video_backend(
            {"model": "seedance-2.0", "duration": 5, "generate_audio": True}
        )
        == "h3"
    )


def test_force_backend_env_override(monkeypatch):
    import local_gateway.main as gw

    monkeypatch.setattr(gw, "VIDEO_BACKEND_FORCE", "ltx")
    assert _select_video_backend({"model": "MiniMax-H3", "duration": 20}) == "ltx"
    monkeypatch.setattr(gw, "VIDEO_BACKEND_FORCE", "h3")
    assert _select_video_backend({"model": "seedance-2.0", "duration": 5}) == "h3"


def test_minimax_h3_registered_in_logical_models():
    import local_gateway.main as gw

    assert "MiniMax-H3" in gw.LOGICAL_MODELS


def test_ltx25_model_routes_to_ltx():
    assert _select_video_backend({"model": "LTX-2.5", "duration": 5}) == "ltx"
    assert _select_video_backend({"model": "ltx-2.5", "duration": 20}) == "ltx"


def test_ltx25_registered_in_logical_models():
    import local_gateway.main as gw

    assert "LTX-2.5" in gw.LOGICAL_MODELS
    assert "local-sdxl" in gw.LOGICAL_MODELS
