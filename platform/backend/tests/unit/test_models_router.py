"""routers/models.py + settings 端点测试（M27）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.model_download_service import DownloadServiceError
from app.services.settings_service import SettingsServiceError


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_services(monkeypatch):
    """隔离三个服务的真实实现。"""
    from app.routers import models as models_router

    mocks = {
        "nas": MagicMock(),
        "dl": MagicMock(),
        "ss": MagicMock(),
    }
    mocks["ss"].nsfw_status.return_value = {"nsfw_enabled": False, "has_pin": True}
    monkeypatch.setattr(models_router, "nas_library_service", mocks["nas"])
    monkeypatch.setattr(models_router, "model_download_service", mocks["dl"])
    monkeypatch.setattr(models_router, "settings_service", mocks["ss"])
    return mocks


class TestLibrary:
    def test_list_library_defaults(self, client, mock_services):
        mock_services["nas"].list_models.return_value = {
            "items": [], "total": 0, "types": [], "scanned_at": 1.0, "cache_hit": False,
        }
        resp = client.get("/api/models/library")
        assert resp.status_code == 200
        kwargs = mock_services["nas"].list_models.call_args.kwargs
        assert kwargs["include_nsfw"] is False  # 设置未开启时强制隐藏

    def test_list_library_nsfw_requires_setting_on(self, client, mock_services):
        mock_services["nas"].list_models.return_value = {
            "items": [], "total": 0, "types": [], "scanned_at": 1.0, "cache_hit": False,
        }
        client.get("/api/models/library?include_nsfw=true")
        kwargs = mock_services["nas"].list_models.call_args.kwargs
        assert kwargs["include_nsfw"] is False  # 设置关闭 → 强制 False

        mock_services["ss"].nsfw_status.return_value = {"nsfw_enabled": True, "has_pin": True}
        client.get("/api/models/library?include_nsfw=true")
        kwargs = mock_services["nas"].list_models.call_args.kwargs
        assert kwargs["include_nsfw"] is True

    def test_list_library_filters(self, client, mock_services):
        mock_services["nas"].list_models.return_value = {
            "items": [], "total": 0, "types": [], "scanned_at": 1.0, "cache_hit": False,
        }
        client.get("/api/models/library?type=loras&q=style&refresh=true")
        kwargs = mock_services["nas"].list_models.call_args.kwargs
        assert kwargs["type_filter"] == "loras"
        assert kwargs["query"] == "style"
        assert kwargs["refresh"] is True

    def test_list_library_unreadable_roots_503(self, client, mock_services):
        mock_services["nas"].list_models.return_value = {
            "items": [],
            "total": 0,
            "types": [],
            "scanned_at": 1.0,
            "cache_hit": False,
            "error": "模型根目录不可读（本机未见 ToIV ComfyUI 模型树）。已配置: /tmp/x。",
        }
        resp = client.get("/api/models/library")
        assert resp.status_code == 503
        assert "不可读" in resp.json()["detail"]


class TestSearch:
    def test_search_ok(self, client, mock_services):
        mock_services["dl"].civitai_search.return_value = {"items": [], "total": 0}
        resp = client.get("/api/models/search?q=test&type=LORA&limit=5")
        assert resp.status_code == 200
        kwargs = mock_services["dl"].civitai_search.call_args.kwargs
        assert kwargs["query"] == "test"
        assert kwargs["model_type"] == "LORA"
        assert kwargs["include_nsfw"] is False

    def test_search_nsfw_gated(self, client, mock_services):
        mock_services["dl"].civitai_search.return_value = {"items": [], "total": 0}
        client.get("/api/models/search?include_nsfw=true")
        assert mock_services["dl"].civitai_search.call_args.kwargs["include_nsfw"] is False

        mock_services["ss"].nsfw_status.return_value = {"nsfw_enabled": True, "has_pin": True}
        client.get("/api/models/search?include_nsfw=true")
        assert mock_services["dl"].civitai_search.call_args.kwargs["include_nsfw"] is True

    def test_search_upstream_error_502(self, client, mock_services):
        mock_services["dl"].civitai_search.side_effect = RuntimeError("timeout")
        resp = client.get("/api/models/search?q=x")
        assert resp.status_code == 502
        assert "Civitai 搜索失败" in resp.json()["detail"]


class TestDownload:
    def _payload(self, **kw):
        base = {
            "download_url": "https://civitai.red/dl/1",
            "filename": "m.safetensors",
            "subdir": "checkpoints",
        }
        base.update(kw)
        return base

    def test_start_download_created(self, client, mock_services):
        mock_services["dl"].start_download.return_value = {"task_id": "t1", "status": "pending"}
        resp = client.post("/api/models/download", json=self._payload(sha256="a" * 64, nsfw=True))
        assert resp.status_code == 201
        kwargs = mock_services["dl"].start_download.call_args.kwargs
        assert kwargs["filename"] == "m.safetensors"
        assert kwargs["sha256"] == "a" * 64
        assert kwargs["nsfw"] is True

    def test_start_download_nsfw_gate_403(self, client, mock_services):
        mock_services["dl"].start_download.side_effect = DownloadServiceError(
            "NSFW 内容未开启：请先在模型库面板输入 PIN 解锁"
        )
        resp = client.post("/api/models/download", json=self._payload())
        assert resp.status_code == 403

    def test_start_download_bad_request_400(self, client, mock_services):
        mock_services["dl"].start_download.side_effect = DownloadServiceError("子目录不在白名单: bad")
        resp = client.post("/api/models/download", json=self._payload(subdir="bad"))
        assert resp.status_code == 400

    def test_list_downloads(self, client, mock_services):
        mock_services["dl"].list_tasks.return_value = [{"task_id": "t1"}]
        resp = client.get("/api/models/downloads")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["task_id"] == "t1"

    def test_get_download(self, client, mock_services):
        mock_services["dl"].get_task.return_value = {"task_id": "t1", "status": "done"}
        assert client.get("/api/models/downloads/t1").status_code == 200

    def test_get_download_404(self, client, mock_services):
        mock_services["dl"].get_task.return_value = None
        assert client.get("/api/models/downloads/nope").status_code == 404

    def test_cancel_download(self, client, mock_services):
        mock_services["dl"].cancel.return_value = True
        resp = client.delete("/api/models/downloads/t1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancel_requested"

    def test_cancel_download_409(self, client, mock_services):
        mock_services["dl"].cancel.return_value = False
        assert client.delete("/api/models/downloads/t1").status_code == 409


class TestNsfwSettings:
    def test_get_status(self, client, mock_services):
        resp = client.get("/api/settings/nsfw")
        assert resp.status_code == 200
        assert resp.json() == {"nsfw_enabled": False, "has_pin": True}

    def test_set_nsfw_ok(self, client, mock_services):
        mock_services["ss"].set_nsfw.return_value = {"nsfw_enabled": True, "has_pin": True}
        resp = client.post("/api/settings/nsfw", json={"enabled": True, "pin": "1234"})
        assert resp.status_code == 200
        assert resp.json()["nsfw_enabled"] is True
        args = mock_services["ss"].set_nsfw.call_args.args
        assert args == (True, "1234", None)

    def test_set_nsfw_first_time_new_pin(self, client, mock_services):
        mock_services["ss"].set_nsfw.return_value = {"nsfw_enabled": True, "has_pin": True}
        client.post("/api/settings/nsfw", json={"enabled": True, "pin": "", "new_pin": "1234"})
        assert mock_services["ss"].set_nsfw.call_args.args == (True, "", "1234")

    def test_set_nsfw_wrong_pin_403(self, client, mock_services):
        mock_services["ss"].set_nsfw.side_effect = SettingsServiceError("PIN 错误")
        resp = client.post("/api/settings/nsfw", json={"enabled": True, "pin": "0000"})
        assert resp.status_code == 403

    def test_change_pin_ok(self, client, mock_services):
        mock_services["ss"].change_pin.return_value = {"nsfw_enabled": True, "has_pin": True}
        resp = client.post("/api/settings/nsfw/pin", json={"pin": "1234", "new_pin": "5678"})
        assert resp.status_code == 200
        assert mock_services["ss"].change_pin.call_args.args == ("1234", "5678")

    def test_change_pin_wrong_old_403(self, client, mock_services):
        mock_services["ss"].change_pin.side_effect = SettingsServiceError("PIN 错误")
        resp = client.post("/api/settings/nsfw/pin", json={"pin": "0000", "new_pin": "5678"})
        assert resp.status_code == 403
