"""model_library（NAS 模型库 / Civitai 下载 / NSFW 门禁）单元测试。"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from novelvideo import config
from novelvideo import model_library as ml
from novelvideo.api.routes import model_library as ml_routes


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """隔离 settings.db + 模型根目录 + 重置服务单例缓存。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    roots = tmp_path / "models"
    (roots / "checkpoints").mkdir(parents=True)
    (roots / "loras").mkdir(parents=True)
    monkeypatch.setenv("DASHBOX_MODEL_ROOTS", str(roots))
    monkeypatch.setenv("DASHBOX_MODEL_LIBRARY_CACHE_TTL", "60")
    ml.nas_library_service._cache = None
    ml.nas_library_service._cache_at = 0.0
    ml.model_download_service._tasks.clear()
    yield tmp_path


def _mk_model(root: Path, rel: str, size: int = 10, mtime: float | None = None) -> Path:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * size)
    if mtime is not None:
        import os

        os.utime(f, (mtime, mtime))
    return f


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ml_routes.router)
    app.dependency_overrides[ml_routes.get_api_user] = lambda: {"username": "alice"}
    return TestClient(app)


# ---------------------------------------------------------------------------
# NSFW（R18 确认开关）
# ---------------------------------------------------------------------------


class TestNsfwGate:
    def test_default_status_disabled(self):
        assert ml.nsfw_status() == {"nsfw_enabled": False}

    def test_enable_disable_roundtrip(self):
        assert ml.set_nsfw(True) == {"nsfw_enabled": True}
        assert ml.nsfw_status()["nsfw_enabled"] is True
        assert ml.set_nsfw(False) == {"nsfw_enabled": False}
        assert ml.nsfw_status()["nsfw_enabled"] is False

    def test_flag_persisted_in_settings_db(self):
        ml.set_nsfw(True)
        from novelvideo.model_gateway_settings import _read_all

        assert _read_all()["model_library.nsfw_enabled"] == "1"


# ---------------------------------------------------------------------------
# NSFW 手动标记（覆盖关键词判定）
# ---------------------------------------------------------------------------


