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
