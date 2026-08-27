"""Reverse-proxy /api/drama/* to the platform short-drama FastAPI.

DashBox web (:8080) already nginx-proxies /api/ to this API (:8780). Browser
stays same-origin; this module forwards /api/drama/* to ST_DRAMA_API_URL
(default http://127.0.0.1:8100). Docker Compose overrides the URL to
http://host.docker.internal:8100 so the api container can reach the host.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("novelvideo.api.drama_proxy")

router = APIRouter(prefix="/api/drama", tags=["drama-proxy"])

DEFAULT_DRAMA_API_URL = "http://127.0.0.1:8100"
_TIMEOUT = httpx.Timeout(600.0, connect=5.0)
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-encoding",
    "content-length",
}
_FORWARD_HEADER_ALLOW = {
    "accept",
    "accept-language",
    "content-type",
    "x-nsfw",
    "authorization",
}


def drama_api_base() -> str:
    raw = (os.environ.get("ST_DRAMA_API_URL") or DEFAULT_DRAMA_API_URL).strip()
    return (raw or DEFAULT_DRAMA_API_URL).rstrip("/")


def _sanitize_subpath(path: str) -> str | None:
    cleaned = (path or "").strip().lstrip("/")
    if not cleaned:
        return ""
    if ".." in cleaned or "://" in cleaned or "\\" in cleaned:
        return None
    if any(part == ".." or part == "" for part in cleaned.split("/")):
        return None
    return cleaned


def _target_url(subpath: str, query: str) -> str:
    # SPA cannot fetch :8100 /static/* (CSP connect/img-src self). Rewrite
    # /api/drama/static/... onto the platform static mount.
    if subpath.startswith("static/"):
        url = f"{drama_api_base()}/{subpath}"
    else:
        url = f"{drama_api_base()}/api/drama/{subpath}" if subpath else f"{drama_api_base()}/api/drama"
    if query:
        url = f"{url}?{query}"
    return url


def _forward_headers(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        low = key.lower()
        if low in _HOP_BY_HOP:
            continue
        if low in _FORWARD_HEADER_ALLOW:
            out[key] = value
    return out


def _response_headers(upstream: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in upstream.items():
        if key.lower() in _HOP_BY_HOP:
            continue
        out[key] = value
    return out


async def _proxy(request: Request, path: str = "") -> Response:
    subpath = _sanitize_subpath(path)
    if subpath is None:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "invalid drama path"},
        )
    url = _target_url(subpath, request.url.query)
    try:
        body = await request.body()
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            upstream = await client.request(
                request.method,
                url,
                content=body,
                headers=_forward_headers(request),
            )
    except httpx.RequestError as exc:
        logger.warning("drama proxy upstream error method=%s url=%s err=%s", request.method, url, exc)
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "drama backend unavailable",
                "data": {"upstream": drama_api_base(), "detail": str(exc)},
            },
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers),
    )


@router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@router.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_drama_root(request: Request) -> Response:
    return await _proxy(request, "")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_drama_path(request: Request, path: str) -> Response:
    return await _proxy(request, path)