class TestNsfwMarks:
    def test_marks_empty_by_default(self):
        assert ml.get_nsfw_marks() == {}

    def test_set_and_clear_mark(self):
        data = ml.set_nsfw_mark("loras/h3_musubi_v4.safetensors", True)
        assert data["count"] == 1
        assert ml.get_nsfw_marks() == {"loras/h3_musubi_v4.safetensors": True}
        data = ml.set_nsfw_mark("loras/h3_musubi_v4.safetensors", None)
        assert data["count"] == 0
        assert ml.get_nsfw_marks() == {}

    def test_mark_sfw_override(self):
        ml.set_nsfw_mark("loras/urpm_v2.safetensors", False)
        assert ml.get_nsfw_marks() == {"loras/urpm_v2.safetensors": False}

    def test_mark_normalizes_separators_and_rejects_traversal(self):
        ml.set_nsfw_mark("\\loras\\a.safetensors", True)
        assert "loras/a.safetensors" in ml.get_nsfw_marks()
        with pytest.raises(ml.DownloadServiceError):
            ml.set_nsfw_mark("../evil.safetensors", True)
        with pytest.raises(ml.DownloadServiceError):
            ml.set_nsfw_mark("  ", True)

    def test_marks_tolerate_corrupted_settings(self):
        from novelvideo.model_gateway_settings import _write_many

        _write_many({"model_library.nsfw_marks": "{not-json"})
        assert ml.get_nsfw_marks() == {}
        _write_many({"model_library.nsfw_marks": "[1,2]"})
        assert ml.get_nsfw_marks() == {}

    def test_entry_override_beats_keyword_both_ways(self):
        ml.set_nsfw_mark("loras/plain.safetensors", True)  # 关键词 SFW → 标记 NSFW
        ml.set_nsfw_mark("loras/urpm_x.safetensors", False)  # 关键词 NSFW → 标记 SFW
        assert ml.is_nsfw_entry("plain.safetensors", "loras/plain.safetensors") is True
        assert ml.is_nsfw_entry("urpm_x.safetensors", "loras/urpm_x.safetensors") is False
        # 未标记条目仍走关键词
        assert ml.is_nsfw_entry("urpm_y.safetensors", "loras/urpm_y.safetensors") is True
        assert ml.is_nsfw_entry("safe.safetensors", "loras/safe.safetensors") is False

    def test_scan_applies_marks(self, tmp_path):
        _mk_model(tmp_path / "models", "loras/h3_musubi_v4.safetensors")
        _mk_model(tmp_path / "models", "loras/urpm_v2.safetensors")
        ml.set_nsfw_mark("loras/h3_musubi_v4.safetensors", True)
        ml.set_nsfw_mark("loras/urpm_v2.safetensors", False)
        data = ml.nas_library_service.list_models(refresh=True, include_nsfw=True)
        by_rel = {e["rel_path"]: e["nsfw"] for e in data["items"]}
        assert by_rel["loras/h3_musubi_v4.safetensors"] is True
        assert by_rel["loras/urpm_v2.safetensors"] is False

    def test_set_mark_invalidates_scan_cache(self, tmp_path):
        _mk_model(tmp_path / "models", "loras/plain.safetensors")
        first = ml.nas_library_service.list_models(refresh=True)
        assert first["items"][0]["nsfw"] is False
        ml.set_nsfw_mark("loras/plain.safetensors", True)
        # 缓存已被 set_nsfw_mark 失效，无需 refresh 即见新标记
        second = ml.nas_library_service.list_models(include_nsfw=True)
        entry = next(e for e in second["items"] if e["rel_path"] == "loras/plain.safetensors")
        assert entry["nsfw"] is True

    def test_marks_endpoints_roundtrip(self):
        client = _client()
        resp = client.get("/model-library/nsfw/marks")
        assert resp.status_code == 200
        assert resp.json()["data"] == {"marks": {}, "count": 0}

        resp = client.post(
            "/model-library/nsfw/marks",
            json={"rel_path": "loras/h3_musubi_v4.safetensors", "nsfw": True},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 1

        resp = client.post(
            "/model-library/nsfw/marks",
            json={"rel_path": "loras/h3_musubi_v4.safetensors", "nsfw": None},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 0

    def test_marks_endpoint_rejects_bad_path(self):
        client = _client()
        resp = client.post(
            "/model-library/nsfw/marks", json={"rel_path": "../x.safetensors", "nsfw": True}
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("majicMIX.safetensors", False),
            ("urpm_v2.safetensors", True),
            ("HentaiStyle.pt", True),
            ("R18-lora.safetensors", True),
        ],
    )
    def test_is_nsfw_name(self, name, expected):
        assert ml.is_nsfw_name(name) is expected

    def test_sanitize_filename_strips_path(self):
        assert ml.sanitize_filename("../../loras/evil.safetensors") == "evil.safetensors"
        assert ml.sanitize_filename("a\\b\\c.pt") == "c.pt"

    @pytest.mark.parametrize("bad", ["", ".", "..", "/", "  "])
    def test_sanitize_filename_rejects(self, bad):
        with pytest.raises(ml.DownloadServiceError, match="非法文件名"):
            ml.sanitize_filename(bad)

    def test_apply_hf_mirror(self, monkeypatch):
        monkeypatch.setenv("DASHBOX_HF_ENDPOINT", "https://hf-mirror.com")
        url = "https://huggingface.co/x/resolve/main/m.safetensors"
        assert ml.apply_hf_mirror(url) == "https://hf-mirror.com/x/resolve/main/m.safetensors"
        assert ml.apply_hf_mirror("https://civitai.red/api/download/1") == (
            "https://civitai.red/api/download/1"
        )

    def test_model_roots_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DASHBOX_MODEL_ROOTS", f"{tmp_path}/a, {tmp_path}/b")
        assert ml.model_roots() == [tmp_path / "a", tmp_path / "b"]


# ---------------------------------------------------------------------------
# NAS 模型库扫描
# ---------------------------------------------------------------------------


class TestNasLibrary:
    def test_scan_entries_fields(self, tmp_path):
        mtime = time.time() - 100
        _mk_model(tmp_path / "models", "checkpoints/a.safetensors", size=100, mtime=mtime)
        _mk_model(tmp_path / "models", "loras/b.pt", size=50)
        _mk_model(tmp_path / "models", "loras/readme.txt")  # 非模型扩展名，忽略

        data = ml.nas_library_service.list_models(refresh=True)
        assert data["total"] == 2
        assert data["types"] == ["checkpoints", "loras"]
        entry = next(e for e in data["items"] if e["name"] == "a.safetensors")
        assert entry["name"] == "a.safetensors"
        assert entry["rel_path"] == "checkpoints/a.safetensors"
        assert entry["root"] == "models"
        assert entry["type"] == "checkpoints"
        assert entry["size"] == 100
        assert entry["mtime"] == pytest.approx(mtime, abs=1)
        assert entry["nsfw"] is False

    def test_nsfw_filtered_unless_included(self, tmp_path):
        _mk_model(tmp_path / "models", "loras/urpm_x.pt")
        _mk_model(tmp_path / "models", "loras/safe.pt")
        all_items = ml.nas_library_service.list_models(refresh=True, include_nsfw=True)
        assert all_items["total"] == 2
        sfw = ml.nas_library_service.list_models()
        assert sfw["total"] == 1 and sfw["items"][0]["name"] == "safe.pt"

    def test_type_and_query_filter(self, tmp_path):
        _mk_model(tmp_path / "models", "checkpoints/majic.safetensors")
        _mk_model(tmp_path / "models", "loras/anime.pt")
        by_type = ml.nas_library_service.list_models(refresh=True, type_filter="loras")
        assert by_type["total"] == 1 and by_type["items"][0]["type"] == "loras"
        by_q = ml.nas_library_service.list_models(query="MAJIC")
        assert by_q["total"] == 1 and by_q["items"][0]["name"] == "majic.safetensors"

    def test_ttl_cache_and_refresh(self, tmp_path):
        first = ml.nas_library_service.list_models(refresh=True)
        assert first["cache_hit"] is False
        _mk_model(tmp_path / "models", "loras/new.pt")
        cached = ml.nas_library_service.list_models()
        assert cached["cache_hit"] is True and cached["total"] == 0
        refreshed = ml.nas_library_service.list_models(refresh=True)
        assert refreshed["total"] == 1

    def test_missing_root_treated_as_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DASHBOX_MODEL_ROOTS", str(tmp_path / "nonexistent"))
        data = ml.nas_library_service.list_models(refresh=True)
        assert data["total"] == 0 and data["items"] == []

    def test_multi_root_aggregation(self, monkeypatch, tmp_path):
        r1, r2 = tmp_path / "r1", tmp_path / "r2"
        _mk_model(r1, "checkpoints/a.safetensors")
        _mk_model(r2, "loras/b.pt")
        monkeypatch.setenv("DASHBOX_MODEL_ROOTS", f"{r1},{r2}")
        data = ml.nas_library_service.list_models(refresh=True)
        assert data["total"] == 2
        assert {e["root"] for e in data["items"]} == {"r1", "r2"}


# ---------------------------------------------------------------------------
# 生成前预检
# ---------------------------------------------------------------------------

SDXL_WORKFLOW = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "majic.safetensors"}},
    "2": {"class_type": "LoraLoader", "inputs": {"lora_name": "anime.pt", "model": ["1", 0]}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
    "4": {"class_type": "KSampler", "inputs": {"seed": 1}},  # 非 loader，忽略
    "5": {"class_type": "LoadImage", "inputs": {"image": "demo.png"}},  # 非模型文件，忽略
}


