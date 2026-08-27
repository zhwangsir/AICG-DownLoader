"""model_registry_service 单元测试（任务3：下载器↔工作台打通）。"""

from __future__ import annotations

import json

import pytest

from app.services.model_registry_service import ModelRegistryService


@pytest.fixture()
def service() -> ModelRegistryService:
    return ModelRegistryService()


@pytest.fixture()
def models_json(tmp_path, monkeypatch):
    """写入一个临时下载器 models.json，并把 env 指向它。"""
    records = [
        {
            "filename": "Cinematic_Photography_style_v1.safetensors",
            "subdir": "loras",
            "source": "civitai",
            "download_url": "https://civitai.com/api/download/models/1407985",
            "sha256": "29A06A259B35D645ECC2518C47E8228771EF74EBC850D9CA4950C9DB1B6199BD",
            "size_kb": 299515.66,
            "downloaded_at": "1786666554",
        },
        {
            "filename": "some_other_checkpoint.safetensors",
            "subdir": "checkpoints",
            "source": "huggingface",
            "download_url": "https://example.com/x",
            "size_kb": 4000000.0,
            "downloaded_at": "1786666555",
        },
    ]
    p = tmp_path / "models.json"
    p.write_text(json.dumps(records), encoding="utf-8")
    monkeypatch.setenv("DOWNLOADER_MODELS_JSON", str(p))
    return p


def test_registry_merges_manifest_and_downloader(service, models_json):
    """manifest 中的 LoRA 与下载器 models.json 按 filename 融合，标注 downloaded。"""
    reg = service.get_registry()
    assert reg["stats"]["manifest_loras"] > 0
    assert reg["stats"]["downloader_total_models"] == 2
    # 第一条 LoRA 应标记已下载且带 subdir
    cinematic = next(
        l for l in reg["loras"] if l["filename"] == "Cinematic_Photography_style_v1.safetensors"
    )
    assert cinematic["downloaded"] is True
    assert cinematic["subdir"] == "loras"
    assert cinematic["downloaded_at"] == "1786666554"
    assert cinematic["trigger_words"]  # 来自 manifest
    # 未在 models.json 中的 LoRA 应标记未下载
    others = [l for l in reg["loras"] if l["filename"] != "Cinematic_Photography_style_v1.safetensors"]
    assert all(l["downloaded"] is False for l in others)


def test_registry_without_downloader_index(service, monkeypatch, tmp_path):
    """下载器 models.json 缺失时按全未下载处理，不报错。"""
    monkeypatch.setenv("DOWNLOADER_MODELS_JSON", str(tmp_path / "nonexistent.json"))
    reg = service.get_registry()
    assert reg["stats"]["manifest_loras"] > 0
    assert reg["stats"]["downloaded_loras"] == 0
    assert reg["stats"]["downloader_total_models"] == 0
    assert all(l["downloaded"] is False for l in reg["loras"])
    assert reg["sources"]["models_json"] is None


def test_registry_corrupt_downloader_index_fail_open(service, monkeypatch, tmp_path):
    """models.json 损坏时 fail-open（按空处理），不抛错。"""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json ", encoding="utf-8")
    monkeypatch.setenv("DOWNLOADER_MODELS_JSON", str(bad))
    reg = service.get_registry()
    assert reg["stats"]["downloader_total_models"] == 0
    assert all(l["downloaded"] is False for l in reg["loras"])


def test_registry_scans_disk_tree(service, models_json, tmp_path, monkeypatch):
    """磁盘上真实存在的 checkpoint/LoRA 出现在 registry，并标 downloaded。"""
    from app.config import settings
    from app.services.nas_library_service import NasLibraryService
    import app.services.nas_library_service as nas_mod

    root = tmp_path / "models"
    (root / "checkpoints").mkdir(parents=True)
    (root / "loras").mkdir(parents=True)
    (root / "checkpoints" / "majicMIX_v7.safetensors").write_bytes(b"ckpt")
    (root / "loras" / "Cinematic_Photography_style_v1.safetensors").write_bytes(b"lora")
    monkeypatch.setattr(settings, "nas_model_roots", str(root))
    monkeypatch.setattr(nas_mod, "_manifest_models_root", lambda: None)
    nas_mod.nas_library_service = NasLibraryService()

    reg = service.get_registry()
    assert reg["stats"]["disk_checkpoints"] == 1
    assert any(c["filename"] == "majicMIX_v7.safetensors" for c in reg["checkpoints"])
    cinematic = next(
        l for l in reg["loras"] if l["filename"] == "Cinematic_Photography_style_v1.safetensors"
    )
    assert cinematic["downloaded"] is True
    assert reg["sources"]["error"] is None


def test_registry_unreadable_roots_sets_error(service, monkeypatch, tmp_path):
    """Mac 看不见 NAS 时 registry 仍 200，但 sources.error 明确说明路径。"""
    from app.config import settings
    from app.services.nas_library_service import NasLibraryService
    import app.services.nas_library_service as nas_mod

    monkeypatch.setenv("DOWNLOADER_MODELS_JSON", str(tmp_path / "nonexistent.json"))
    monkeypatch.setattr(settings, "nas_model_roots", str(tmp_path / "no-such-models"))
    monkeypatch.setattr(nas_mod, "_manifest_models_root", lambda: None)
    nas_mod.nas_library_service = NasLibraryService()

    reg = service.get_registry()
    assert reg["stats"]["disk_checkpoints"] == 0
    assert reg["sources"]["error"]
    assert "不可读" in reg["sources"]["error"]
