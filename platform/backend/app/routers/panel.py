"""Fused-panel status: backend + downloader config/models.json + DashBox URLs.

Does not spawn the Rust desktop helper. Launch/status/link only.
"""
from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter

from app.config import settings
from app.services.model_registry_service import _default_models_json
from app.services.nas_library_service import describe_roots, roots_error_message

router = APIRouter(prefix="/api/panel", tags=["panel"])

DASHBOX_WEB = "http://127.0.0.1:%s" % os.environ.get("ST_WEB_PORT", "8080")
DASHBOX_API = "http://127.0.0.1:%s" % os.environ.get("ST_API_PORT", "8780")
DRAMA_API = os.environ.get("DRAMA_API_BASE", "http://127.0.0.1:8100").rstrip("/")
# Cheap localhost listen probe. Closed ports must not stall the API.
_PROBE_TIMEOUT = 0.3
_PROBE_HOST = "127.0.0.1"


def _resolved_config_path() -> Path:
    path = Path(settings.downloader_config_path)
    if not path.is_absolute():
        # app/routers/panel.py -> repo root is parents[4]
        path = Path(__file__).resolve().parents[4] / path
    return path


def _resolved_models_json() -> Path:
    override = os.environ.get("DOWNLOADER_MODELS_JSON", "").strip()
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[4] / path
        return path
    return Path(_default_models_json())


def _readable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _probe_listening(url: str) -> bool:
    """TCP connect or HTTP GET; never raises; capped at ~300ms."""
    parsed = urlparse(url)
    host = parsed.hostname or _PROBE_HOST
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))

    async def tcp() -> bool:
        return await asyncio.to_thread(_tcp_open, host, port, _PROBE_TIMEOUT)

    async def http() -> bool:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_PROBE_TIMEOUT, connect=_PROBE_TIMEOUT),
            trust_env=False,
        ) as client:
            await client.get(url)
        return True

    async def one(fn) -> bool:
        try:
            return bool(await fn())
        except Exception:
            return False

    try:
        tcp_ok, http_ok = await asyncio.wait_for(
            asyncio.gather(one(tcp), one(http)),
            timeout=_PROBE_TIMEOUT + 0.05,
        )
        return bool(tcp_ok or http_ok)
    except Exception:
        return False


@router.get("/status")
async def panel_status() -> dict:
    """Tiny fused-product status. No engine scrape, no Rust spawn, no docker."""
    cfg = _resolved_config_path()
    models = _resolved_models_json()
    web_listening, api_listening = await asyncio.gather(
        _probe_listening(DASHBOX_WEB),
        _probe_listening(DASHBOX_API),
    )
    nas_roots = describe_roots()
    return {
        "backend": "ok",
        "product": "DashBox",
        "downloader_config_path": str(cfg),
        "downloader_config_readable": _readable(cfg),
        "models_json_path": str(models),
        "models_json_readable": _readable(models),
        "nas_model_roots": nas_roots,
        "nas_model_roots_error": roots_error_message(nas_roots),
        "drama_backend": {
            "api": DRAMA_API,
            "note": "platform/ FastAPI short-drama module. Main UI is DashBox :8080.",
        },
        "dashbox": {
            "web": DASHBOX_WEB,
            "api": DASHBOX_API,
            "web_listening": bool(web_listening),
            "api_listening": bool(api_listening),
            "note": "DashBox CE (DramaClaw/SuperTale upstream, ELv2). Main UI. ./start-dashbox.sh. Not rebranded.",
        },
    }
