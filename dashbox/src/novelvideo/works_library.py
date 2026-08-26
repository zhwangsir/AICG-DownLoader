"""作品库：扫描 works/ 目录的样本作品（视频矩阵产物），供前端画廊展示。

数据源形态（由 scripts/sample_matrix_driver.py 产出）：
    works/<id>/work.json   元数据（title/category/duration/engine/features/nsfw/desc…）
    works/<id>/video.mp4   成片
    works/<id>/cover.png   封面（首帧）

配置：
- DASHBOX_WORKS_ROOT：作品根目录（默认 <repo>/works）
- DASHBOX_WORKS_CACHE_TTL：扫描缓存秒数（默认 10，生成期间快速刷新）
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from novelvideo.model_library import nsfw_status

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_WORKS_ROOT = str(_REPO_ROOT / "works")
WORK_CATEGORIES = {"anime", "real", "3d"}
_CACHE_TTL_DEFAULT = 10.0

_lock = threading.Lock()
_cache: list[dict[str, Any]] | None = None
_cache_at: float = 0.0


def works_root() -> Path:
    return Path(os.environ.get("DASHBOX_WORKS_ROOT", DEFAULT_WORKS_ROOT))


def _cache_ttl() -> float:
    return float(os.environ.get("DASHBOX_WORKS_CACHE_TTL", str(_CACHE_TTL_DEFAULT)))


def invalidate_cache() -> None:
    """清缓存（新作品落盘后由调用方触发）。"""
    global _cache, _cache_at
    with _lock:
        _cache = None
        _cache_at = 0.0


def _load_work_meta(d: Path) -> dict[str, Any] | None:
    meta_file = d / "work.json"
    if not meta_file.is_file():
        return None
    try:
        meta = json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("作品元数据损坏 %s: %s", meta_file, e)
        return None
    if not isinstance(meta, dict) or not meta.get("id") or not (d / str(meta.get("video", ""))).is_file():
        return None
    meta.setdefault("title", d.name)
    meta.setdefault("category", "real")
    meta.setdefault("duration", "")
    meta.setdefault("engine", "")
    meta.setdefault("features", [])
    meta.setdefault("nsfw", False)
    meta.setdefault("desc", "")
    meta["has_cover"] = (d / str(meta.get("cover", ""))).is_file()
    meta["sizeBytes"] = (d / str(meta["video"])).stat().st_size
    return meta


def scan_works() -> list[dict[str, Any]]:
    """扫描作品根目录，返回按 createdAt 倒序的作品列表（含 NSFW 条目，过滤在路由层）。"""
    global _cache, _cache_at
    with _lock:
        if _cache is not None and time.time() - _cache_at < _cache_ttl():
            return _cache
    root = works_root()
    items: list[dict[str, Any]] = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            meta = _load_work_meta(d)
            if meta is not None:
                items.append(meta)
    items.sort(key=lambda x: str(x.get("createdAt", "")), reverse=True)
    with _lock:
        _cache = items
        _cache_at = time.time()
    return items


def list_works(
    *,
    category: str = "",
    feature: str = "",
    q: str = "",
    include_nsfw: bool | None = None,
) -> list[dict[str, Any]]:
    """过滤列表：赛道 / 特性标签 / 关键词 / NSFW 开关。

    include_nsfw=None 时跟随 settings.db 的 R18 确认状态。
    """
    if include_nsfw is None:
        include_nsfw = bool(nsfw_status().get("nsfw_enabled"))
    out: list[dict[str, Any]] = []
    for w in scan_works():
        if w["nsfw"] and not include_nsfw:
            continue
        if category and w["category"] != category:
            continue
        if feature and feature not in w.get("features", []):
            continue
        if q:
            hay = f"{w['title']} {w.get('titleEn', '')} {w['desc']} {w['engine']}".lower()
            if q.lower() not in hay:
                continue
        out.append(w)
    return out


def get_work(work_id: str) -> dict[str, Any] | None:
    """按 id 取单条作品（不校验 NSFW，媒体访问校验在路由层）。"""
    for w in scan_works():
        if w["id"] == work_id:
            return w
    return None


def work_media_path(work_id: str, kind: str) -> Path | None:
    """返回作品媒体文件绝对路径；kind ∈ video/cover。防路径穿越：id 必须为目录名。"""
    if not work_id or "/" in work_id or "\\" in work_id or work_id.startswith("."):
        return None
    d = works_root() / work_id
    if not d.is_dir() or not d.name == work_id:
        return None
    meta = _load_work_meta(d)
    if meta is None:
        return None
    key = "video" if kind == "video" else "cover" if kind == "cover" else None
    if key is None:
        return None
    p = d / str(meta.get(key, ""))
    if not p.is_file() or p.parent != d:
        return None
    return p
