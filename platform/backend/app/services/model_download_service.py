"""模型下载整合服务（M27）：Civitai 搜索 + 后台下载到 NAS + SHA256 校验。

与 Rust 下载器（src/main.rs）同策略：
- Civitai 走 civitai.red 镜像（core 实测 civitai.com 不可达）
- HuggingFace 走 hf-mirror.com（huggingface.co 不可达）
- SHA256 校验（Civitai files[].hashes.SHA256 / 调用方显式传入）

下载执行：线程池（asyncio.to_thread）流式写 .part → 完成后 rename → 可选 sha256 校验，
失败删除半成品。任务注册表进程内存维护（单 uvicorn 进程），支持取消（chunk 级检查）。
NSFW 闸门：模型带 nsfw 标记且设置未开启 → 拒绝（403 由路由层转换）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.nas_library_service import DEFAULT_DOWNLOAD_ROOT, is_nsfw_name
from app.services.settings_service import settings_service

logger = logging.getLogger(__name__)


class DownloadServiceError(Exception):
    """下载服务错误（NSFW 闸门/参数非法/网络失败/校验失败）。"""


def _subdir_whitelist() -> set[str]:
    return {s.strip() for s in settings.download_subdir_whitelist.split(",") if s.strip()}


def sanitize_filename(filename: str) -> str:
    """文件名安全化：去路径分隔与父目录引用，保留原名主体。"""
    name = filename.replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", ".."):
        raise DownloadServiceError(f"非法文件名: {filename!r}")
    return name


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_writable_dir(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def _is_readable_dir(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.R_OK)
    except OSError:
        return False


def resolve_download_root() -> Path:
    """下载落盘的 NAS 根目录：取 nas_model_roots 中第一个可用路径。

    优先存在且可写的目录；否则第一个存在且可读的目录；再否则第一个存在的目录。
    全部不可用时保底旧行为（列表第一项，或 DEFAULT_DOWNLOAD_ROOT）。
    不硬编码本机家目录，路径一律来自 settings.nas_model_roots / 环境变量 NAS_MODEL_ROOTS。
    """
    roots = [Path(p.strip()) for p in settings.nas_model_roots.split(",") if p.strip()]
    if not roots:
        return Path(DEFAULT_DOWNLOAD_ROOT)
    for root in roots:
        if _is_writable_dir(root):
            return root
    for root in roots:
        if _is_readable_dir(root):
            return root
    for root in roots:
        if _is_dir(root):
            return root
    return roots[0]


def apply_hf_mirror(url: str) -> str:
    """huggingface.co 链接改走 hf-mirror（与 Rust 端 apply_mirror 一致）。"""
    if "huggingface.co" in url:
        return url.replace("https://huggingface.co", settings.hf_endpoint)
    return url


class ModelDownloadService:
    """Civitai 搜索 + 后台下载任务管理。"""

    def __init__(self):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            timeout=httpx.Timeout(settings.download_timeout, read=settings.download_timeout),
            trust_env=False,
            follow_redirects=True,
        )

    # ---------- Civitai 搜索 ----------

    def civitai_search(
        self,
        query: str = "",
        model_type: str | None = None,
        limit: int = 20,
        include_nsfw: bool = False,
    ) -> dict[str, Any]:
        """搜索 Civitai 模型并规整为统一结构。"""
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if query:
            params["query"] = query
        if model_type:
            params["types"] = model_type
        # Civitai API: nsfw=false 时只返回全年龄内容；true 返回全部
        params["nsfw"] = "true" if include_nsfw else "false"

        resp = self._http.get(f"{settings.civitai_api_base}/v1/models", params=params)
        resp.raise_for_status()
        data = resp.json()

        items = []
        for m in data.get("items", []):
            versions = []
            for v in m.get("modelVersions", [])[:3]:  # 只取前 3 个版本
                files = []
                for f in v.get("files", []):
                    if not isinstance(f.get("sizeKB"), (int, float)):
                        continue
                    files.append(
                        {
                            "name": f.get("name", ""),
                            "size_kb": f["sizeKB"],
                            "download_url": f.get("downloadUrl", ""),
                            "sha256": (f.get("hashes") or {}).get("SHA256"),
                            "primary": bool(f.get("primary")),
                        }
                    )
                if files:
                    versions.append({"id": v.get("id"), "name": v.get("name", ""), "files": files})
            if not versions:
                continue
            items.append(
                {
                    "id": m.get("id"),
                    "name": m.get("name", ""),
                    "type": m.get("type", ""),
                    "nsfw": bool(m.get("nsfw")),
                    "versions": versions,
                }
            )
        return {"items": items, "total": len(items)}

    # ---------- 任务注册表 ----------

    def _update(self, task_id: str, **patch: Any) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(patch)

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda t: t["created_at"], reverse=True)
        return tasks

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task["status"] not in ("pending", "running"):
                return False
            task["cancel_requested"] = True
            if task["status"] == "pending":
                task["status"] = "canceled"
            return True

    # ---------- 下载 ----------

    def start_download(
        self,
        download_url: str,
        filename: str,
        subdir: str,
        sha256: str | None = None,
        nsfw: bool = False,
    ) -> dict[str, Any]:
        """登记并启动后台下载任务（线程执行）。"""
        if subdir not in _subdir_whitelist():
            raise DownloadServiceError(f"子目录不在白名单: {subdir}")
        filename = sanitize_filename(filename)
        # NSFW 闸门：显式标记或文件名命中，且设置未开启 → 拒绝
        if (nsfw or is_nsfw_name(filename)) and not settings_service.nsfw_status()["nsfw_enabled"]:
            raise DownloadServiceError("NSFW 内容未开启：请先在模型库面板输入 PIN 解锁")

        task_id = uuid.uuid4().hex[:12]
        dest = resolve_download_root() / subdir / filename
        task = {
            "task_id": task_id,
            "filename": filename,
            "subdir": subdir,
            "dest": str(dest),
            "source_url": download_url,
            "sha256": sha256,
            "nsfw": bool(nsfw or is_nsfw_name(filename)),
            "status": "pending",
            "downloaded": 0,
            "total": 0,
            "speed_bps": 0.0,
            "error": None,
            "cancel_requested": False,
            "created_at": time.time(),
        }
        with self._lock:
            self._tasks[task_id] = task

        thread = threading.Thread(
            target=self._run_download, args=(task_id, download_url, dest), daemon=True
        )
        thread.start()
        return task

    def _run_download(self, task_id: str, url: str, dest: Path) -> None:
        """流式下载 → .part → rename → sha256 校验（后台线程）。"""
        url = apply_hf_mirror(url)
        part = dest.with_suffix(dest.suffix + ".part")
        started = time.time()
        try:
            self._update(task_id, status="running")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with self._http.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length") or 0)
                self._update(task_id, total=total)
                downloaded = 0
                with open(part, "wb") as f:
                    for chunk in resp.iter_bytes(settings.download_chunk_size):
                        if self.get_task(task_id)["cancel_requested"]:
                            raise DownloadServiceError("已取消")
                        f.write(chunk)
                        downloaded += len(chunk)
                        elapsed = max(time.time() - started, 0.01)
                        self._update(
                            task_id, downloaded=downloaded, speed_bps=downloaded / elapsed
                        )

            task = self.get_task(task_id)
            expected = task["sha256"]
            if expected:
                digest = hashlib.sha256(part.read_bytes()).hexdigest()
                if digest.lower() != expected.lower():
                    part.unlink(missing_ok=True)
                    raise DownloadServiceError(
                        f"SHA256 校验失败: 期望 {expected[:12]}… 实际 {digest[:12]}…"
                    )
            part.replace(dest)
            self._update(task_id, status="done", finished_at=time.time())
            logger.info("模型下载完成: %s (%d bytes)", dest, self.get_task(task_id)["downloaded"])
        except Exception as e:
            part.unlink(missing_ok=True)
            status = "canceled" if str(e) == "已取消" else "error"
            self._update(task_id, status=status, error=str(e), finished_at=time.time())
            logger.warning("模型下载失败 %s: %s", task_id, e)


model_download_service = ModelDownloadService()
