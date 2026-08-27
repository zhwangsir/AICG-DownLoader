"""NAS 模型库浏览服务（M27）。

扫描 settings.nas_model_roots（workstation CIFS: /mnt/toiv-nas/...）以及
lora_manifest.json 的 destination_dir 父目录（Mac: ~/NAS/Windows/ComfyUI/...）。
产出统一模型条目：name / rel_path / root / type / size / mtime / nsfw。
全部根目录不可读时在返回体里带 error，由路由层 503，避免静默空列表。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 默认落盘库（下载服务写入的 root）：主模型库（workstation）
DEFAULT_DOWNLOAD_ROOT = "/mnt/toiv-nas/Windows/ComfyUI/ComfyUIModel/models"
_LORA_MANIFEST = Path(__file__).resolve().parents[2] / "scripts" / "lora_manifest.json"


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _manifest_models_root() -> Path | None:
    """lora_manifest destination_dir 指向 .../models/loras，父目录即 ToIV ComfyUI 模型树。"""
    try:
        if not _LORA_MANIFEST.is_file():
            return None
        data = json.loads(_LORA_MANIFEST.read_text(encoding="utf-8").lstrip("\ufeff"))
        dest = (data.get("destination_dir") or "").strip()
        if not dest:
            return None
        path = Path(dest)
        return path.parent if path.name.lower() == "loras" else path
    except Exception as e:
        logger.warning("读取 lora_manifest destination_dir 失败: %s", e)
        return None


def model_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in _split_csv(settings.nas_model_roots):
        path = Path(raw)
        key = str(path)
        if key not in seen:
            seen.add(key)
            roots.append(path)
    extra = _manifest_models_root()
    if extra is not None:
        key = str(extra)
        if key not in seen:
            roots.append(extra)
    return roots


def describe_roots() -> list[dict[str, Any]]:
    info: list[dict[str, Any]] = []
    for root in model_roots():
        readable = False
        try:
            readable = root.is_dir() and os.access(root, os.R_OK)
        except OSError:
            readable = False
        info.append({"path": str(root), "readable": readable})
    return info


def roots_error_message(roots_info: list[dict[str, Any]] | None = None) -> str | None:
    info = roots_info if roots_info is not None else describe_roots()
    if any(r["readable"] for r in info):
        return None
    paths = ", ".join(r["path"] for r in info) or "(none)"
    return (
        "模型根目录不可读（本机未见 ToIV ComfyUI 模型树）。"
        f"已配置: {paths}。"
        "请把 NAS 挂到 lora_manifest destination_dir 的父目录"
        "（.../Windows/ComfyUI/ComfyUIModel/models）；不要把 SMB 密码写入仓库。"
    )


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
        """列出模型（带过滤）。返回 {items, total, types, scanned_at, cache_hit, roots, error}。"""
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

        roots_info = describe_roots()
        return {
            "items": items,
            "total": len(items),
            "types": types,
            "scanned_at": self._cache_at,
            "cache_hit": cache_hit,
            "roots": roots_info,
            "error": roots_error_message(roots_info),
        }


nas_library_service = NasLibraryService()