class TestPreflight:
    def test_extract_refs_known_loaders(self):
        refs = ml.extract_model_refs(SDXL_WORKFLOW)
        by_field = {r["field"]: r for r in refs}
        assert len(refs) == 3
        assert by_field["ckpt_name"]["expected_types"] == ["checkpoints"]
        assert by_field["lora_name"]["expected_types"] == ["loras"]
        assert by_field["vae_name"]["expected_types"] == ["vae"]
        assert by_field["ckpt_name"]["node_id"] == "1"

    def test_extract_skips_malformed_nodes(self):
        workflow = {
            "1": "not-a-dict",
            "2": {"class_type": "CheckpointLoaderSimple"},  # 无 inputs
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "  "}},  # 空白忽略
            "4": {"class_type": "UnknownLoader", "inputs": {"ckpt_name": "x.safetensors"}},  # 未映射节点
        }
        assert ml.extract_model_refs(workflow) == []

    def test_extract_multi_field_loader(self):
        workflow = {
            "1": {
                "class_type": "LTXAVTextEncoderLoader",
                "inputs": {"text_encoder": "gemma.safetensors", "ckpt_name": "ltx.safetensors"},
            }
        }
        refs = ml.extract_model_refs(workflow)
        assert {(r["field"], r["expected_types"][0]) for r in refs} == {
            ("text_encoder", "text_encoders"),
            ("ckpt_name", "checkpoints"),
        }

    def test_preflight_present_and_missing(self, tmp_path):
        _mk_model(tmp_path / "models", "checkpoints/majic.safetensors")
        _mk_model(tmp_path / "models", "loras/anime.pt")
        # vae.safetensors 缺失
        result = ml.preflight_workflow(SDXL_WORKFLOW)
        assert result["total"] == 3
        assert result["missing_count"] == 1
        miss = result["missing"][0]
        assert miss["filename"] == "vae.safetensors"
        assert miss["present"] is False and miss["present_anywhere"] is False
        present = [r for r in result["refs"] if r["filename"] == "majic.safetensors"][0]
        assert present["present"] is True

    def test_preflight_wrong_subdir_is_missing_but_anywhere(self, tmp_path):
        # 文件在 loras 却被 checkpoints 引用 → present=False, present_anywhere=True
        _mk_model(tmp_path / "models", "loras/majic.safetensors")
        _mk_model(tmp_path / "models", "loras/anime.pt")
        _mk_model(tmp_path / "models", "vae/vae.safetensors")
        result = ml.preflight_workflow(SDXL_WORKFLOW)
        ckpt = [r for r in result["refs"] if r["field"] == "ckpt_name"][0]
        assert ckpt["present"] is False
        assert ckpt["present_anywhere"] is True
        assert result["missing_count"] == 1

    def test_preflight_unet_accepts_unet_or_diffusion_models(self, tmp_path):
        _mk_model(tmp_path / "models", "diffusion_models/h3.safetensors")
        workflow = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "h3.safetensors"}}}
        result = ml.preflight_workflow(workflow)
        assert result["missing_count"] == 0

    def test_preflight_includes_nsfw_models(self, tmp_path):
        # 比对的是磁盘事实，NSFW 条目也算在位
        _mk_model(tmp_path / "models", "checkpoints/urpm_ckpt.safetensors")
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "urpm_ckpt.safetensors"}}
        }
        result = ml.preflight_workflow(workflow)
        assert result["missing_count"] == 0

    def test_router_preflight_endpoint(self, tmp_path):
        _mk_model(tmp_path / "models", "checkpoints/majic.safetensors")
        c = _client()
        resp = c.post("/model-library/preflight", json={"workflow": SDXL_WORKFLOW})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 3 and data["missing_count"] == 2
        names = {m["filename"] for m in data["missing"]}
        assert names == {"anime.pt", "vae.safetensors"}


