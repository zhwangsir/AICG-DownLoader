"""NAS 模型库浏览服务（M27）。

扫描 core 挂载的 NAS 模型根目录（settings.nas_model_roots），产出统一模型条目：
- name / rel_path / root（来源根）/ type（子目录即 ComfyUI 模型类别）/ size / mtime / nsfw
- TTL 缓存（nas_library_cache_ttl），refresh 强制重扫
- NSFW 标记：文件名关键词（nsfw_keywords，小写子串）或精确名单（nsfw_exact_names）
- root 目录缺失/不可读不阻断（记 warning，按空目录处理）
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 默认落盘库（下载服务写入的 root）：主模型库
DEFAULT_DOWNLOAD_ROOT = "/mnt/toiv-nas/Windows/ComfyUI/ComfyUIModel/models"


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def model_roots() -> list[Path]:
    return [Path(p) for p in _split_csv(settings.nas_model_roots)]


def model_extensions() -> set[str]:
    return {e.lower() for e in _split_csv(settings.model_file_extensions)}


def nsfw_keywords() -> list[str]:
    return [k.lower() for k in _split_csv(settings.nsfw_keywords)]


def nsfw_exact_names() -> set[str]:
    return {n.lower() for n in _split_csv(settings.nsfw_exact_names)}


def is_nsfw_name(filename: str) -> bool:
    """按文件名判定 NSFW（不含扩展名做精确名单匹配；小写子串做关键词匹配）。"""
    lower = filename.lower()
    stem = lower.rsplit(".", 1)[0]
    if stem in nsfw_exact_names():
        return True
    return any(k in lower for k in nsfw_keywords())


class NasLibraryService:
    """NAS 模型库扫描与缓存。"""

    def __init__(self):
        self._cache: list[dict[str, Any]] | None = None
        self._cache_at: float = 0.0
        self._lock = threading.Lock()

    def _scan_root(self, root: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        exts = model_extensions()
        if not root.is_dir():
            logger.warning("模型库根目录不可读: %s（按空处理）", root)
            return entries
        for type_dir in sorted(root.iterdir()):
            if not type_dir.is_dir() or type_dir.name.startswith((".", "#")):
                continue
            for f in sorted(type_dir.rglob("*")):
                if not f.is_file() or f.suffix.lower() not in exts:
                    continue
                try:
                    st = f.stat()
                except OSError as e:
                    logger.warning("stat 失败 %s: %s（跳过）", f, e)
                    continue
                entries.append(
                    {
                        "name": f.name,
                        "rel_path": str(f.relative_to(root)),
                        "root": root.name,  # 主库=models / ToIV 专用=comfyui-models
                        "type": type_dir.name,
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                        "nsfw": is_nsfw_name(f.name),
                    }
                )
        return entries

    def _scan_all(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for root in model_roots():
            entries.extend(self._scan_root(root))
        entries.sort(key=lambda e: e["mtime"], reverse=True)
        return entries

    def list_models(
        self,
        type_filter: str | None = None,
        query: str | None = None,
        include_nsfw: bool = False,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """列出模型（带过滤）。返回 {items, total, types, scanned_at, cache_hit}。"""
        with self._lock:
            now = time.time()
            cache_valid = (
                self._cache is not None
                and (now - self._cache_at) < settings.nas_library_cache_ttl
            )
            if refresh or not cache_valid:
                self._cache = self._scan_all()
                self._cache_at = now
            items = self._cache or []
            cache_hit = cache_valid and not refresh

        types = sorted({e["type"] for e in items})
        if type_filter:
            items = [e for e in items if e["type"] == type_filter]
        if not include_nsfw:
            items = [e for e in items if not e["nsfw"]]
        if query:
            q = query.lower()
            items = [e for e in items if q in e["name"].lower() or q in e["rel_path"].lower()]

        return {
            "items": items,
            "total": len(items),
            "types": types,
            "scanned_at": self._cache_at,
            "cache_hit": cache_hit,
        }


nas_library_service = NasLibraryService()
