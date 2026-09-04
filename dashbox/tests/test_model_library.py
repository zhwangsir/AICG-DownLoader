"""model_library（NAS 模型库 / Civitai 下载 / NSFW 门禁）单元测试。"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

import httpx
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


# ---------------------------------------------------------------------------
# 生图测试台（generate-image：R18 门禁 + 网关转发）
# ---------------------------------------------------------------------------


class _FakeTranslatorAgent:
    """替身 Agent：可注入输出文本或异常。"""

    def __init__(self, output: str | None = None, error: Exception | None = None):
        self._output = output
        self._error = error
        self.calls: list[str] = []

    async def run(self, text: str):
        self.calls.append(text)
        if self._error:
            raise self._error
        import types

        return types.SimpleNamespace(output=self._output)


class TestPromptTranslation:
    """generate-image 中文提示词译写（SDXL 不识别中文，送网关前转英文 tag）。"""

    @pytest.mark.asyncio
    async def test_english_prompt_passes_through_without_llm(self, monkeypatch):
        # 纯英文输入零开销直通：不应构造/调用 Agent
        def _boom():
            raise AssertionError("纯英文提示词不应触达 LLM")

        monkeypatch.setattr(ml_routes, "_get_prompt_translator_agent", _boom)
        out = await ml_routes.translate_prompt_to_english("1girl, red hair, masterpiece")
        assert out == "1girl, red hair, masterpiece"

    @pytest.mark.asyncio
    async def test_chinese_prompt_translated_to_english_tags(self, monkeypatch):
        agent = _FakeTranslatorAgent(output="1girl, 28 years old, long straight black hair, slim, white silk nightgown")
        monkeypatch.setattr(ml_routes, "_get_prompt_translator_agent", lambda: agent)
        out = await ml_routes.translate_prompt_to_english("28岁女性，黑色长直发，纤细体型，白色丝绸睡裙")
        assert out.startswith("1girl")
        assert agent.calls  # 确实走了 LLM

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_original(self, monkeypatch):
        agent = _FakeTranslatorAgent(error=RuntimeError("gateway down"))
        monkeypatch.setattr(ml_routes, "_get_prompt_translator_agent", lambda: agent)
        text = "黑色长直发的女性"
        out = await ml_routes.translate_prompt_to_english(text)
        assert out == text  # fail-open

    @pytest.mark.asyncio
    async def test_suspiciously_short_translation_falls_back(self, monkeypatch):
        # 中文 8 字 → 译写结果 2 字符，明显丢信息，回退原文
        agent = _FakeTranslatorAgent(output="ok")
        monkeypatch.setattr(ml_routes, "_get_prompt_translator_agent", lambda: agent)
        text = "28岁女性黑色长直发纤细体型"
        out = await ml_routes.translate_prompt_to_english(text)
        assert out == text

    @pytest.mark.asyncio
    async def test_empty_and_whitespace_prompts(self):
        assert await ml_routes.translate_prompt_to_english("") == ""
        assert await ml_routes.translate_prompt_to_english("   ") == "   "

    def test_generate_image_endpoint_translates_before_gateway(self, monkeypatch):
        """端点集成：中文 prompt 译写后才送 local_gateway（网关收到英文）。"""
        captured = {}

        def _handler(request: httpx.Request) -> Response:
            captured["body"] = json.loads(request.read())
            return Response(200, json={"data": [{"b64_json": "QUJD"}]})

        agent = _FakeTranslatorAgent(output="1girl, 28 years old, long black straight hair, slim body, white silk nightgown")
        monkeypatch.setattr(ml_routes, "_get_prompt_translator_agent", lambda: agent)

        with respx.mock:
            respx.post(url__regex=r".*/v1/images/generations").mock(side_effect=_handler)
            c = _client()
            r = c.post(
                "/model-library/generate-image",
                json={
                    "prompt": "28岁女性，黑色长直发，白色丝绸睡裙",
                    "negative_prompt": "低画质",
                    "checkpoint": "majicMIX.safetensors",
                },
            )
            assert r.status_code == 200
            body = captured["body"]
            translated = "1girl, 28 years old, long black straight hair, slim body, white silk nightgown"
            assert body["prompt"] == translated
            # negative 同样含中文 → 同一替身 Agent 译写（输出恒定）
            assert body["negative_prompt"] == translated
            # 两次译写（正向 + 负向）都经同一个替身 Agent
            assert len(agent.calls) == 2


class TestGenerateImage:
    def _post(self, client: TestClient, **overrides):
        body = {
            "prompt": "1girl, test",
            "negative_prompt": "lowres",
            "checkpoint": "majicMIX realistic_v7.safetensors",
            "size": "832x1216",
        }
        body.update(overrides)
        return client.post("/model-library/generate-image", json=body)

    def test_nsfw_checkpoint_blocked_without_r18(self):
        with respx.mock:
            # R18 关闭 + NSFW 底模 → 403（不应触达网关）
            c = _client()
            r = self._post(c, checkpoint="lustifySDXLNSFW_apexV8.safetensors")
            assert r.status_code == 403
            assert "R18" in r.json()["detail"]

    def test_nsfw_checkpoint_allowed_with_r18(self):
        ml.set_nsfw(True)
        with respx.mock:
            route = respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(200, json={"data": [{"b64_json": "QUJD"}]})
            )
            c = _client()
            r = self._post(c, checkpoint="lustifySDXLNSFW_apexV8.safetensors")
            assert r.status_code == 200
            assert r.json() == {"ok": True, "data": {"data": [{"b64_json": "QUJD"}]}}
            sent = route.calls.last.request.read()
            assert b"lustifySDXLNSFW" in sent
            assert b"DC-sdxl" in sent

    def test_sfw_checkpoint_passes_without_r18(self):
        with respx.mock:
            route = respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(200, json={"data": [{"b64_json": "QUJD"}]})
            )
            c = _client()
            r = self._post(c)
            assert r.status_code == 200
            assert route.called

    def test_gateway_error_mapped_to_502(self):
        with respx.mock:
            respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(502, json={"error": {"message": "缺失权重"}})
            )
            c = _client()
            r = self._post(c)
            assert r.status_code == 502
            assert "缺失权重" in r.json()["detail"]

    def test_gateway_unreachable_502(self):
        with respx.mock:
            respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            c = _client()
            r = self._post(c)
            assert r.status_code == 502
            assert "不可达" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_project_id_saves_png_and_returns_url(self, monkeypatch, tmp_path):
        import base64

        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"imgdata").decode()
        with respx.mock:
            respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(200, json={"data": [{"b64_json": png}]})
            )

            class _Ctx:
                output_dir = str(tmp_path)
                project_id = "proj-1"

            async def _fake_resolve(**kwargs):
                return _Ctx()

            monkeypatch.setattr(ml_routes, "resolve_project_context", _fake_resolve)
            monkeypatch.setattr(ml_routes, "require_project_home_node", lambda ctx, operation: None)

            from fastapi.testclient import TestClient as TC

            app = FastAPI()
            app.include_router(ml_routes.router)
            app.dependency_overrides[ml_routes.get_api_user] = lambda: {"username": "alice"}
            c = TC(app)
            r = c.post(
                "/model-library/generate-image",
                json={
                    "prompt": "1girl",
                    "checkpoint": "majicMIX.safetensors",
                    "project_id": "proj-1",
                },
            )
            assert r.status_code == 200
            data = r.json()["data"]
            assert data["url"].startswith("/static/projects/proj-1/")
            assert "freezone/_outputs/nsfw_studio/" in data["rel_path"]
            # 文件真实落盘
            saved = tmp_path / data["rel_path"]
            assert saved.read_bytes().startswith(b"\x89PNG")

    def test_reference_url_routes_to_edits(self):
        captured = {}

        def _handler(request: httpx.Request) -> Response:
            captured["path"] = request.url.path
            captured["body"] = request.read()
            return Response(200, json={"data": [{"b64_json": "QUJD"}]})

        with respx.mock:
            respx.post(url__regex=r".*").mock(side_effect=_handler)
            c = _client()
            r = c.post(
                "/model-library/generate-image",
                json={
                    "prompt": "1girl",
                    "checkpoint": "majicMIX.safetensors",
                    "reference_url": "http://x/ref.png",
                },
            )
            assert r.status_code == 200
            assert captured["path"] == "/v1/images/edits"
            assert b"http://x/ref.png" in captured["body"]

    def test_no_project_returns_b64_only(self):
        with respx.mock:
            respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(200, json={"data": [{"b64_json": "QUJD"}]})
            )
            c = _client()
            r = self._post(c)
            assert r.status_code == 200
            assert "url" not in r.json()["data"]


class TestNsfwGateRoundtrip:
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
            ("10Eros_Max_h3_fl2va_beta2_pruned_int8_convrot.safetensors", True),
            ("minimax_h3_fl2va_pruned_int8_convrot.safetensors", False),
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


# ---------------------------------------------------------------------------
# Checkpoint 架构探测（safetensors header → 工作流路由）
# ---------------------------------------------------------------------------


def _mk_safetensors(path: Path, keys: list[str]) -> Path:
    """手写一个只含 header 的合法 safetensors（data 段空）。"""
    import struct

    header = {k: {"dtype": "F32", "shape": [1], "data_offsets": [0, 0]} for k in keys}
    blob = json.dumps(header).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(blob)) + blob)
    return path


class TestArchDetect:
    def test_sd_complete(self, tmp_path):
        _mk_safetensors(
            tmp_path / "models/checkpoints/sdxl.safetensors",
            ["model.diffusion_model.x", "conditioner.embedders.clip_l.y", "vae.decoder"],
        )
        assert ml.detect_checkpoint_arch(
            "checkpoints/sdxl.safetensors", [tmp_path / "models"]
        ) == "sd"

    def test_sd15(self, tmp_path):
        _mk_safetensors(
            tmp_path / "models/checkpoints/sd15.safetensors",
            ["cond_stage_model.transformer", "model.diffusion_model.x"],
        )
        assert ml.detect_checkpoint_arch(
            "checkpoints/sd15.safetensors", [tmp_path / "models"]
        ) == "sd"

    def test_flux_complete(self, tmp_path):
        _mk_safetensors(
            tmp_path / "models/checkpoints/flux.safetensors",
            ["model.diffusion_model.x", "clip_l.y", "t5xxl.z", "vae.decoder"],
        )
        assert ml.detect_checkpoint_arch(
            "checkpoints/flux.safetensors", [tmp_path / "models"]
        ) == "flux"

    def test_flux_double_blocks(self, tmp_path):
        _mk_safetensors(
            tmp_path / "models/checkpoints/f2.safetensors",
            ["double_blocks.0.x", "single_blocks.0.y"],
        )
        # double_blocks 且无 vae → flux-unet
        assert ml.detect_checkpoint_arch(
            "checkpoints/f2.safetensors", [tmp_path / "models"]
        ) == "flux-unet"

    def test_krea2(self, tmp_path):
        _mk_safetensors(
            tmp_path / "models/checkpoints/krea.safetensors",
            ["blocks.0.attn.gate.weight", "txtfusion.w", "tmlp.w"],
        )
        assert ml.detect_checkpoint_arch(
            "checkpoints/krea.safetensors", [tmp_path / "models"]
        ) == "krea2"

    def test_lora_and_other_and_missing(self, tmp_path):
        _mk_safetensors(
            tmp_path / "models/checkpoints/l.safetensors",
            ["lora_te_text_model.0", "lora_unet.1"],
        )
        root = tmp_path / "models"
        assert ml.detect_checkpoint_arch("checkpoints/l.safetensors", [root]) == "lora"
        _mk_safetensors(
            root / "checkpoints/vid.safetensors", ["transformer.blocks.0", "patch_embed"]
        )
        assert ml.detect_checkpoint_arch("checkpoints/vid.safetensors", [root]) == "other"
        # 文件不存在 → unknown（放行，不误拦）
        assert ml.detect_checkpoint_arch("checkpoints/none.safetensors", [root]) == "unknown"

    def test_generate_routes_flux_workflow(self, monkeypatch):
        """arch=flux/krea2 时 payload 带对应 workflow 转发（2026-08-18 krea2 已接入）。"""
        captured = {}

        def _handler(request: httpx.Request) -> Response:
            captured["path"] = request.url.path
            captured["body"] = request.read()
            return Response(200, json={"data": [{"b64_json": "QUJD"}]})

        with respx.mock:
            respx.post(url__regex=r".*").mock(side_effect=_handler)
            c = _client()
            monkeypatch.setattr(
                ml_routes, "detect_checkpoint_arch", lambda rel: "flux"
            )
            r = c.post(
                "/model-library/generate-image",
                json={"prompt": "1girl", "checkpoint": "flux1-dev-fp8.safetensors"},
            )
            assert r.status_code == 200
            assert b"flux" in captured["body"]

            monkeypatch.setattr(
                ml_routes, "detect_checkpoint_arch", lambda rel: "krea2"
            )
            r2 = c.post(
                "/model-library/generate-image",
                json={"prompt": "1girl", "checkpoint": "krea2TurboFP8_krea2TURBO.safetensors"},
            )
            assert r2.status_code == 200
            assert b"krea2" in captured["body"]

    def test_models_endpoint_annotates_arch(self, tmp_path, monkeypatch):
        _mk_safetensors(
            tmp_path / "models/checkpoints/krea2TurboFP8_krea2TURBO.safetensors",
            ["blocks.0.attn.gate.weight", "txtfusion.w"],
        )
        _mk_safetensors(
            tmp_path / "models/checkpoints/flux1-dev-fp8.safetensors",
            ["model.diffusion_model.x", "clip_l.y", "t5xxl.z", "vae.decoder"],
        )
        c = _client()
        items = c.get("/model-library/models").json()["data"]["items"]
        by_name = {e["name"]: e for e in items}
        assert by_name["krea2TurboFP8_krea2TURBO.safetensors"]["arch"] == "krea2"
        # krea2 已接入（UNETLoader 链），不再禁选
        assert not by_name["krea2TurboFP8_krea2TURBO.safetensors"].get("sdxl_incompatible")
        # flux 可用：有 arch 标记但不禁选
        assert by_name["flux1-dev-fp8.safetensors"]["arch"] == "flux"
        assert not by_name["flux1-dev-fp8.safetensors"].get("sdxl_incompatible")


# ---------------------------------------------------------------------------
# SDXL 不兼容清单（生成失败自学习 denylist）
# ---------------------------------------------------------------------------


class TestSdxlIncompatible:
    def _post_gen(self, client, checkpoint="krea2TurboFP8_krea2TURBO.safetensors"):
        return client.post(
            "/model-library/generate-image",
            json={"prompt": "1girl", "checkpoint": checkpoint},
        )

    def test_clip_invalid_error_marks_and_returns_422(self):
        """ComfyUI 报 clip 无效 → 记入清单 + 422 人话报错。"""
        with respx.mock:
            respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(
                    500,
                    json={
                        "error": {
                            "message": (
                                "SDXL 执行失败: [['execution_error', "
                                "{'node_type': 'CLIPTextEncode', 'exception_message': "
                                "\"ERROR: clip input is invalid: None\\n\\nIf the clip is from "
                                "a checkpoint loader node your checkpoint does not contain "
                                "a valid clip or text encoder model.\"}]]"
                            )
                        }
                    },
                )
            )
            c = _client()
            r = self._post_gen(c)
            assert r.status_code == 422
            assert "不含文本编码器" in r.json()["detail"]
            # 已记入清单
            entries = ml.get_sdxl_incompatible()
            assert "krea2TurboFP8_krea2TURBO.safetensors" in entries
            assert "文本编码器" in entries["krea2TurboFP8_krea2TURBO.safetensors"]

    def test_other_gateway_errors_not_marked(self):
        with respx.mock:
            respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(500, json={"error": {"message": "显存不足 OOM"}})
            )
            c = _client()
            r = self._post_gen(c)
            assert r.status_code == 502
            assert ml.get_sdxl_incompatible() == {}

    def test_models_endpoint_flags_incompatible(self, tmp_path):
        _mk_model(tmp_path / "models", "checkpoints/krea2TurboFP8_krea2TURBO.safetensors")
        _mk_model(tmp_path / "models", "checkpoints/majic.safetensors")
        ml.set_sdxl_incompatible(
            "krea2TurboFP8_krea2TURBO.safetensors", "不含文本编码器"
        )
        c = _client()
        items = c.get("/model-library/models").json()["data"]["items"]
        by_name = {e["name"]: e for e in items}
        assert by_name["krea2TurboFP8_krea2TURBO.safetensors"]["sdxl_incompatible"] is True
        assert "sdxl_incompatible" not in by_name["majic.safetensors"]

    def test_management_endpoint_roundtrip(self):
        c = _client()
        resp = c.post(
            "/model-library/sdxl-incompatible",
            json={"filename": "krea2TurboFP8_krea2TURBO.safetensors", "reason": "test"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 1
        # 移除（误记纠正）
        resp = c.post(
            "/model-library/sdxl-incompatible",
            json={"filename": "krea2TurboFP8_krea2TURBO.safetensors", "reason": None},
        )
        assert resp.json()["data"]["count"] == 0
        assert c.get("/model-library/sdxl-incompatible").json()["data"]["entries"] == {}

    def test_success_clears_stale_denylist_entry(self, monkeypatch):
        """生成成功 → 自动洗白历史 denylist 记录（架构接入后残留不再禁选）。"""
        monkeypatch.setattr(ml_routes, "detect_checkpoint_arch", lambda rel: "krea2")
        ml.set_sdxl_incompatible(
            "krea2TurboFP8_krea2TURBO.safetensors", "不含文本编码器（历史误记）"
        )
        with respx.mock:
            respx.post("http://127.0.0.1:8790/v1/images/generations").mock(
                return_value=Response(200, json={"data": [{"b64_json": "QUJD"}]})
            )
            c = _client()
            r = self._post_gen(c)
            assert r.status_code == 200
            # 成功后清单已自动清除
            assert ml.get_sdxl_incompatible() == {}


# ---------------------------------------------------------------------------
# R18 短剧分镜规划（r18-script/plan：门禁 + 同步 LLM 端点）
# ---------------------------------------------------------------------------


class TestR18ScriptPlan:
    def _post(self, client: TestClient, **overrides):
        body = {
            "synopsis": "雨夜酒店，两个陌生人的相遇",
            "characters": [{"name": "林薇", "description": "28岁黑长发女性"}],
            "duration_sec": 90,
        }
        body.update(overrides)
        return client.post("/model-library/r18-script/plan", json=body)

    def test_blocked_without_r18(self):
        c = _client()
        r = self._post(c)
        assert r.status_code == 403
        assert "R18" in r.json()["detail"]

    def test_happy_path_returns_scenes(self, monkeypatch):
        ml.set_nsfw(True)

        async def _fake_plan(req):
            from novelvideo.agents.r18_script_planner import (
                R18Scene,
                R18ScriptPlan,
                normalize_plan,
            )
            return normalize_plan(
                R18ScriptPlan(
                    title="雨夜",
                    scenes=[
                        R18Scene(
                            scene_no=1, kind="portrait", shot_description="定妆",
                            image_prompt="1girl, black hair, masterpiece",
                        ),
                        R18Scene(
                            scene_no=2, kind="action", shot_description="动作",
                            image_prompt="m15510n4ry, 1girl", video_prompt="motion",
                            preset_id="wan22-missionary", audio="tts",
                        ),
                    ],
                )
            )

        monkeypatch.setattr(ml_routes, "plan_r18_script", _fake_plan)
        c = _client()
        r = self._post(c)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["title"] == "雨夜"
        assert [s["kind"] for s in data["scenes"]] == ["portrait", "action"]
        assert data["scenes"][1]["preset_id"] == "h3-aio"

    def test_planner_value_error_maps_503(self, monkeypatch):
        ml.set_nsfw(True)

        async def _boom(req):
            raise ValueError("API key not set")

        monkeypatch.setattr(ml_routes, "plan_r18_script", _boom)
        c = _client()
        r = self._post(c)
        assert r.status_code == 503

    def test_planner_failure_maps_502(self, monkeypatch):
        ml.set_nsfw(True)

        async def _boom(req):
            raise RuntimeError("model timeout")

        monkeypatch.setattr(ml_routes, "plan_r18_script", _boom)
        c = _client()
        r = self._post(c)
        assert r.status_code == 502
        assert "分镜规划失败" in r.json()["detail"]

    def test_empty_synopsis_422(self):
        ml.set_nsfw(True)
        c = _client()
        r = self._post(c, synopsis="")
        assert r.status_code == 422


class TestR18PlannerUnit:
    def test_normalize_falls_back_invalid_preset(self):
        from novelvideo.agents.r18_script_planner import (
            R18Scene,
            R18ScriptPlan,
            normalize_plan,
        )

        plan = normalize_plan(
            R18ScriptPlan(
                title="t",
                scenes=[
                    R18Scene(scene_no=5, kind="action", shot_description="x",
                             image_prompt="p", preset_id="nonexistent"),
                    R18Scene(scene_no=1, kind="portrait", shot_description="y",
                             image_prompt="p", video_prompt="should-clear"),
                    R18Scene(scene_no=2, kind="plot", shot_description="z",
                             image_prompt="p", preset_id="wan22-missionary"),
                ],
            )
        )
        assert [s.scene_no for s in plan.scenes] == [1, 2, 3]
        assert plan.scenes[0].preset_id == "h3-aio"  # 非法 action 预设兜底
        assert plan.scenes[1].video_prompt == ""  # portrait 清运动词
        assert plan.scenes[2].preset_id == ""  # plot 清预设

    def test_build_user_prompt_includes_characters(self):
        from novelvideo.agents.r18_script_planner import (
            R18Character,
            R18ScriptPlanRequest,
            build_user_prompt,
        )

        req = R18ScriptPlanRequest(
            synopsis="梗概内容",
            characters=[R18Character(name="林薇", description="黑长发")],
            style_hint="电影感",
        )
        prompt = build_user_prompt(req)
        assert "梗概内容" in prompt
        assert "林薇" in prompt and "黑长发" in prompt
        assert "电影感" in prompt
        assert "90" in prompt and "9:16" in prompt


# ---------------------------------------------------------------------------
# R18 配音（r18-tts：CosyVoice2 代理）
# ---------------------------------------------------------------------------


class TestR18Tts:
    def _post(self, client: TestClient, **overrides):
        body = {"text": "深夜的酒店房间，灯光很暖。", "voice": "zh-CN-XiaoxiaoNeural"}
        body.update(overrides)
        return client.post("/model-library/r18-tts", json=body)

    def test_blocked_without_r18(self):
        c = _client()
        r = self._post(c)
        assert r.status_code == 403
        assert "R18" in r.json()["detail"]

    def test_happy_path_returns_b64_without_project(self):
        ml.set_nsfw(True)
        mp3 = b"ID3\x04\x00" + b"\x00" * 32  # ID3 头 + 脏数据即可过魔数校验
        with respx.mock:
            route = respx.post("http://192.168.71.127:9201/v1/audio/speech").mock(
                return_value=Response(200, content=mp3, headers={"content-type": "audio/mpeg"})
            )
            c = _client()
            r = self._post(c)
            assert r.status_code == 200
            data = r.json()["data"]
            assert data["format"] == "mp3"
            assert base64.b64decode(data["audio_b64"]) == mp3
            sent = json.loads(route.calls.last.request.read())
            assert sent["voice"] == "zh-CN-XiaoxiaoNeural"
            assert sent["response_format"] == "mp3"
            # 无 emotion → 不带 instructions 字段（走普通合成路径）
            assert "instructions" not in sent

    def test_emotion_maps_to_restricted_instructions(self):
        """emotion 非空 → 拼「请用X的语气说」受限指令集透传（instruct2 路径）。"""
        ml.set_nsfw(True)
        mp3 = b"ID3\x04\x00" + b"\x00" * 32
        with respx.mock:
            route = respx.post("http://192.168.71.127:9201/v1/audio/speech").mock(
                return_value=Response(200, content=mp3, headers={"content-type": "audio/mpeg"})
            )
            c = _client()
            r = self._post(c, emotion="温柔害羞")
            assert r.status_code == 200
            sent = json.loads(route.calls.last.request.read())
            assert sent["instructions"] == "请用温柔害羞的语气说"

    def test_build_instructions_unit(self):
        assert ml_routes._build_instructions("") == ""
        assert ml_routes._build_instructions("  ") == ""
        # 收录情感词 → 丰富指令（语速/声音修饰，2026-08-19 调研：具体词效果明显）
        assert ml_routes._build_instructions("温柔") == "请用温柔缠绵、语速稍缓的语气说"
        assert ml_routes._build_instructions("羞涩。") == "请用害羞羞涩、声音轻柔的语气说"
        assert ml_routes._build_instructions("喘息轻颤") == "请用气声喘息、声音轻颤的语气说"
        assert ml_routes._build_instructions("平静") == "请用自然平和、像朋友聊天的语气说"
        # 未收录词 → 通用受限格式（issue #1802 短格式兜底）
        assert ml_routes._build_instructions("妖媚诱惑") == "请用妖媚诱惑的语气说"

    def test_speed_source_routing(self):
        """speed 透传：dialogue 默认 1.0；narration 默认 1.05；显式 speed 优先。"""
        ml.set_nsfw(True)
        mp3 = b"ID3\x04\x00" + b"\x00" * 32
        with respx.mock:
            route = respx.post("http://192.168.71.127:9201/v1/audio/speech").mock(
                return_value=Response(200, content=mp3, headers={"content-type": "audio/mpeg"})
            )
            c = _client()
            r = self._post(c)  # 默认 dialogue
            assert r.status_code == 200
            sent = json.loads(route.calls.last.request.read())
            assert sent["speed"] == 1.0

            r = self._post(c, source="narration")
            assert r.status_code == 200
            sent = json.loads(route.calls.last.request.read())
            assert sent["speed"] == 1.05

            r = self._post(c, source="narration", speed=0.9)
            assert r.status_code == 200
            sent = json.loads(route.calls.last.request.read())
            assert sent["speed"] == 0.9

    def test_unknown_voice_422(self):
        ml.set_nsfw(True)
        c = _client()
        r = self._post(c, voice="zh-CN-NotExist")
        assert r.status_code == 422
        assert "音色" in r.json()["detail"]

    def test_non_mp3_payload_maps_502(self):
        ml.set_nsfw(True)
        with respx.mock:
            respx.post("http://192.168.71.127:9201/v1/audio/speech").mock(
                return_value=Response(200, json={"error": "boom"})
            )
            c = _client()
            r = self._post(c)
            assert r.status_code == 502

    def test_upstream_unreachable_maps_502(self):
        ml.set_nsfw(True)
        with respx.mock:
            respx.post("http://192.168.71.127:9201/v1/audio/speech").mock(
                side_effect=httpx.ConnectError("refused")
            )
            c = _client()
            r = self._post(c)
            assert r.status_code == 502
            assert "CosyVoice2" in r.json()["detail"]


class TestH3CleanPreset:
    def test_preset_registered_and_lora_free(self):
        from pathlib import Path as _P

        meta = ml_routes.NSFW_VIDEO_PRESETS["h3-clean"]
        assert meta["route"] == "h3"
        path = _P(ml_routes.PRESET_DIR) / meta["file"]
        assert path.is_file(), f"预设文件缺失: {path}"
        wf = json.loads(path.read_text(encoding="utf-8"))
        # 无 LoRA；model 链直连 UNETLoader；音画双解码齐全
        assert not any(n["class_type"] == "LoraLoaderModelOnly" for n in wf.values())
        assert wf["32"]["inputs"]["model"] == ["1", 0]
        assert wf["33"]["inputs"]["model"] == ["1", 0]
        kinds = {n["class_type"] for n in wf.values()}
        assert {"VAEDecode", "VAEDecodeAudio", "CreateVideo", "SaveVideo"} <= kinds
        # 默认提示词不带 NSFW 触发词
        assert "hmmotion" not in wf["20"]["inputs"]["prompt"]


# ---------------------------------------------------------------------------
# R18 成片合成（r18-compose：filter 构造 / 媒体解析 / 端点集成）
# ---------------------------------------------------------------------------


class TestR18ComposeFilter:
    def test_native_plus_tts_layered_mix(self):
        """native 音轨 + tts 配音两路 amix，tts 按镜头起始 adelay。"""
        fc, vout, aout = ml_routes._build_compose_filter(
            num_videos=2,
            video_has_audio=[True, False],
            tts_offsets_ms=[None, 5000],
            has_srt=False,
            target_w=832,
            target_h=1216,
        )
        assert vout == "[vcat]" and aout == "[aout]"
        assert "concat=n=2:v=1:a=0[vcat]" in fc
        assert "[0:a]aresample=24000,aformat=channel_layouts=mono[v0a]" in fc
        # 镜头2 无音轨不进混音；tts 延迟 = 5000 + 250 淡入偏移
        assert "[2:a]aresample=24000,aformat=channel_layouts=mono,adelay=5250:all=1[t1]" in fc
        assert "amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[aout]" in fc
        assert "subtitles" not in fc

    def test_all_silent_falls_back_anullsrc(self):
        fc, vout, aout = ml_routes._build_compose_filter(
            num_videos=2,
            video_has_audio=[False, False],
            tts_offsets_ms=[None, None],
            has_srt=False,
            target_w=832,
            target_h=1216,
        )
        assert "anullsrc=r=24000:cl=mono[aout]" in fc
        assert "amix" not in fc

    def test_srt_burn_after_concat(self):
        fc, vout, aout = ml_routes._build_compose_filter(
            num_videos=1,
            video_has_audio=[True],
            tts_offsets_ms=[0],
            has_srt=True,
            target_w=832,
            target_h=1216,
        )
        assert vout == "[vout]"
        assert "subtitles=sub.srt" in fc
        assert "PingFang SC" in fc


class TestR18ComposeResolveMedia:
    def _mk_output(self, tmp_path):
        out = tmp_path / "output"
        (out / "freezone/_outputs/nsfw_studio").mkdir(parents=True)
        return out

    def test_resolves_static_url(self, tmp_path):
        out = self._mk_output(tmp_path)
        f = out / "freezone/_outputs/nsfw_studio/v1.mp4"
        f.write_bytes(b"x" * 10)
        url = f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{f.name}?v=99"
        resolved = ml_routes._resolve_project_media(out, url, "PRJ123")
        assert resolved == f.resolve()

    def test_rejects_path_escape(self, tmp_path):
        out = self._mk_output(tmp_path)
        evil = "/static/projects/PRJ123/../../etc/passwd"
        with pytest.raises(Exception) as ei:
            ml_routes._resolve_project_media(out, evil, "PRJ123")
        assert "越界" in str(ei.value)

    def test_rejects_missing_file(self, tmp_path):
        out = self._mk_output(tmp_path)
        with pytest.raises(Exception) as ei:
            ml_routes._resolve_project_media(
                out, "/static/projects/PRJ123/freezone/_outputs/nsfw_studio/nope.mp4", "PRJ123"
            )
        assert "不存在" in str(ei.value)


class TestR18ComposeEndpoint:
    def _mk_ctx(self, monkeypatch, tmp_path):
        """挂 fake resolve_project_context（editor 权限）指向 tmp 输出目录。"""
        out = tmp_path / "output"
        (out / "freezone/_outputs/nsfw_studio").mkdir(parents=True)

        class _Ctx:
            project_id = "PRJ123"
            output_dir = str(out)

        async def _fake_resolve(**kwargs):
            return _Ctx()

        def _fake_require(ctx, **kwargs):
            return None

        monkeypatch.setattr(ml_routes, "resolve_project_context", _fake_resolve)
        monkeypatch.setattr(ml_routes, "require_project_home_node", _fake_require)
        return out

    def test_compose_blocked_without_r18(self):
        c = _client()
        r = c.post(
            "/model-library/r18-compose",
            json={"project_id": "P", "shots": [{"video_url": "/static/x.mp4"}]},
        )
        assert r.status_code == 403

    def test_compose_happy_path(self, monkeypatch, tmp_path):
        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        v1 = out / "freezone/_outputs/nsfw_studio/v1.mp4"
        v2 = out / "freezone/_outputs/nsfw_studio/v2.mp4"
        t1 = out / "freezone/_outputs/nsfw_studio/tts1.mp3"
        for f in (v1, v2, t1):
            f.write_bytes(b"x" * 16)

        async def _dur(path):
            return 5.0

        async def _aud(path):
            return path.suffix == ".mp4" and "v1" in path.name

        async def _size(path):
            return (832, 1216)

        executed: dict = {}

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def _fake_exec(*cmd, **kwargs):
            executed["cmd"] = cmd
            executed["cwd"] = kwargs.get("cwd")
            # ffmpeg 执行时捕获字幕文件内容（with 块退出后 tmpdir 即清理）
            sub = Path(kwargs.get("cwd") or ".") / "sub.srt"
            executed["srt"] = sub.read_text(encoding="utf-8") if sub.exists() else None
            # 模拟 ffmpeg 产出成片文件（从 cmd 末尾取输出路径）
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
            return _FakeProc()

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)
        monkeypatch.setattr(ml_routes, "_probe_video_size", _size)
        monkeypatch.setattr(ml_routes.asyncio, "create_subprocess_exec", _fake_exec)

        c = _client()
        r = c.post(
            "/model-library/r18-compose",
            json={
                "project_id": "PRJ123",
                "title": "深夜初遇",
                "srt": "1\n00:00:00,000 --> 00:00:05,000\n测试字幕\n",
                "shots": [
                    {"video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{v1.name}", "tts_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{t1.name}", "audio_mode": "tts"},
                    {"video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{v2.name}", "audio_mode": "none"},
                ],
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["duration_sec"] == 10.0
        assert data["shots"] == 2
        assert "/static/projects/PRJ123/" in data["url"]
        cmd = executed["cmd"]
        # 输入顺序：2 视频 + 1 mp3；字幕烧录在列
        inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
        assert len(inputs) == 3
        assert str(v1) in inputs[0] and str(v2) in inputs[1] and str(t1) in inputs[2]
        fc = cmd[cmd.index("-filter_complex") + 1]
        # 镜头1 的 tts offset=0 → adelay=0+250 淡入偏移
        assert "concat=n=2" in fc and "adelay=250:all=1" in fc and "subtitles=sub.srt" in fc
        # 字幕内容已落盘到 ffmpeg cwd
        assert executed["srt"] is not None and executed["srt"].startswith("1\n")

    def test_compose_ffmpeg_failure_maps_502(self, monkeypatch, tmp_path):
        ml.set_nsfw(True)
        out = self._mk_ctx(monkeypatch, tmp_path)
        v1 = out / "freezone/_outputs/nsfw_studio/v1.mp4"
        v1.write_bytes(b"x" * 16)

        async def _dur(path):
            return 5.0

        async def _aud(path):
            return False

        async def _size(path):
            return (832, 1216)

        class _FakeProc:
            returncode = 1

            async def communicate(self):
                return b"", b"boom\nInvalid data"

        async def _fake_exec(*cmd, **kwargs):
            return _FakeProc()

        monkeypatch.setattr(ml_routes, "_probe_media_duration", _dur)
        monkeypatch.setattr(ml_routes, "_probe_has_audio", _aud)
        monkeypatch.setattr(ml_routes, "_probe_video_size", _size)
        monkeypatch.setattr(ml_routes.asyncio, "create_subprocess_exec", _fake_exec)

        c = _client()
        r = c.post(
            "/model-library/r18-compose",
            json={
                "project_id": "PRJ123",
                "shots": [{"video_url": f"/static/projects/PRJ123/freezone/_outputs/nsfw_studio/{v1.name}"}],
            },
        )
        assert r.status_code == 502
        assert "ffmpeg" in r.json()["detail"]


# ---------------------------------------------------------------------------
# R18 视频生成（generate-video：预设直提 ComfyUI + mp4 落盘）
# ---------------------------------------------------------------------------


class TestGenerateVideo:
    def _post(self, client: TestClient, **overrides):
        body = {
            "preset_id": "h3-aio",
            "prompt": "m15510n4ry, a woman",
            "first_frame_url": "http://x/frame.png",
        }
        body.update(overrides)
        return client.post("/model-library/generate-video", json=body)

    def test_blocked_without_r18(self):
        with respx.mock:
            c = _client()
            r = self._post(c)
            assert r.status_code == 403
            assert "R18" in r.json()["detail"]

    def test_unknown_preset_400(self):
        ml.set_nsfw(True)
        with respx.mock:
            c = _client()
            r = self._post(c, preset_id="no-such")
            assert r.status_code == 400
            assert "h3-aio" in r.json()["detail"]

    def test_wan_preset_rejected_on_generate_path(self):
        """Wan JSON 留盘，但短剧 generate 路径不得选 Wan。"""
        ml.set_nsfw(True)
        with respx.mock:
            c = _client()
            r = self._post(c, preset_id="wan22-missionary")
            assert r.status_code == 400
            detail = r.json()["detail"]
            assert "MiniMax-H3" in detail
            assert "h3-aio" in detail
            assert "wan22" not in detail

    def test_video_presets_hidden_without_r18(self):
        c = _client()
        assert c.get("/model-library/video-presets").json()["data"]["items"] == []
        ml.set_nsfw(True)
        items = c.get("/model-library/video-presets").json()["data"]["items"]
        assert {i["id"] for i in items} == {"h3-aio", "h3-clean"}
        assert all(i["route"] == "h3" for i in items)

    def test_patch_workflow_wan(self):
        wf, _ = ml_routes._load_preset_workflow("wan22-missionary")
        out = ml_routes._patch_video_workflow(
            wf,
            prompt="POS",
            negative_prompt="NEG",
            first_frame_name="f.png",
            width=480,
            height=832,
            length=81,
            seed=42,
        )
        i2v = next(n for n in out.values() if n["class_type"] == "WanImageToVideo")
        assert i2v["inputs"]["width"] == 480 and i2v["inputs"]["length"] == 81
        load = next(n for n in out.values() if n["class_type"] == "LoadImage")
        assert load["inputs"]["image"] == "f.png"
        texts = [n for n in out.values() if n["class_type"] == "CLIPTextEncode"]
        by_text = {n["inputs"]["text"]: n for n in texts}
        assert "POS" in by_text and "NEG" in by_text
        # 正向进 title 含「正向」的节点
        pos_node = by_text["POS"]
        assert "正向" in pos_node["_meta"]["title"]
        seeds = {
            n["inputs"]["noise_seed"]
            for n in out.values()
            if n["class_type"] == "KSamplerAdvanced"
        }
        assert seeds == {42}

    def test_patch_workflow_h3(self):
        wf, _ = ml_routes._load_preset_workflow("h3-aio")
        out = ml_routes._patch_video_workflow(
            wf,
            prompt="hmmotion, scene",
            negative_prompt=None,
            first_frame_name="h.png",
            width=768,
            height=1344,
            length=124,
            seed=7,
        )
        i2v = next(n for n in out.values() if n["class_type"] == "MiniMaxH3ImageToVideo")
        assert i2v["inputs"]["prompt"] == "hmmotion, scene"
        unet = next(n for n in out.values() if n["class_type"] == "UNETLoader")
        assert unet["inputs"]["unet_name"].startswith("10Eros")
        assert i2v["inputs"]["width"] == 768 and i2v["inputs"]["length"] == 124
        noise = next(n for n in out.values() if n["class_type"] == "RandomNoise")
        assert noise["inputs"]["noise_seed"] == 7
        load = next(n for n in out.values() if n["class_type"] == "LoadImage")
        assert load["inputs"]["image"] == "h.png"

    def test_project_id_saves_mp4_and_returns_url(self, monkeypatch, tmp_path):
        ml.set_nsfw(True)

        async def _fake_submit(workflow, first_frame_url):
            assert first_frame_url == "http://x/frame.png"
            return {"video_bytes": b"\x00\x00\x00\x18ftypmp42DATA", "filename": "out.mp4", "backend": "http://lb"}

        monkeypatch.setattr(ml_routes, "_submit_and_collect", _fake_submit)

        class _Ctx:
            output_dir = str(tmp_path)
            project_id = "proj-1"

        async def _fake_resolve(**kwargs):
            return _Ctx()

        monkeypatch.setattr(ml_routes, "resolve_project_context", _fake_resolve)
        monkeypatch.setattr(ml_routes, "require_project_home_node", lambda ctx, operation: None)

        c = _client()
        r = self._post(c, project_id="proj-1", seed=99)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["seed"] == 99
        assert data["url"].startswith("/static/projects/proj-1/")
        assert data["rel_path"].endswith(".mp4")
        assert "freezone/_outputs/nsfw_studio/" in data["rel_path"]
        saved = tmp_path / data["rel_path"]
        assert saved.read_bytes().startswith(b"\x00\x00\x00\x18ftyp")

    def test_random_seed_when_absent(self, monkeypatch):
        ml.set_nsfw(True)

        async def _fake_submit(workflow, first_frame_url):
            return {"video_bytes": b"v", "filename": "out.mp4", "backend": "http://lb"}

        monkeypatch.setattr(ml_routes, "_submit_and_collect", _fake_submit)
        with respx.mock:
            c = _client()
            r = self._post(c)
        assert r.status_code == 200
        assert isinstance(r.json()["data"]["seed"], int)

    def test_upload_first_frame_accepts_data_uri(self):
        """上游图片节点旧产物为 b64 data URI 时直接解码，不走 httpx。"""
        import asyncio

        png = b"\x89PNG\r\n\x1a\n" + b"frame"
        b64 = base64.b64encode(png).decode()
        data_uri = f"data:image/png;base64,{b64}"
        received: list[bytes] = []

        def _handler(request: httpx.Request) -> Response:
            if request.url.path == "/upload/image":
                received.append(request.content)
                return Response(200, json={"name": "ok"})
            return Response(200, json={"prompt_id": "x"})

        with respx.mock:
            respx.post(url__regex=r"^http://h3:8195/upload/image").mock(side_effect=_handler)
            async def _run():
                async with httpx.AsyncClient() as client:
                    return await ml_routes._upload_first_frame(
                        client, data_uri, ["http://h3:8195"]
                    )
            filename = asyncio.run(_run())
            assert filename.startswith("dc_") and filename.endswith(".png")
            assert len(received) == 1
            # multipart body 内嵌了原始 PNG 字节
            assert b"\x89PNG" in received[0]

    def test_upload_first_frame_rejects_bad_data_uri(self):
        import asyncio

        with respx.mock:
            async def _run():
                async with httpx.AsyncClient() as client:
                    return await ml_routes._upload_first_frame(
                        client, "data:image/png;base64,!!not-b64!!", ["http://h3:8195"]
                    )
            try:
                asyncio.run(_run())
                raise AssertionError("应当抛 400")
            except Exception as e:
                assert "400" in str(e) or "data URI" in str(e)

    def test_submit_routes_by_workflow_class(self):
        """Wan 路线走 LB 且首帧覆盖三后端；H3 路线单点。"""
        import asyncio

        wan_wf, _ = ml_routes._load_preset_workflow("wan22-missionary")
        h3_wf, _ = ml_routes._load_preset_workflow("h3-aio")
        uploads: list[str] = []
        histories: dict[str, dict] = {}

        def _make_handler(backend: str):
            def _handler(request: httpx.Request) -> Response:
                if request.url.path == "/upload/image":
                    uploads.append(backend)
                    return Response(200, json={"name": "ok"})
                if request.url.path == "/prompt":
                    pid = f"pid-{backend}"
                    histories[pid] = {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {"50": {"gifs": [{"filename": "v.mp4", "subfolder": "nsfw", "type": "output"}]}},
                    }
                    return Response(200, json={"prompt_id": pid})
                if request.url.path.startswith("/history/"):
                    pid = request.url.path.split("/")[-1]
                    return Response(200, json={pid: histories[pid]})
                if request.url.path == "/view":
                    return Response(200, content=b"mp4bytes")
                return Response(404)

            return _handler

        original_interval = ml_routes.VIDEO_POLL_INTERVAL
        original_max_wait = ml_routes.VIDEO_MAX_WAIT_SECONDS
        ml_routes.VIDEO_POLL_INTERVAL = 0.01  # 测试几乎不等待
        ml_routes.VIDEO_MAX_WAIT_SECONDS = 10.0  # 失败快速暴露，不卡测试
        try:
            # 注意：必须用 `with respx.mock:`（全局 mocker）——`respx.mock(...)`
            # 带参调用返回独立 mocker，模块级 respx.post 注册不进去，请求会
            # passthrough 到真实集群（曾触发真实提交，见调试记录）。
            with respx.mock:
                for base, tag in [
                    (r"http://192\.168\.71\.127:8196", ":8196"),
                    (r"http://192\.168\.71\.116:8188", "pc01"),
                    (r"http://192\.168\.71\.114:8193", "pc02"),
                    (r"http://192\.168\.71\.127:8188", "lb"),
                    (r"http://192\.168\.71\.127:8195", "h3"),
                ]:
                    respx.post(url__regex=rf"^{base}/(upload/image|prompt|view)").mock(
                        side_effect=_make_handler(tag)
                    )
                    respx.get(url__regex=rf"^{base}/(history|view)").mock(
                        side_effect=_make_handler(tag)
                    )
                respx.get("http://x/frame.png").mock(
                    return_value=Response(200, content=b"png")
                )

                async def _run(wf):
                    return await ml_routes._submit_and_collect(wf, "http://x/frame.png")

                wan_result = asyncio.run(_run(wan_wf))
                assert wan_result["backend"] == "http://192.168.71.127:8188"
                assert wan_result["video_bytes"] == b"mp4bytes"
                # 首帧已覆盖全部三后端（LB 随机路由不丢文件）
                assert set(uploads) == {":8196", "pc01", "pc02"}

                uploads.clear()
                h3_result = asyncio.run(_run(h3_wf))
                assert h3_result["backend"] == "http://192.168.71.127:8195"
                assert uploads == ["h3"]
        finally:
            ml_routes.VIDEO_POLL_INTERVAL = original_interval
            ml_routes.VIDEO_MAX_WAIT_SECONDS = original_max_wait
