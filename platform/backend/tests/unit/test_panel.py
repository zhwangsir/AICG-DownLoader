"""GET /api/panel/status — fused product status (no Rust spawn)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _stub_paths(tmp_path, monkeypatch, *, cfg_ok: bool = True, models_ok: bool = True):
    cfg = tmp_path / "config.json"
    models = tmp_path / "models.json"
    if cfg_ok:
        cfg.write_text('{"comfy_root": "/tmp"}', encoding="utf-8")
        monkeypatch.setattr("app.routers.panel._resolved_config_path", lambda: cfg)
    else:
        monkeypatch.setattr(
            "app.routers.panel._resolved_config_path", lambda: tmp_path / "no-config.json"
        )
    if models_ok:
        models.write_text("[]", encoding="utf-8")
        monkeypatch.setattr("app.routers.panel._resolved_models_json", lambda: models)
    else:
        monkeypatch.setattr(
            "app.routers.panel._resolved_models_json", lambda: tmp_path / "no-models.json"
        )


def test_panel_status_ok(client, tmp_path, monkeypatch):
    _stub_paths(tmp_path, monkeypatch)

    async def fake_probe(url: str) -> bool:
        return url.rstrip("/").endswith(":8080")

    monkeypatch.setattr("app.routers.panel._probe_listening", fake_probe)

    resp = client.get("/api/panel/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "ok"
    assert body["product"] == "DashBox"
    assert body["downloader_config_readable"] is True
    assert body["models_json_readable"] is True
    assert body["dashbox"]["web"] == "http://127.0.0.1:8080"
    assert body["drama_backend"]["api"].startswith("http://127.0.0.1:8100")
    assert body["dashbox"]["api"] == "http://127.0.0.1:8780"
    assert body["dashbox"]["web_listening"] is True
    assert body["dashbox"]["api_listening"] is False
    assert isinstance(body.get("nas_model_roots"), list)


def test_panel_status_missing_files(client, tmp_path, monkeypatch):
    _stub_paths(tmp_path, monkeypatch, cfg_ok=False, models_ok=False)

    async def fake_probe(_url: str) -> bool:
        return False

    monkeypatch.setattr("app.routers.panel._probe_listening", fake_probe)

    resp = client.get("/api/panel/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "ok"
    assert body["downloader_config_readable"] is False
    assert body["models_json_readable"] is False
    assert body["dashbox"]["web_listening"] is False
    assert body["dashbox"]["api_listening"] is False


def test_panel_status_probe_closed_ports_do_not_hang(client, tmp_path, monkeypatch):
    """Real TCP/HTTP probe against likely-closed ports; must return quickly."""
    _stub_paths(tmp_path, monkeypatch)
    resp = client.get("/api/panel/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["dashbox"]["web_listening"], bool)
    assert isinstance(body["dashbox"]["api_listening"], bool)
