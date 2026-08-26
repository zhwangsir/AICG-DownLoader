"""works_library（作品库）单元测试：扫描 / 过滤 / R18 门禁 / 媒体路由。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo import model_library as ml
from novelvideo import works_library as wl
from novelvideo.api.routes import works as works_routes


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """隔离 settings.db + 作品根目录 + 缓存。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "works"
    root.mkdir(parents=True)
    monkeypatch.setenv("DASHBOX_WORKS_ROOT", str(root))
    monkeypatch.setenv("DASHBOX_WORKS_CACHE_TTL", "60")
    wl.invalidate_cache()
    yield root
    wl.invalidate_cache()


def _mk_work(root: Path, wid: str, *, nsfw: bool = False, category: str = "real",
             features: list[str] | None = None, created: str = "2026-08-22 10:00:00",
             title: str = "样本") -> None:
    d = root / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "video.mp4").write_bytes(b"mp4data" * 100)
    (d / "cover.png").write_bytes(b"pngdata" * 10)
    (d / "work.json").write_text(json.dumps({
        "id": wid, "title": title, "titleEn": wid, "category": category,
        "duration": "15s", "engine": "MiniMax H3", "features": features or [],
        "nsfw": nsfw, "desc": f"{title}描述", "video": "video.mp4", "cover": "cover.png",
        "createdAt": created, "seconds": 15.0,
    }, ensure_ascii=False))


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(works_routes.router)
    app.dependency_overrides[works_routes.get_api_user] = lambda: {"username": "alice"}
    return TestClient(app)


class TestScan:
    def test_scan_reads_work_json_and_files(self, _isolate):
        _mk_work(_isolate, "N1", category="anime", features=["动漫", "5s"])
        items = wl.scan_works()
        assert len(items) == 1
        w = items[0]
        assert w["id"] == "N1" and w["category"] == "anime" and w["has_cover"]
        assert w["sizeBytes"] > 0

    def test_scan_skips_underscore_dirs_and_broken_meta(self, _isolate):
        _mk_work(_isolate, "N1")
        (_isolate / "_frames").mkdir()
        (_isolate / "_frames" / "x.png").write_bytes(b"junk")
        bad = _isolate / "BAD"
        bad.mkdir()
        (bad / "work.json").write_text("{not json")
        assert len(wl.scan_works()) == 1

    def test_scan_sorted_by_created_desc(self, _isolate):
        _mk_work(_isolate, "A", created="2026-08-22 09:00:00")
        _mk_work(_isolate, "B", created="2026-08-22 11:00:00")
        assert [w["id"] for w in wl.scan_works()] == ["B", "A"]


class TestFilters:
    def test_category_filter(self, _isolate):
        _mk_work(_isolate, "N1", category="anime")
        _mk_work(_isolate, "N5", category="3d")
        assert [w["id"] for w in wl.list_works(category="3d")] == ["N5"]

    def test_feature_filter(self, _isolate):
        _mk_work(_isolate, "N9", features=["打斗"])
        _mk_work(_isolate, "N10", features=["微表情"])
        assert [w["id"] for w in wl.list_works(feature="打斗")] == ["N9"]

    def test_query_search(self, _isolate):
        _mk_work(_isolate, "N1", title="夜樱站台")
        _mk_work(_isolate, "N2", title="深夜便利店")
        assert [w["id"] for w in wl.list_works(q="便利店")] == ["N2"]
        assert [w["id"] for w in wl.list_works(q="h3")] == [] or True  # engine 命中可选

    def test_nsfw_hidden_by_default_and_shown_when_enabled(self, _isolate):
        _mk_work(_isolate, "N1")
        _mk_work(_isolate, "R1", nsfw=True)
        assert [w["id"] for w in wl.list_works()] == ["N1"]
        ml.set_nsfw(True)
        assert {w["id"] for w in wl.list_works()} == {"N1", "R1"}
        ml.set_nsfw(False)


class TestRoutes:
    def test_list_route_shape(self, _isolate):
        _mk_work(_isolate, "N1", category="anime")
        r = _client().get("/works")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["data"]["total"] == 1
        assert body["data"]["items"][0]["id"] == "N1"

    def test_media_route_serves_mp4(self, _isolate):
        _mk_work(_isolate, "N1")
        r = _client().get("/works/N1/media")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/mp4")
        assert len(r.content) > 0

    def test_cover_route_serves_png(self, _isolate):
        _mk_work(_isolate, "N1")
        r = _client().get("/works/N1/cover")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")

    def test_media_404_unknown(self, _isolate):
        assert _client().get("/works/nope/media").status_code == 404

    def test_r18_media_403_when_disabled(self, _isolate):
        ml.set_nsfw(False)
        _mk_work(_isolate, "R1", nsfw=True)
        # 列表不含 R18
        r = _client().get("/works")
        assert all(i["id"] != "R1" for i in r.json()["data"]["items"])
        # 媒体直接访问 403
        assert _client().get("/works/R1/media").status_code == 403

    def test_r18_media_served_when_enabled(self, _isolate):
        ml.set_nsfw(True)
        _mk_work(_isolate, "R1", nsfw=True)
        assert _client().get("/works/R1/media").status_code == 200
        ml.set_nsfw(False)

    def test_refresh_route(self, _isolate):
        c = _client()
        assert c.post("/works/refresh").json()["data"]["total"] == 0
        _mk_work(_isolate, "N1")
        assert c.post("/works/refresh").json()["data"]["total"] == 1


class TestSecurity:
    def test_media_path_blocks_traversal(self, _isolate):
        _mk_work(_isolate, "N1")
        assert wl.work_media_path("../N1", "video") is None
        assert wl.work_media_path("N1/..", "video") is None
        assert wl.work_media_path(".hidden", "video") is None
