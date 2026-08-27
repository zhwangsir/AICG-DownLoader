"""nas_library_service 单元测试（M27：NAS 模型库浏览）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config import settings
from app.services.nas_library_service import (
    NasLibraryService,
    is_nsfw_name,
    model_extensions,
    nsfw_keywords,
)


@pytest.fixture()
def nas_tree(tmp_path, monkeypatch):
    """构造两级模型库目录树并指向配置。"""
    root1 = tmp_path / "models"
    root2 = tmp_path / "comfyui-models"
    (root1 / "checkpoints").mkdir(parents=True)
    (root1 / "loras").mkdir(parents=True)
    (root1 / ".hidden").mkdir(parents=True)
    (root2 / "checkpoints").mkdir(parents=True)

    f1 = root1 / "checkpoints" / "majicMIX_v7.safetensors"
    f1.write_bytes(b"a" * 100)
    f2 = root1 / "loras" / "style.safetensors"
    f2.write_bytes(b"b" * 50)
    f3 = root1 / "checkpoints" / "lustifySDXLNSFW_v8.safetensors"
    f3.write_bytes(b"c" * 200)
    (root1 / ".hidden" / "skip.safetensors").write_bytes(b"d" * 10)
    (root1 / "checkpoints" / "note.txt").write_text("ignore")
    f4 = root2 / "checkpoints" / "animagineXL40.safetensors"
    f4.write_bytes(b"e" * 150)

    # 稳定 mtime 排序：f1 最新
    import os

    now = time.time()
    for i, f in enumerate([f4, f3, f2, f1]):
        mtime = now - (4 - i) * 100
        os.utime(f, (mtime, mtime))

    monkeypatch.setattr(
        settings, "nas_model_roots", f"{root1},{root2}"
    )
    monkeypatch.setattr(settings, "nas_library_cache_ttl", 60.0)
    return root1, root2, [f1, f2, f3, f4]


class TestHelpers:
    def test_model_extensions(self):
        assert ".safetensors" in model_extensions()

    def test_nsfw_keywords(self):
        assert "nsfw" in nsfw_keywords()

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("majicMIX_v7.safetensors", False),
            ("lustifySDXLNSFW_v8.safetensors", True),
            ("uberRealisticPornMerge_urpmv13.safetensors", True),
            ("SexGod-lora.safetensors", True),
            ("animagineXL40.safetensors", False),
        ],
    )
    def test_is_nsfw_name(self, name, expected):
        assert is_nsfw_name(name) is expected

    def test_is_nsfw_exact_names(self, monkeypatch):
        monkeypatch.setattr(settings, "nsfw_exact_names", "special_model")
        assert is_nsfw_name("special_model.safetensors") is True
        assert is_nsfw_name("special_model_v2.safetensors") is False


class TestScan:
    def test_scan_collects_entries(self, nas_tree):
        svc = NasLibraryService()
        result = svc.list_models(include_nsfw=True)
        names = {e["name"] for e in result["items"]}
        assert names == {
            "majicMIX_v7.safetensors",
            "style.safetensors",
            "lustifySDXLNSFW_v8.safetensors",
            "animagineXL40.safetensors",
        }
        assert result["types"] == ["checkpoints", "loras"]

    def test_entry_fields(self, nas_tree):
        svc = NasLibraryService()
        entry = next(
            e for e in svc.list_models(include_nsfw=True)["items"] if e["name"] == "majicMIX_v7.safetensors"
        )
        assert entry["type"] == "checkpoints"
        assert entry["size"] == 100
        assert entry["nsfw"] is False
        assert entry["rel_path"] == "checkpoints/majicMIX_v7.safetensors"
        assert entry["mtime"] > 0

    def test_nsfw_marking(self, nas_tree):
        svc = NasLibraryService()
        entry = next(
            e for e in svc.list_models(include_nsfw=True)["items"] if "lustify" in e["name"]
        )
        assert entry["nsfw"] is True

    def test_sorted_by_mtime_desc(self, nas_tree):
        svc = NasLibraryService()
        items = svc.list_models(include_nsfw=True)["items"]
        mtimes = [e["mtime"] for e in items]
        assert mtimes == sorted(mtimes, reverse=True)

    def test_missing_root_tolerated(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "nas_model_roots", str(tmp_path / "nonexistent"))
        svc = NasLibraryService()
        result = svc.list_models()
        assert result["total"] == 0
        assert result["error"]  # 不可读时明确失败，不再静默空列表
        assert "不可读" in result["error"]

    def test_stat_failure_skipped(self, nas_tree, monkeypatch):
        """stat 抛 OSError 的文件被跳过且不阻断扫描（77-79 行覆盖）。"""
        root1, _, _ = nas_tree
        target = root1 / "checkpoints" / "majicMIX_v7.safetensors"
        orig_stat = type(target).stat

        def flaky_stat(self, *a, **k):
            if self == target:
                raise OSError("模拟权限不足")
            return orig_stat(self, *a, **k)

        monkeypatch.setattr(type(target), "is_file", lambda self: True)
        monkeypatch.setattr(type(target), "stat", flaky_stat)
        svc = NasLibraryService()
        names = {e["name"] for e in svc.list_models(include_nsfw=True)["items"]}
        assert "majicMIX_v7.safetensors" not in names
        assert "style.safetensors" in names  # 其他文件不受影响


class TestFilters:
    def test_type_filter(self, nas_tree):
        svc = NasLibraryService()
        result = svc.list_models(type_filter="loras", include_nsfw=True)
        assert [e["name"] for e in result["items"]] == ["style.safetensors"]
        # types 返回全量类型（不受过滤影响）
        assert "checkpoints" in result["types"]

    def test_query_filter(self, nas_tree):
        svc = NasLibraryService()
        result = svc.list_models(query="animagine", include_nsfw=True)
        assert [e["name"] for e in result["items"]] == ["animagineXL40.safetensors"]

    def test_query_matches_rel_path(self, nas_tree):
        svc = NasLibraryService()
        result = svc.list_models(query="loras/", include_nsfw=True)
        assert [e["name"] for e in result["items"]] == ["style.safetensors"]

    def test_nsfw_excluded_by_default(self, nas_tree):
        svc = NasLibraryService()
        names = {e["name"] for e in svc.list_models()["items"]}
        assert "lustifySDXLNSFW_v8.safetensors" not in names
        assert len(names) == 3


class TestCache:
    def test_cache_hit_within_ttl(self, nas_tree):
        svc = NasLibraryService()
        r1 = svc.list_models()
        r2 = svc.list_models()
        assert r1["cache_hit"] is False
        assert r2["cache_hit"] is True

    def test_refresh_bypasses_cache(self, nas_tree):
        svc = NasLibraryService()
        svc.list_models()
        r = svc.list_models(refresh=True)
        assert r["cache_hit"] is False

    def test_cache_expires(self, nas_tree, monkeypatch):
        svc = NasLibraryService()
        svc.list_models()
        svc._cache_at -= 3600  # 人工过期
        r = svc.list_models()
        assert r["cache_hit"] is False
