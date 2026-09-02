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


def test_seedance_models_route_to_h3_not_auto_ltx():
    assert _select_video_backend({"model": "seedance-2.0", "duration": 5}) == "h3"
    assert _select_video_backend({"model": "seedance-1.0-pro-fast", "duration": 5}) == "h3"


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
    monkeypatch.setattr(gw, "LTX_ENABLED", True)
    assert _select_video_backend({"model": "MiniMax-H3", "duration": 20}) == "ltx"
    monkeypatch.setattr(gw, "VIDEO_BACKEND_FORCE", "h3")
    assert _select_video_backend({"model": "seedance-2.0", "duration": 5}) == "h3"


def test_force_ltx_ignored_when_disabled(monkeypatch):
    import local_gateway.main as gw

    monkeypatch.setattr(gw, "VIDEO_BACKEND_FORCE", "ltx")
    monkeypatch.setattr(gw, "LTX_ENABLED", False)
    assert _select_video_backend({"model": "MiniMax-H3", "duration": 5}) == "h3"


def test_minimax_h3_registered_in_logical_models():
    import local_gateway.main as gw

    assert "MiniMax-H3" in gw.LOGICAL_MODELS


def test_ltx25_model_routes_to_ltx_only_when_enabled(monkeypatch):
    import local_gateway.main as gw

    monkeypatch.setattr(gw, "LTX_ENABLED", True)
    assert _select_video_backend({"model": "LTX-2.5", "duration": 5}) == "ltx"
    assert _select_video_backend({"model": "ltx-2.5", "duration": 20}) == "ltx"
    monkeypatch.setattr(gw, "LTX_ENABLED", False)
    assert _select_video_backend({"model": "LTX-2.5", "duration": 5}) == "h3"


def test_ltx25_kept_in_code_but_hidden_from_models_when_disabled(monkeypatch):
    import local_gateway.main as gw

    assert "LTX-2.5" in gw.LOGICAL_MODELS
    assert "local-sdxl" in gw.LOGICAL_MODELS
    monkeypatch.setattr(gw, "LTX_ENABLED", False)
    listed = gw.listed_logical_models()
    assert "LTX-2.5" not in listed
    assert "MiniMax-H3" in listed
    assert "happyhorse-1.0" in listed
    monkeypatch.setattr(gw, "LTX_ENABLED", True)
    assert "LTX-2.5" in gw.listed_logical_models()


def test_auto_empty_model_never_picks_ltx_even_if_enabled(monkeypatch):
    import local_gateway.main as gw

    monkeypatch.setattr(gw, "LTX_ENABLED", True)
    monkeypatch.setattr(gw, "VIDEO_BACKEND_FORCE", "")
    assert _select_video_backend({"model": "", "duration": 5}) == "h3"
    assert _select_video_backend({"model": "seedance-2.0", "duration": 5}) == "h3"