# ---------------------------------------------------------------------------
# 下载服务
# ---------------------------------------------------------------------------


class TestDownloadService:
    def test_start_download_whitelist(self):
        with pytest.raises(ml.DownloadServiceError, match="白名单"):
            ml.model_download_service.start_download("http://x/f.safetensors", "f.safetensors", "../../etc")

    def test_start_download_nsfw_gated(self):
        with pytest.raises(ml.DownloadServiceError, match="NSFW"):
            ml.model_download_service.start_download("http://x/urpm.safetensors", "urpm.safetensors", "loras")

    def test_start_download_nsfw_allowed_after_confirm(self):
        ml.set_nsfw(True)
        with respx.mock:
            respx.get("http://x/urpm.safetensors").mock(
                return_value=Response(200, content=b"weights")
            )
            task = ml.model_download_service.start_download(
                "http://x/urpm.safetensors", "urpm.safetensors", "loras"
            )
            assert task["nsfw"] is True
            self._wait(task["task_id"])
            final = ml.model_download_service.get_task(task["task_id"])
            assert final["status"] == "done"

    @staticmethod
    def _wait(task_id: str, timeout: float = 10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = ml.model_download_service.get_task(task_id)
            if t["status"] in ("done", "error", "canceled"):
                return t
            time.sleep(0.02)
        raise AssertionError(f"任务未在 {timeout}s 内结束: {ml.model_download_service.get_task(task_id)}")

    def test_download_success_with_sha256(self, tmp_path):
        payload = b"fake-weights" * 100
        digest = hashlib.sha256(payload).hexdigest()
        with respx.mock:
            respx.get("http://x/m.safetensors").mock(
                return_value=Response(
                    200, content=payload, headers={"content-length": str(len(payload))}
                )
            )
            task = ml.model_download_service.start_download(
                "http://x/m.safetensors", "m.safetensors", "checkpoints", sha256=digest
            )
            final = self._wait(task["task_id"])
            assert final["status"] == "done"
            assert final["total"] == len(payload)
            dest = tmp_path / "models" / "checkpoints" / "m.safetensors"
            assert dest.read_bytes() == payload
            # 下载完成即入库
            lib = ml.nas_library_service.list_models(refresh=True)
            assert lib["total"] == 1 and lib["items"][0]["name"] == "m.safetensors"

    def test_download_sha256_mismatch(self):
        with respx.mock:
            respx.get("http://x/bad.safetensors").mock(return_value=Response(200, content=b"x"))
            task = ml.model_download_service.start_download(
                "http://x/bad.safetensors", "bad.safetensors", "loras", sha256="0" * 64
            )
            final = self._wait(task["task_id"])
            assert final["status"] == "error"
            assert "SHA256" in final["error"]

    def test_download_http_error_cleans_part(self, tmp_path):
        with respx.mock:
            respx.get("http://x/404.safetensors").mock(return_value=Response(404))
            task = ml.model_download_service.start_download(
                "http://x/404.safetensors", "404.safetensors", "loras"
            )
            final = self._wait(task["task_id"])
            assert final["status"] == "error"
            assert not list((tmp_path / "models").rglob("*.part"))

    def test_cancel_task(self):
        with respx.mock:
            respx.get("http://x/slow.safetensors").mock(
                return_value=Response(200, content=b"z" * (8 * 1024 * 1024))
            )
            task = ml.model_download_service.start_download(
                "http://x/slow.safetensors", "slow.safetensors", "loras"
            )
            tid = task["task_id"]
            assert ml.model_download_service.cancel(tid) is True
            final = self._wait(tid)
            assert final["status"] == "canceled"
            assert ml.model_download_service.cancel(tid) is False

    def test_list_tasks_sorted_desc(self):
        with respx.mock:
            respx.get(url__startswith="http://x/").mock(return_value=Response(200, content=b"1"))
            t1 = ml.model_download_service.start_download("http://x/a.pt", "a.pt", "loras")
            t2 = ml.model_download_service.start_download("http://x/b.pt", "b.pt", "loras")
            self._wait(t1["task_id"])
            self._wait(t2["task_id"])
        tasks = ml.model_download_service.list_tasks()
        assert [t["task_id"] for t in tasks] == [t2["task_id"], t1["task_id"]]

    def test_civitai_search_maps_fields(self):
        body = {
            "items": [
                {
                    "id": 1,
                    "name": "majicMIX",
                    "type": "Checkpoint",
                    "nsfw": False,
                    "modelVersions": [
                        {
                            "id": 11,
                            "name": "v7",
                            "files": [
                                {
                                    "name": "majic.safetensors",
                                    "sizeKB": 2000000,
                                    "downloadUrl": "http://dl/1",
                                    "hashes": {"SHA256": "ab" * 32},
                                    "primary": True,
                                },
                                {"name": "nofile"},  # 无 sizeKB，跳过
                            ],
                        }
                    ],
                },
                {"id": 2, "name": "noversion", "modelVersions": []},  # 无版本，跳过
            ]
        }
        with respx.mock:
            route = respx.get("https://civitai.red/api/v1/models").mock(
                return_value=Response(200, json=body)
            )
            data = ml.model_download_service.civitai_search(query="majic", include_nsfw=False)
        assert route.calls.last.request.url.params["nsfw"] == "false"
        assert route.calls.last.request.url.params["query"] == "majic"
        assert data["total"] == 1
        item = data["items"][0]
        assert item["name"] == "majicMIX" and item["type"] == "Checkpoint"
        f = item["versions"][0]["files"][0]
        assert f["sha256"] == "ab" * 32 and f["primary"] is True


# ---------------------------------------------------------------------------
# 路由层
# ---------------------------------------------------------------------------


class TestRouter:
    def test_models_endpoint_envelope_and_filters(self, tmp_path):
        _mk_model(tmp_path / "models", "checkpoints/a.safetensors")
        _mk_model(tmp_path / "models", "loras/urpm_b.pt")
        c = _client()
        resp = c.get("/model-library/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["total"] == 1  # NSFW 未解锁，默认过滤
        resp2 = c.get("/model-library/models", params={"include_nsfw": "true"})
        assert resp2.json()["data"]["total"] == 1  # 仍未开启，include_nsfw 不生效
        ml.set_nsfw(True)
        resp3 = c.get("/model-library/models", params={"include_nsfw": "true", "refresh": "true"})
        assert resp3.json()["data"]["total"] == 2

    def test_nsfw_endpoints(self):
        c = _client()
        assert c.get("/model-library/nsfw").json()["data"] == {"nsfw_enabled": False}
        resp = c.post("/model-library/nsfw", json={"enabled": True})
        assert resp.status_code == 200 and resp.json()["data"] == {"nsfw_enabled": True}
        off = c.post("/model-library/nsfw", json={"enabled": False})
        assert off.json()["data"] == {"nsfw_enabled": False}

    def test_download_endpoints(self, tmp_path):
        c = _client()
        resp = c.post(
            "/model-library/downloads",
            json={
                "download_url": "http://x/m.safetensors",
                "filename": "m.safetensors",
                "subdir": "checkpoints",
            },
        )
        assert resp.status_code == 201
        task = resp.json()["data"]
        TestDownloadService._wait(task["task_id"])
        tasks = c.get("/model-library/downloads").json()["data"]["items"]
        assert len(tasks) == 1
        cancel = c.delete(f"/model-library/downloads/{task['task_id']}")
        assert cancel.status_code == 404  # 已结束不可取消
        assert c.delete("/model-library/downloads/nope").status_code == 404

    def test_download_nsfw_blocked_403(self):
        c = _client()
        resp = c.post(
            "/model-library/downloads",
            json={
                "download_url": "http://x/u.safetensors",
                "filename": "urpm_x.safetensors",
                "subdir": "loras",
            },
        )
        assert resp.status_code == 403

    def test_download_bad_subdir_400(self):
        c = _client()
        resp = c.post(
            "/model-library/downloads",
            json={"download_url": "http://x/a.pt", "filename": "a.pt", "subdir": "hack"},
        )
        assert resp.status_code == 400

    def test_search_endpoint(self):
        with respx.mock:
            respx.get("https://civitai.red/api/v1/models").mock(
                return_value=Response(200, json={"items": []})
            )
            c = _client()
            resp = c.get("/model-library/search", params={"q": "test"})
        assert resp.status_code == 200 and resp.json()["data"]["items"] == []
