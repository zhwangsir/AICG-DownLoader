"""模型注册表服务 —— 打通 ComfyUI 下载器与 AI 短剧工作台。

背景（任务3）：下载器（Rust，src/main.rs）把 Civitai/HF 模型下载到 ComfyUI 子目录，
并在下载器配置目录维护 models.json（filename/subdir/sha256/size/downloaded_at，无
trigger_words）；工作台 RAG（rag_service）用 scripts/lora_manifest.json 推荐 LoRA
（含 trigger_words/weight/style_key）。两者此前是孤岛——下载的模型不会自动出现在
工作台的推荐/参数里。

本服务把两份数据源按 filename 融合为统一的「模型注册表」，并扫描 ToIV ComfyUI
模型树（nas_model_roots + lora_manifest destination_dir）标注磁盘上真实存在的
checkpoint/LoRA：
- lora_manifest.json：权威 LoRA 元数据（trigger_words/weight/style_key/sha256）
- 下载器 models.json（可选）：已下载事实（filename/subdir/sha256/size/downloaded_at）
- 磁盘扫描：checkpoints/loras 实文件（Mac 未见 NAS 时 sources.error 明确失败）

下载器 models.json 默认路径（与 src/main.rs models_path() 一致）：
- macOS:  ~/Library/Application Support/comfy-downloader/models.json
- Linux:  ~/.config/comfy-downloader/models.json
- Windows: %APPDATA%/comfy-downloader/models.json
可用环境变量 DOWNLOADER_MODELS_JSON 显式覆盖。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 内置 LoRA 清单（RAG 风格库配套）
_LORA_MANIFEST = Path(__file__).resolve().parents[2] / "scripts" / "lora_manifest.json"


def _default_models_json() -> Path:
    """下载器 models.json 的平台默认路径（与 Rust 端 models_path() 对齐）。"""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "comfy-downloader"
            / "models.json"
        )
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "comfy-downloader" / "models.json"
    return Path.home() / ".config" / "comfy-downloader" / "models.json"


def _load_json(path: Path) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except Exception as e:  # 读取/解析失败不阻断，按空数据处理
        logger.warning("读取 %s 失败: %s", path, e)
    return None


class ModelRegistryService:
    """融合下载器与工作台的模型注册表。"""

    def get_registry(self, models_json_path: str | None = None) -> dict[str, Any]:
        """返回统一模型清单。

        Returns:
            {
              "loras": [ {filename, style_key, trigger_words, weight, sha256,
                          size_kb, downloaded, subdir, downloaded_at} ],
              "checkpoints": [ {filename, rel_path, size, root} ],
              "downloader_models": [ 原始 models.json 记录（含非 LoRA 模型）],
              "stats": {"manifest_loras": N, "downloaded_loras": M, ...},
              "sources": {"manifest": path, "models_json": path|None,
                          "model_roots": [...], "error": str|None},
            }
        """
        manifest = _load_json(_LORA_MANIFEST) or {}
        manifest_items = manifest.get("items", []) if isinstance(manifest, dict) else []

        mj_path = (
            Path(models_json_path)
            if models_json_path
            else Path(os.environ.get("DOWNLOADER_MODELS_JSON", str(_default_models_json())))
        )
        downloader_records = _load_json(mj_path)
        if not isinstance(downloader_records, list):
            downloader_records = []
        downloaded_by_filename = {
            r.get("filename", ""): r for r in downloader_records if r.get("filename")
        }

        from app.services.nas_library_service import nas_library_service

        lib = nas_library_service.list_models(include_nsfw=True)
        disk_by_filename: dict[str, dict[str, Any]] = {}
        checkpoints: list[dict[str, Any]] = []
        disk_lora_count = 0
        for entry in lib.get("items") or []:
            disk_by_filename.setdefault(entry["name"], entry)
            if entry.get("type") == "checkpoints":
                checkpoints.append(
                    {
                        "filename": entry["name"],
                        "rel_path": entry.get("rel_path", ""),
                        "size": entry.get("size", 0),
                        "root": entry.get("root", ""),
                    }
                )
            elif entry.get("type") == "loras":
                disk_lora_count += 1

        loras: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in manifest_items:
            filename = item.get("filename", "")
            dl = downloaded_by_filename.get(filename)
            on_disk = disk_by_filename.get(filename)
            seen.add(filename)
            if dl and dl.get("subdir"):
                subdir = dl["subdir"]
            elif on_disk and on_disk.get("type"):
                subdir = on_disk["type"]
            else:
                subdir = "loras"
            loras.append(
                {
                    "filename": filename,
                    "name": item.get("name", ""),
                    "style_key": item.get("style_key", ""),
                    "trigger_words": item.get("trigger_words", []),
                    "weight": item.get("weight", 0.7),
                    "sha256": item.get("sha256", ""),
                    "size_kb": item.get("size_kb", 0),
                    "downloaded": dl is not None or on_disk is not None,
                    "subdir": subdir,
                    "downloaded_at": (dl or {}).get("downloaded_at")
                    or (on_disk or {}).get("mtime"),
                }
            )

        for name, entry in disk_by_filename.items():
            if entry.get("type") != "loras" or name in seen:
                continue
            loras.append(
                {
                    "filename": name,
                    "name": name,
                    "style_key": "",
                    "trigger_words": [],
                    "weight": 0.7,
                    "sha256": "",
                    "size_kb": round((entry.get("size") or 0) / 1024, 2),
                    "downloaded": True,
                    "subdir": "loras",
                    "downloaded_at": entry.get("mtime"),
                }
            )

        downloaded_loras = sum(1 for lora in loras if lora["downloaded"])
        return {
            "loras": loras,
            "checkpoints": checkpoints,
            "downloader_models": downloader_records,
            "stats": {
                "manifest_loras": len(manifest_items),
                "downloaded_loras": downloaded_loras,
                "downloader_total_models": len(downloader_records),
                "disk_checkpoints": len(checkpoints),
                "disk_loras": disk_lora_count,
            },
            "sources": {
                "manifest": str(_LORA_MANIFEST),
                "models_json": str(mj_path) if mj_path.exists() else None,
                "model_roots": lib.get("roots") or [],
                "error": lib.get("error"),
            },
        }


model_registry_service = ModelRegistryService()
