from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app():
    from novelvideo.api.routes import drama_proxy

    application = FastAPI()
    application.include_router(drama_proxy.router)
    return application


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.content = content
        self.headers = httpx.Headers(headers or {"content-type": "application/json"})


def test_drama_api_base_default(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.delenv("ST_DRAMA_API_URL", raising=False)
    assert drama_proxy.drama_api_base() == "http://127.0.0.1:8100"


def test_drama_api_base_override(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.setenv("ST_DRAMA_API_URL", "http://host.docker.internal:8100/")
    assert drama_proxy.drama_api_base() == "http://host.docker.internal:8100"


def test_drama_proxy_forwards_health(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.setenv("ST_DRAMA_API_URL", "http://127.0.0.1:8100")
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, content=None, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            return _FakeResponse(
                200,
                b'{"status":"ok","version":"0.4.0"}',
                {"content-type": "application/json"},
            )

    monkeypatch.setattr(drama_proxy.httpx, "AsyncClient", _Client)
    client = TestClient(_app())
    response = client.get("/api/drama/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert captured["method"] == "GET"
    assert captured["url"] == "http://127.0.0.1:8100/api/drama/health"


def test_drama_proxy_rejects_scheme_in_path() -> None:
    from novelvideo.api.routes import drama_proxy

    assert drama_proxy._sanitize_subpath("http://evil.example/api") is None
    client = TestClient(_app())
    response = client.get("/api/drama/http://evil.example")
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_drama_proxy_upstream_down(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.setenv("ST_DRAMA_API_URL", "http://127.0.0.1:8100")

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, content=None, headers=None):
            raise httpx.ConnectError("connection refused", request=httpx.Request(method, url))

    monkeypatch.setattr(drama_proxy.httpx, "AsyncClient", _Client)
    client = TestClient(_app())
    response = client.get("/api/drama/health")
    assert response.status_code == 502
    body = response.json()
    assert body["ok"] is False
    assert body["data"]["upstream"] == "http://127.0.0.1:8100"


def test_drama_proxy_rewrites_static_to_platform_static(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.setenv("ST_DRAMA_API_URL", "http://127.0.0.1:8100")
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, content=None, headers=None):
            captured["method"] = method
            captured["url"] = url
            return _FakeResponse(200, b"png", {"content-type": "image/png"})

    monkeypatch.setattr(drama_proxy.httpx, "AsyncClient", _Client)
    client = TestClient(_app())
    response = client.get("/api/drama/static/storyboard/s1.png")
    assert response.status_code == 200
    assert captured["method"] == "GET"
    assert captured["url"] == "http://127.0.0.1:8100/static/storyboard/s1.png"


def test_drama_proxy_forwards_script_generate_async(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.setenv("ST_DRAMA_API_URL", "http://127.0.0.1:8100")
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, content=None, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["content"] = content
            return _FakeResponse(
                422,
                b'{"detail":"request validation failed"}',
                {"content-type": "application/json"},
            )

    monkeypatch.setattr(drama_proxy.httpx, "AsyncClient", _Client)
    client = TestClient(_app())
    response = client.post("/api/drama/script/generate_async", json={})
    assert response.status_code == 422
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8100/api/drama/script/generate_async"


def test_drama_proxy_forwards_video_generate_async(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.setenv("ST_DRAMA_API_URL", "http://127.0.0.1:8100")
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, content=None, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["content"] = content
            return _FakeResponse(
                422,
                b'{"detail":"request validation failed"}',
                {"content-type": "application/json"},
            )

    monkeypatch.setattr(drama_proxy.httpx, "AsyncClient", _Client)
    client = TestClient(_app())
    response = client.post("/api/drama/video/generate_async", json={})
    assert response.status_code == 422
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8100/api/drama/video/generate_async"


def test_drama_proxy_forwards_voice_and_edit_generate_async(monkeypatch) -> None:
    from novelvideo.api.routes import drama_proxy

    monkeypatch.setenv("ST_DRAMA_API_URL", "http://127.0.0.1:8100")
    seen: list[str] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, content=None, headers=None):
            seen.append(url)
            return _FakeResponse(
                422,
                b'{"detail":"request validation failed"}',
                {"content-type": "application/json"},
            )

    monkeypatch.setattr(drama_proxy.httpx, "AsyncClient", _Client)
    client = TestClient(_app())
    assert client.post("/api/drama/voice/generate_async", json={}).status_code == 422
    assert client.post("/api/drama/edit/generate_async", json={}).status_code == 422
    assert seen == [
        "http://127.0.0.1:8100/api/drama/voice/generate_async",
        "http://127.0.0.1:8100/api/drama/edit/generate_async",
    ]
