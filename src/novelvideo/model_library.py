"""本地模型库：NAS 模型浏览 + Civitai 下载整合 + NSFW 门禁（移植自 AICG M27）。

三组能力：
- NasLibraryService：扫描模型根目录（DASHBOX_MODEL_ROOTS，CSV），产出
  name/rel_path/root/type/size/mtime/nsfw 条目，TTL 缓存 + 过滤/搜索
- ModelDownloadService：Civitai 搜索透传（civitai.red 镜像）+ 后台线程下载
  （.part → rename → SHA256 校验，chunk 级取消），落盘根与模型库同源即下即入库
- NSFW 门禁：R18 确认开关存 settings.db runtime_settings，未确认开启时
  库列表过滤 NSFW 条目、NSFW 下载请求拒绝

配置（环境变量，默认值按本机集群固化）：
- DASHBOX_MODEL_ROOTS：模型根目录 CSV（默认本机 NAS 挂载点两个库）
- DASHBOX_MODEL_LIBRARY_CACHE_TTL：扫描缓存秒数（默认 60）
- DASHBOX_CIVITAI_API_BASE：Civitai 镜像（默认 https://civitai.red/api）
- DASHBOX_HF_ENDPOINT：HF 镜像（默认 https://hf-mirror.com）
- DASHBOX_NSFW_KEYWORDS：NSFW 文件名关键词 CSV（小写子串匹配）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from novelvideo.model_gateway_settings import _read_all, _write_many

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

DEFAULT_MODEL_ROOTS = (
    "/private/tmp/nas_mnt/Windows/ComfyUI/ComfyUIModel/models,"
    "/private/tmp/nas_mnt/toiv/comfyui-models"
)
DEFAULT_NSFW_KEYWORDS = (
    "nsfw,porn,xxx,hentai,r18,erotic,nude,urpm,lustify,bigasse,sexgod,footjob"
    ",pussy,cumshot,blowjob,deepthroat,twerk,dr34ml4y,m4crom4sti4,hmnsfw,hmpussy"
    ",vagassist,slop,missionary,d0gg1e,c0wg1rl,bl0wj0b,m15510n4ry,pull0ut,b0dyshot"
)
MODEL_FILE_EXTENSIONS = {".safetensors", ".pt", ".pth", ".ckpt", ".bin", ".onnx"}
DOWNLOAD_SUBDIR_WHITELIST = {
    "checkpoints",
    "loras",
    "vae",
    "clip",
    "clip_vision",
    "controlnet",
    "diffusion_models",
    "text_encoders",
    "upscale_models",
    "embeddings",
    "ipadapter",
    "unet",
}
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_TIMEOUT = 30.0

_K_NSFW_ENABLED = "model_library.nsfw_enabled"
_K_NSFW_MARKS = "model_library.nsfw_marks"


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def model_roots() -> list[Path]:
    raw = os.environ.get("DASHBOX_MODEL_ROOTS", DEFAULT_MODEL_ROOTS)
    return [Path(p) for p in _split_csv(raw)]


def cache_ttl() -> float:
    return float(os.environ.get("DASHBOX_MODEL_LIBRARY_CACHE_TTL", "60"))


def civitai_api_base() -> str:
    return os.environ.get("DASHBOX_CIVITAI_API_BASE", "https://civitai.red/api")


def hf_endpoint() -> str:
    return os.environ.get("DASHBOX_HF_ENDPOINT", "https://hf-mirror.com")


def nsfw_keywords() -> list[str]:
    raw = os.environ.get("DASHBOX_NSFW_KEYWORDS", DEFAULT_NSFW_KEYWORDS)
    return [k.lower() for k in _split_csv(raw)]


def is_nsfw_name(filename: str) -> bool:
    """按文件名判定 NSFW（小写子串关键词匹配）。"""
    lower = filename.lower()
    return any(k in lower for k in nsfw_keywords())


# ---------------------------------------------------------------------------
# NSFW 手动标记（settings.db 覆盖表：rel_path → true/false，优先级高于关键词）
# ---------------------------------------------------------------------------


def get_nsfw_marks() -> dict[str, bool]:
    """读取手动标记表 {rel_path: bool}（容忍损坏数据）。"""
    raw = _read_all().get(_K_NSFW_MARKS, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("NSFW 手动标记表损坏，按空处理")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): bool(v) for k, v in data.items()}


def set_nsfw_mark(rel_path: str, nsfw: bool | None) -> dict[str, Any]:
    """设置/清除单条手动标记；nsfw=None 表示清除覆盖（回退关键词判定）。"""
    rel_path = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel_path or ".." in rel_path.split("/"):
        raise DownloadServiceError(f"非法 rel_path: {rel_path!r}")
    marks = get_nsfw_marks()
    if nsfw is None:
        marks.pop(rel_path, None)
    else:
        marks[rel_path] = bool(nsfw)
    _write_many({_K_NSFW_MARKS: json.dumps(marks, ensure_ascii=False, sort_keys=True)})
    logger.info("NSFW 手动标记 %s → %s", rel_path, nsfw)
    # 标记变化影响扫描结果，立即失效 TTL 缓存
    nas_library_service.invalidate()
    return {"marks": marks, "count": len(marks)}


def is_nsfw_entry(filename: str, rel_path: str, marks: dict[str, bool] | None = None) -> bool:
    """条目级 NSFW 判定：手动标记优先，缺省回退文件名关键词。"""
    if marks is None:
        marks = get_nsfw_marks()
    normalized = rel_path.replace("\\", "/").lstrip("/")
    if normalized in marks:
        return marks[normalized]
    return is_nsfw_name(filename)


# ---------------------------------------------------------------------------
# NSFW 门禁（R18 确认开关，存 settings.db runtime_settings）
# ---------------------------------------------------------------------------


def nsfw_status() -> dict[str, Any]:
    data = _read_all()
    return {"nsfw_enabled": data.get(_K_NSFW_ENABLED) == "1"}


_nsfw_lock = threading.Lock()


def set_nsfw(enabled: bool) -> dict[str, Any]:
    """R18 确认后置位/复位 NSFW 开关（前端已做 R18 确认提示，后端不再校验 PIN）。"""
    with _nsfw_lock:
        _write_many({_K_NSFW_ENABLED: "1" if enabled else "0"})
        logger.info("NSFW 状态切换 → %s", enabled)
        return nsfw_status()


# ---------------------------------------------------------------------------
# NAS 模型库扫描
# ---------------------------------------------------------------------------


class NasLibraryService:
    """模型根目录扫描与 TTL 缓存。"""

    def __init__(self):
        self._cache: list[dict[str, Any]] | None = None
        self._cache_at: float = 0.0
        self._lock = threading.Lock()

    def _scan_root(self, root: Path, marks: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not root.is_dir():
            logger.warning("模型库根目录不可读: %s（按空处理）", root)
            return entries
        if marks is None:
            marks = get_nsfw_marks()
        for type_dir in sorted(root.iterdir()):
            if not type_dir.is_dir() or type_dir.name.startswith((".", "#")):
                continue
            for f in sorted(type_dir.rglob("*")):
                if not f.is_file() or f.suffix.lower() not in MODEL_FILE_EXTENSIONS:
                    continue
                try:
                    st = f.stat()
                except OSError as e:
                    logger.warning("stat 失败 %s: %s（跳过）", f, e)
                    continue
                rel_path = str(f.relative_to(root))
                entries.append(
                    {
                        "name": f.name,
                        "rel_path": rel_path,
                        "root": root.name,
                        "type": type_dir.name,
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                        "nsfw": is_nsfw_entry(f.name, rel_path, marks),
                    }
                )
        return entries

    def _scan_all(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        marks = get_nsfw_marks()
        for root in model_roots():
            entries.extend(self._scan_root(root, marks))
        entries.sort(key=lambda e: e["mtime"], reverse=True)
        return entries

    def invalidate(self) -> None:
        """失效 TTL 缓存（手动标记/外部落盘后调用）。"""
        with self._lock:
            self._cache = None
            self._cache_at = 0.0

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
            cache_valid = self._cache is not None and (now - self._cache_at) < cache_ttl()
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


# ---------------------------------------------------------------------------
# 生成前预检：workflow JSON 模型引用提取与比对
# ---------------------------------------------------------------------------

# loader 节点 class_type → [(携带文件名的 input 字段, 应所在的子目录集合)]
# 子目录与 ComfyUI models/ 目录类别一致；比对时文件名命中任一候选子目录即算在位。
LOADER_FILE_FIELDS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "CheckpointLoaderSimple": [("ckpt_name", ("checkpoints",))],
    "LoraLoader": [("lora_name", ("loras",))],
    "LoraLoaderModelOnly": [("lora_name", ("loras",))],
    "VAELoader": [("vae_name", ("vae",))],
    "LTXVAudioVAELoader": [("vae_name", ("vae",))],
    "CLIPLoader": [("clip_name", ("clip",))],
    "DualCLIPLoader": [("clip_name", ("clip",))],
    "CLIPVisionLoader": [("clip_name", ("clip_vision",))],
    "UNETLoader": [("unet_name", ("unet", "diffusion_models"))],
    "LTXAVTextEncoderLoader": [
        ("text_encoder", ("text_encoders",)),
        ("ckpt_name", ("checkpoints",)),
    ],
    "IPAdapterModelLoader": [("ipadapter_file", ("ipadapter",))],
    "UpscaleModelLoader": [("model_name", ("upscale_models",))],
    "LatentUpscaleModelLoader": [("model_name", ("upscale_models",))],
    "ControlNetLoader": [("control_net_name", ("controlnet",))],
}


def extract_model_refs(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """从 ComfyUI API Format workflow 提取模型文件引用。"""
    refs: list[dict[str, Any]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for field, type_candidates in LOADER_FILE_FIELDS.get(str(class_type), []):
            value = inputs.get(field)
            if isinstance(value, str) and value.strip():
                refs.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "field": field,
                        "filename": value.strip(),
                        "expected_types": list(type_candidates),
                    }
                )
    return refs


def preflight_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    """比对 workflow 模型引用与模型库（含 NSFW，检查的是磁盘事实不过滤）。

    返回 {refs: [...], missing: [...], total, missing_count}；
    每个 ref 附加 present（候选子目录命中）/ present_anywhere（库中任意位置命中）。
    """
    refs = extract_model_refs(workflow)
    lib = nas_library_service.list_models(include_nsfw=True)
    entries = lib["items"]
    by_name_type = {(e["name"], e["type"]) for e in entries}
    by_name = {e["name"] for e in entries}

    missing: list[dict[str, Any]] = []
    for ref in refs:
        filename = ref["filename"]
        ref["present"] = any(
            (filename, t) in by_name_type for t in ref["expected_types"]
        )
        ref["present_anywhere"] = filename in by_name
        if not ref["present"]:
            missing.append(ref)
    return {
        "refs": refs,
        "missing": missing,
        "total": len(refs),
        "missing_count": len(missing),
        "checked_at": lib["scanned_at"],
    }


# ---------------------------------------------------------------------------
# Civitai 搜索 + 后台下载
# ---------------------------------------------------------------------------


class DownloadServiceError(Exception):
    """下载服务错误（NSFW 闸门/参数非法/网络失败/校验失败）。"""


def sanitize_filename(filename: str) -> str:
    """文件名安全化：去路径分隔与父目录引用，保留原名主体。"""
    name = filename.replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", ".."):
        raise DownloadServiceError(f"非法文件名: {filename!r}")
    return name


def resolve_download_root() -> Path:
    """下载落盘根目录（取模型根第一项，与模型库同源，完成即入库）。"""
    roots = model_roots()
    if not roots:
        raise DownloadServiceError("未配置模型库根目录（DASHBOX_MODEL_ROOTS）")
    return roots[0]


def apply_hf_mirror(url: str) -> str:
    """huggingface.co 链接改走 hf-mirror。"""
    if "huggingface.co" in url:
        return url.replace("https://huggingface.co", hf_endpoint())
    return url


class ModelDownloadService:
    """Civitai 搜索 + 后台下载任务管理（进程内注册表）。"""

    def __init__(self):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            timeout=httpx.Timeout(DOWNLOAD_TIMEOUT, read=DOWNLOAD_TIMEOUT),
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
        params["nsfw"] = "true" if include_nsfw else "false"

        resp = self._http.get(f"{civitai_api_base()}/v1/models", params=params)
        resp.raise_for_status()
        data = resp.json()

        items = []
        for m in data.get("items", []):
            versions = []
            for v in m.get("modelVersions", [])[:3]:
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
        if subdir not in DOWNLOAD_SUBDIR_WHITELIST:
            raise DownloadServiceError(f"子目录不在白名单: {subdir}")
        filename = sanitize_filename(filename)
        if (nsfw or is_nsfw_name(filename)) and not nsfw_status()["nsfw_enabled"]:
            raise DownloadServiceError("NSFW 内容未开启：请先在模型库面板确认 R18 提示")

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
                    for chunk in resp.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                        if self.get_task(task_id)["cancel_requested"]:
                            raise DownloadServiceError("已取消")
                        f.write(chunk)
                        downloaded += len(chunk)
                        elapsed = max(time.time() - started, 0.01)
                        self._update(task_id, downloaded=downloaded, speed_bps=downloaded / elapsed)

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
