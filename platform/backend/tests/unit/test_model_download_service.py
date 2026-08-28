"""model_download_service 单元测试（M27：Civitai 搜索 + 后台下载）。"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.services.model_download_service import (
    DownloadServiceError,
    ModelDownloadService,
    apply_hf_mirror,
    resolve_download_root,
    sanitize_filename,
)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "nas_model_roots", str(tmp_path / "models"))
    monkeypatch.setattr(settings, "download_chunk_size", 4)
    s = ModelDownloadService()
    s._http = MagicMock()
    return s


def _civitai_payload():
    return {
        "items": [
            {
                "id": 1,
                "name": "TestModel",
                "type": "Checkpoint",
                "nsfw": False,
                "modelVersions": [
                    {
                        "id": 11,
                        "name": "v1",
                        "files": [
                            {
                                "name": "model.safetensors",
                                "sizeKB": 2048,
                                "downloadUrl": "https://civitai.red/dl/1",
                                "hashes": {"SHA256": "abc123"},
                                "primary": True,
                            },
                            {"name": "preview.png"},  # 无 sizeKB → 跳过
                        ],
                    }
                ],
            },
            {
                "id": 2,
                "name": "NoFiles",
                "type": "LORA",
                "nsfw": True,
                "modelVersions": [{"id": 21, "name": "v1", "files": []}],
            },
        ]
    }


class TestCivitaiSearch:
    def test_search_parsing(self, svc):
        resp = MagicMock()
        resp.json.return_value = _civitai_payload()
        resp.raise_for_status = MagicMock()
        svc._http.get.return_value = resp

        result = svc.civitai_search(query="test", model_type="Checkpoint", limit=10)
        assert result["total"] == 1  # NoFiles 被跳过
        item = result["items"][0]
        assert item["name"] == "TestModel"
        assert item["nsfw"] is False
        f = item["versions"][0]["files"][0]
        assert f["sha256"] == "abc123"
        assert f["primary"] is True
        # 请求参数
        params = svc._http.get.call_args.kwargs["params"]
        assert params["query"] == "test"
        assert params["types"] == "Checkpoint"
        assert params["nsfw"] == "false"

    def test_search_nsfw_param(self, svc):
        resp = MagicMock()
        resp.json.return_value = {"items": []}
        svc._http.get.return_value = resp
        svc.civitai_search(include_nsfw=True)
        assert svc._http.get.call_args.kwargs["params"]["nsfw"] == "true"

    def test_search_limit_clamped(self, svc):
        resp = MagicMock()
        resp.json.return_value = {"items": []}
        svc._http.get.return_value = resp
        svc.civitai_search(limit=500)
        assert svc._http.get.call_args.kwargs["params"]["limit"] == 100


class TestHelpers:
    def test_sanitize_filename(self):
        assert sanitize_filename("a/b/c.safetensors") == "c.safetensors"
        assert sanitize_filename("..\\..\\evil.safetensors") == "evil.safetensors"
        with pytest.raises(DownloadServiceError):
            sanitize_filename("..")
        with pytest.raises(DownloadServiceError):
            sanitize_filename("/")

    def test_apply_hf_mirror(self, monkeypatch):
        monkeypatch.setattr(settings, "hf_endpoint", "https://hf-mirror.com")
        assert apply_hf_mirror("https://huggingface.co/x/y.safetensors") == (
            "https://hf-mirror.com/x/y.safetensors"
        )
        assert apply_hf_mirror("https://civitai.red/dl/1") == "https://civitai.red/dl/1"

    def test_resolve_download_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "nas_model_roots", f"{tmp_path}/a, {tmp_path}/b")
        assert resolve_download_root() == tmp_path / "a"


def _stream_response(body: bytes, chunk: int = 4):
    resp = MagicMock()
    resp.headers = {"content-length": str(len(body))}
    resp.raise_for_status = MagicMock()
    resp.iter_bytes = MagicMock(
        side_effect=lambda n: [body[i : i + n] for i in range(0, len(body), n)]
    )
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _wait_done(svc, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = svc.get_task(task_id)
        if t["status"] in ("done", "error", "canceled"):
            return t
        time.sleep(0.02)
    raise AssertionError("任务超时未结束")


class TestDownload:
    def test_start_download_validation(self, svc):
        with pytest.raises(DownloadServiceError, match="白名单"):
            svc.start_download("https://x/f", "f.safetensors", "bad_dir")

    def test_nsfw_gate_blocks_when_disabled(self, svc):
        with pytest.raises(DownloadServiceError, match="NSFW"):
            svc.start_download(
                "https://x/f", "lustifyNSFW_v8.safetensors", "checkpoints"
            )

    def test_nsfw_gate_passes_when_enabled(self, svc, monkeypatch, tmp_path):
        from app.services import settings_service as ss_mod

        monkeypatch.setattr(
            ss_mod.settings_service,
            "nsfw_status",
            lambda: {"nsfw_enabled": True, "has_pin": True},
        )
        body = b"0123456789"
        svc._http.stream.return_value = _stream_response(body)
        task = svc.start_download(
            "https://x/f", "lustifyNSFW_v8.safetensors", "checkpoints"
        )
        final = _wait_done(svc, task["task_id"])
        assert final["status"] == "done"
        assert final["nsfw"] is True

    def test_download_success(self, svc, tmp_path):
        body = b"hello world!"
        svc._http.stream.return_value = _stream_response(body)
        task = svc.start_download("https://x/f", "a.safetensors", "checkpoints")
        final = _wait_done(svc, task["task_id"])
        assert final["status"] == "done"
        assert final["downloaded"] == len(body)
        dest = tmp_path / "models" / "checkpoints" / "a.safetensors"
        assert dest.read_bytes() == body
        assert not (tmp_path / "models" / "checkpoints" / "a.safetensors.part").exists()

    def test_download_sha256_ok(self, svc, tmp_path):
        body = b"verify me"
        digest = hashlib.sha256(body).hexdigest()
        svc._http.stream.return_value = _stream_response(body)
        task = svc.start_download(
            "https://x/f", "b.safetensors", "loras", sha256=digest
        )
        final = _wait_done(svc, task["task_id"])
        assert final["status"] == "done"

    def test_download_sha256_mismatch_deletes_file(self, svc, tmp_path):
        svc._http.stream.return_value = _stream_response(b"bad body")
        task = svc.start_download(
            "https://x/f", "c.safetensors", "vae", sha256="0" * 64
        )
        final = _wait_done(svc, task["task_id"])
        assert final["status"] == "error"
        assert "SHA256 校验失败" in final["error"]
        assert not (tmp_path / "models" / "vae" / "c.safetensors").exists()
        assert not (tmp_path / "models" / "vae" / "c.safetensors.part").exists()

    def test_download_http_error(self, svc):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=Exception("连接失败"))
        ctx.__exit__ = MagicMock(return_value=False)
        svc._http.stream.return_value = ctx
        task = svc.start_download("https://x/f", "d.safetensors", "clip")
        final = _wait_done(svc, task["task_id"])
        assert final["status"] == "error"
        assert "连接失败" in final["error"]

    def test_download_cancel(self, svc):
        # 慢速流：每 chunk 后检查取消
        body = b"x" * 64
        resp = MagicMock()
        resp.headers = {"content-length": str(len(body))}
        resp.raise_for_status = MagicMock()

        def slow_iter(n):
            for i in range(0, len(body), n):
                time.sleep(0.05)
                yield body[i : i + n]

        resp.iter_bytes = MagicMock(side_effect=slow_iter)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=resp)
        ctx.__exit__ = MagicMock(return_value=False)
        svc._http.stream.return_value = ctx

        task = svc.start_download("https://x/f", "e.safetensors", "unet")
        time.sleep(0.1)
        assert svc.cancel(task["task_id"]) is True
        final = _wait_done(svc, task["task_id"])
        assert final["status"] == "canceled"

    def test_cancel_nonexistent(self, svc):
        assert svc.cancel("nope") is False

    def test_cancel_finished_task(self, svc):
        svc._http.stream.return_value = _stream_response(b"done")
        task = svc.start_download("https://x/f", "f.safetensors", "controlnet")
        _wait_done(svc, task["task_id"])
        assert svc.cancel(task["task_id"]) is False

    def test_cancel_pending_task(self, svc, monkeypatch):
        # 冻结线程启动，任务停在 pending → cancel 直接置 canceled（确定性，无竞态）
        monkeypatch.setattr(
            "app.services.model_download_service.threading.Thread",
            lambda *a, **k: MagicMock(start=MagicMock()),
        )
        task = svc.start_download("https://x/f", "g.safetensors", "embeddings")
        assert svc.get_task(task["task_id"])["status"] == "pending"
        assert svc.cancel(task["task_id"]) is True
        assert svc.get_task(task["task_id"])["status"] == "canceled"

    def test_hf_mirror_applied_in_download(self, svc, tmp_path):
        body = b"hf"
        svc._http.stream.return_value = _stream_response(body)
        svc.start_download(
            "https://huggingface.co/repo/resolve/main/m.safetensors",
            "m.safetensors",
            "ipadapter",
        )
        time.sleep(0.3)
        url = svc._http.stream.call_args.args[1] if len(svc._http.stream.call_args.args) > 1 else svc._http.stream.call_args[0][1]
        assert "hf-mirror.com" in url

    def test_list_tasks_sorted_desc(self, svc):
        svc._http.stream.return_value = _stream_response(b"z")
        t1 = svc.start_download("https://x/1", "t1.safetensors", "clip_vision")
        t2 = svc.start_download("https://x/2", "t2.safetensors", "clip_vision")
        _wait_done(svc, t1["task_id"])
        _wait_done(svc, t2["task_id"])
        tasks = svc.list_tasks()
        assert tasks[0]["task_id"] == t2["task_id"]

    def test_get_task_missing(self, svc):
        assert svc.get_task("missing") is None


class TestResolveDownloadRootReadableFirst:
    """Mac 上 nas_model_roots 第一项常是不可读的 /mnt/toiv-nas，应跳到第一个可读根。"""

    def test_skips_missing_first_picks_readable_second(self, monkeypatch, tmp_path):
        missing = tmp_path / "mnt-toiv-nas"
        readable = tmp_path / "ok-nas"
        readable.mkdir()
        monkeypatch.setattr(settings, "nas_model_roots", f"{missing},{readable}")
        assert resolve_download_root() == readable

    def test_prefers_writable_over_readonly(self, monkeypatch, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        ok = tmp_path / "ok"
        ok.mkdir()
        locked.chmod(0o500)
        try:
            monkeypatch.setattr(settings, "nas_model_roots", f"{locked},{ok}")
            assert resolve_download_root() == ok
        finally:
            locked.chmod(0o700)

    def test_empty_list_falls_back_to_default(self, monkeypatch):
        from app.services.nas_library_service import DEFAULT_DOWNLOAD_ROOT

        monkeypatch.setattr(settings, "nas_model_roots", "  ,  ")
        assert resolve_download_root() == Path(DEFAULT_DOWNLOAD_ROOT)

    def test_neither_exists_keeps_first_listed(self, monkeypatch, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        monkeypatch.setattr(settings, "nas_model_roots", f"{a}, {b}")
        assert resolve_download_root() == a
