"""LatentSync 1.6 唇形同步服务 — FastAPI 包装层。

部署位置: workstation GPU3 (192.168.71.127:8289), 显存 ~18GB。
本服务在字节开源的 LatentSync 之上提供 HTTP 接口:

OpenAI 风格接口 (主):
  - POST /lip_sync            提交唇形同步任务 (multipart/form-data), 返回 task_id
  - POST /v1/lip_sync         同上 (兼容路径)
  - GET  /tasks/{task_id}     查询任务状态与结果
  - GET  /health              健康检查 (含模型就绪状态)
  - GET  /v1/models           返回模型列表

兼容接口 (供既有 LatentSyncService 客户端零改动使用, 复用同一任务存储):
  - POST /v1/video/upload             上传媒体, 返回 {"filename"}
  - POST /v1/lipsync/submit           提交任务, 返回 {"task_id"}
  - GET  /v1/lipsync/status/{id}      返回 {"status","progress","message"}
  - GET  /v1/lipsync/result/{id}      返回 {"video_url","duration_seconds"}

输入媒体: 上传文件或 URL 均可; 内部用 ffmpeg 将视频转 25fps、音频转 16kHz 单声道。
失败降级: 推理失败时复制原视频作为结果返回, 标记 degraded=true。
"""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import (
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ----------------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("latentsync-serve")

# ----------------------------------------------------------------------------
# 路径与 LatentSync 仓库定位
# ----------------------------------------------------------------------------
WORKSPACE = Path("/workspace")
IO_DIR = WORKSPACE / "io"                 # 上传/下载/结果根目录 (run.sh 挂载 /tmp/latentsync-io)
UPLOAD_DIR = IO_DIR / "uploads"           # 原始上传与下载的临时文件
OUTPUT_DIR = IO_DIR / "output"            # 结果视频 (供 /files/output/ 下载)
LATENTSYNC_ROOT = WORKSPACE / "LatentSync"  # 官方仓库 (Dockerfile 中 clone)

for d in (IO_DIR, UPLOAD_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# 将 LatentSync 仓库加入 import 路径并切换工作目录,
# 使 `from latentsync.predict import Predictor` 与仓库内的相对 config 路径可用
if str(LATENTSYNC_ROOT) not in sys.path:
    sys.path.insert(0, str(LATENTSYNC_ROOT))
try:
    os.chdir(LATENTSYNC_ROOT)
except OSError:
    pass

# ----------------------------------------------------------------------------
# Predictor 单例 (懒加载, 模型权重通过 HF/卷挂载, 不在镜像内下载)
# ----------------------------------------------------------------------------
_predictor: Any = None
_predictor_lock = threading.Lock()
_model_ready: bool = False
_model_error: Optional[str] = None
_predictor_supports_ref_image: bool = False


def _load_predictor() -> None:
    """加载 LatentSync Predictor (约 18GB 显存), 启动时后台线程调用。"""
    global _predictor, _model_ready, _model_error, _predictor_supports_ref_image
    with _predictor_lock:
        if _predictor is not None or _model_error is not None:
            return
        try:
            logger.info("开始加载 LatentSync Predictor ...")
            from latentsync.predict import Predictor  # 延迟 import, 避免依赖缺失时启动崩溃

            _predictor = Predictor()
            _predictor.setup()
            # 探测 predict() 是否支持 reference_image 形参 (不同版本签名不一致)
            try:
                sig = inspect.signature(_predictor.predict)
                params = sig.parameters
                _predictor_supports_ref_image = (
                    "reference_image" in params
                    or any(p.kind is p.VAR_KEYWORD for p in params.values())
                )
            except (TypeError, ValueError):
                _predictor_supports_ref_image = False
            _model_ready = True
            logger.info(
                "LatentSync Predictor 加载完成 (reference_image 支持=%s)",
                _predictor_supports_ref_image,
            )
        except Exception as exc:  # noqa: BLE001 - 启动期错误需全部捕获以便健康检查上报
            _model_error = str(exc)
            logger.exception("LatentSync Predictor 加载失败: %s", exc)


def get_predictor() -> Any:
    """返回已加载的 Predictor; 若仍在加载则阻塞至完成; 失败返回 None。"""
    global _model_error
    with _predictor_lock:
        if _predictor is not None:
            return _predictor
        if _model_error is not None:
            return None
    # 仍在加载: 等待加载线程释放锁后再次检查
    with _predictor_lock:
        return _predictor if _model_error is None else None


# ----------------------------------------------------------------------------
# 异步任务存储 (GPU 串行, 单 worker)
# ----------------------------------------------------------------------------
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Task:
    task_id: str
    base_url: str                                   # 创建任务时的服务外部基址, 用于拼结果 URL
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    message: str = ""
    error: Optional[str] = None
    video_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    degraded: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    temp_files: list[Path] = field(default_factory=list)  # 处理完成后清理的临时文件


_tasks: dict[str, Task] = {}
_tasks_lock = threading.Lock()
# GPU 一次只能跑一个推理 (~18GB 显存), 串行执行避免 OOM
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="latentsync-worker")


def _new_task(base_url: str) -> Task:
    task = Task(task_id=uuid.uuid4().hex, base_url=base_url.rstrip("/"))
    with _tasks_lock:
        _tasks[task.task_id] = task
    return task


def _get_task(task_id: str) -> Task:
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return task


def _result_url(base_url: str, filename: str) -> str:
    return f"{base_url}/files/output/{filename}"


# ----------------------------------------------------------------------------
# ffmpeg 工具: 预处理 / 时长探测 / 通用执行
# ----------------------------------------------------------------------------
def _run_cmd(cmd: list[str], timeout: int = 600) -> None:
    """运行外部命令, 失败抛 RuntimeError。"""
    logger.debug("exec: %s", " ".join(cmd))
    proc = subprocess.run(  # noqa: S603 - 命令由内部构造, 参数均为字面量
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"命令失败 ({proc.returncode}): {' '.join(cmd)}\n"
            f"stderr: {proc.stderr[-2000:]}"
        )


def _preprocess_video(src: Path) -> Path:
    """视频转 25fps、去音轨、yuv420p, 满足 LatentSync 输入要求。"""
    dst = UPLOAD_DIR / f"{src.stem}_25fps_{uuid.uuid4().hex[:6]}.mp4"
    _run_cmd([
        "ffmpeg", "-y", "-i", str(src),
        "-r", "25", "-an", "-vsync", "cfr",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        str(dst),
    ])
    return dst


def _preprocess_audio(src: Path) -> Path:
    """音频转 16000Hz 单声道 PCM wav, 满足 LatentSync 输入要求。"""
    dst = UPLOAD_DIR / f"{src.stem}_16k_{uuid.uuid4().hex[:6]}.wav"
    _run_cmd([
        "ffmpeg", "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(dst),
    ])
    return dst


def _probe_duration(path: Path) -> float:
    try:
        proc = subprocess.run(  # noqa: S603
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return float((proc.stdout or "").strip() or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _cleanup(files: list[Path]) -> None:
    for f in files:
        try:
            if f and f.exists():
                f.unlink()
        except OSError:
            pass


def _infer_ext(name: Optional[str], default: str) -> str:
    if name and "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        if ext.isalnum() and len(ext) <= 5:
            return ext
    return default


# ----------------------------------------------------------------------------
# 输入解析: 上传文件 / URL 下载
# ----------------------------------------------------------------------------
async def _save_upload(upload: UploadFile, prefix: str, default_ext: str) -> Path:
    ext = _infer_ext(upload.filename, default_ext)
    dst = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail=f"上传文件为空: {upload.filename}")
    dst.write_bytes(content)
    return dst


async def _download_url(url: str, prefix: str, default_ext: str) -> Path:
    ext = _infer_ext(url.split("?")[0].split("/")[-1] if "/" in url else None, default_ext)
    dst = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dst, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"下载失败 {url}: {exc}")
    return dst


async def _resolve_input(
    upload: Optional[UploadFile],
    url: Optional[str],
    prefix: str,
    default_ext: str,
    label: str,
) -> Path:
    """优先使用上传文件, 其次下载 URL, 都缺失则 400。"""
    if upload is not None and upload.filename:
        path = await _save_upload(upload, prefix, default_ext)
        logger.info("%s 上传文件: %s", label, path.name)
        return path
    if url:
        logger.info("%s 下载 URL: %s", label, url)
        return await _download_url(url, prefix, default_ext)
    raise HTTPException(status_code=400, detail=f"缺少 {label} (需提供文件或 *_url)")


# ----------------------------------------------------------------------------
# 推理执行 (在工作线程中运行, 阻塞 GPU)
# ----------------------------------------------------------------------------
def _call_predict(
    predictor: Any,
    video_path: Path,
    audio_path: Path,
    reference_image: Optional[Path],
    inference_steps: int,
    guidance_scale: float,
) -> Path:
    """按官方 predict.py 签名调用; reference_image 仅在版本支持时透传。"""
    if reference_image is not None and _predictor_supports_ref_image:
        result = predictor.predict(
            video_path=str(video_path),
            audio_path=str(audio_path),
            reference_image=str(reference_image),
            inference_steps=inference_steps,
            guidance_scale=guidance_scale,
        )
    else:
        result = predictor.predict(
            video_path=str(video_path),
            audio_path=str(audio_path),
            inference_steps=inference_steps,
            guidance_scale=guidance_scale,
        )
    # predict 返回值可能是 Path / str / None
    if result is None:
        raise RuntimeError("Predictor.predict 返回空结果")
    return Path(str(result))


def _run_inference(
    task_id: str,
    video_path: Path,
    audio_path: Path,
    reference_image: Optional[Path],
    inference_steps: int,
    guidance_scale: float,
) -> None:
    """工作线程: 预处理 → 推理 → 输出; 失败降级返回原视频。"""
    with _tasks_lock:
        task = _tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        task.progress = 10
        task.message = "预处理音视频 (25fps / 16kHz)"

    proc_video: Optional[Path] = None
    proc_audio: Optional[Path] = None
    try:
        proc_video = _preprocess_video(video_path)
        proc_audio = _preprocess_audio(audio_path)
        task.temp_files.extend([proc_video, proc_audio])

        with _tasks_lock:
            task.progress = 20
            task.message = "加载模型 / 推理中"

        predictor = get_predictor()
        if predictor is None:
            raise RuntimeError(f"模型未就绪: {_model_error or '未知错误'}")

        result_path = _call_predict(
            predictor, proc_video, proc_audio,
            reference_image, inference_steps, guidance_scale,
        )

        # 标准化输出: 移动到 OUTPUT_DIR 以 <task_id>.mp4 命名
        out_name = f"{task_id}.mp4"
        out_path = OUTPUT_DIR / out_name
        if result_path.exists() and result_path.resolve() != out_path.resolve():
            shutil.move(str(result_path), str(out_path))
        elif not result_path.exists():
            raise RuntimeError(f"推理输出不存在: {result_path}")

        duration = _probe_duration(out_path)
        with _tasks_lock:
            task.status = TaskStatus.SUCCEEDED
            task.progress = 100
            task.message = "唇形同步完成"
            task.video_url = _result_url(task.base_url, out_name)
            task.duration_seconds = duration
            task.finished_at = time.time()
        logger.info("任务完成 %s -> %s (%.2fs)", task_id, out_name, duration)

    except Exception as exc:  # noqa: BLE001 - 任何推理错误均降级
        logger.exception("任务推理失败 %s, 降级返回原视频: %s", task_id, exc)
        degraded_name = f"{task_id}_degraded.mp4"
        degraded_path = OUTPUT_DIR / degraded_name
        try:
            # 降级: 把原视频(已转 25fps 的预处理产物优先, 否则原始上传)复制为结果
            src_for_degrade = proc_video if (proc_video and proc_video.exists()) else video_path
            shutil.copy(str(src_for_degrade), str(degraded_path))
            duration = _probe_duration(degraded_path)
            with _tasks_lock:
                task.status = TaskStatus.SUCCEEDED  # 降级视为成功, 调用方通过 degraded 字段识别
                task.progress = 100
                task.message = f"唇形同步失败, 已降级返回原视频: {exc}"
                task.error = str(exc)
                task.degraded = True
                task.video_url = _result_url(task.base_url, degraded_name)
                task.duration_seconds = duration
                task.finished_at = time.time()
            logger.warning("任务降级 %s -> %s", task_id, degraded_name)
        except Exception:  # noqa: BLE001 - 降级也失败才标记 FAILED
            logger.exception("降级也失败 %s", task_id)
            with _tasks_lock:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.message = "唇形同步失败且降级失败"
                task.finished_at = time.time()

    finally:
        # 清理临时输入与预处理产物 (保留 OUTPUT_DIR 下的结果供下载)
        with _tasks_lock:
            temps = list(task.temp_files)
            task.temp_files.clear()
        _cleanup(temps)


def _submit_inference(
    task_id: str,
    video_path: Path,
    audio_path: Path,
    reference_image: Optional[Path],
    inference_steps: int,
    guidance_scale: float,
) -> None:
    _executor.submit(
        _run_inference,
        task_id, video_path, audio_path, reference_image,
        inference_steps, guidance_scale,
    )


# ----------------------------------------------------------------------------
# FastAPI 应用
# ----------------------------------------------------------------------------
app = FastAPI(
    title="LatentSync 1.6 Lip Sync Service",
    version="1.6.0",
    description="字节 LatentSync 唇形同步服务 (OpenAI 风格 + 兼容接口)",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# 静态文件: /files/output/xxx.mp4 提供结果下载; /files/uploads/ 亦可访问
app.mount("/files", StaticFiles(directory=str(IO_DIR)), name="files")


@app.on_event("startup")
async def _startup() -> None:
    """后台线程加载模型, 不阻塞 HTTP 启动; 健康检查上报就绪状态。"""
    threading.Thread(target=_load_predictor, daemon=True).start()


# ---- 健康检查 / 模型列表 ----
@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_ready": _model_ready,
        "model_error": _model_error,
        "tasks_total": len(_tasks),
    }


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "LatentSync-1.6",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "bytedance",
                "resolution": 512,
            }
        ],
    }


# ---- 主接口: 提交唇形同步任务 ----
@app.post("/lip_sync")
@app.post("/v1/lip_sync")
async def lip_sync(
    request: Request,
    video: Optional[UploadFile] = File(None),
    audio: Optional[UploadFile] = File(None),
    reference_image: Optional[UploadFile] = File(None),
    video_url: Optional[str] = Form(None),
    audio_url: Optional[str] = Form(None),
    reference_image_url: Optional[str] = Form(None),
    inference_steps: int = Form(40),
    guidance_scale: float = Form(1.5),
) -> dict[str, Any]:
    """提交唇形同步任务 (multipart)。文件与 *_url 二选一; 返回 task_id 异步处理。"""
    base_url = str(request.base_url)
    video_path = await _resolve_input(video, video_url, "video", "mp4", "video")
    audio_path = await _resolve_input(audio, audio_url, "audio", "wav", "audio")
    ref_path: Optional[Path] = None
    if reference_image is not None and reference_image.filename:
        ref_path = await _save_upload(reference_image, "ref", "png")
    elif reference_image_url:
        ref_path = await _download_url(reference_image_url, "ref", "png")

    if inference_steps <= 0 or inference_steps > 200:
        raise HTTPException(status_code=400, detail="inference_steps 取值范围 1-200")
    if guidance_scale <= 0:
        raise HTTPException(status_code=400, detail="guidance_scale 必须为正数")

    task = _new_task(base_url)
    task.temp_files.extend([video_path, audio_path])
    if ref_path is not None:
        task.temp_files.append(ref_path)

    _submit_inference(
        task.task_id, video_path, audio_path, ref_path,
        inference_steps, guidance_scale,
    )
    logger.info(
        "提交任务 %s steps=%s gs=%s ref=%s",
        task.task_id, inference_steps, guidance_scale, ref_path is not None,
    )
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "message": "任务已排队",
        "poll_url": f"{base_url.rstrip('/')}/tasks/{task.task_id}",
    }


# ---- 主接口: 查询任务 ----
@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    resp: dict[str, Any] = {
        "task_id": task.task_id,
        "status": task.status.value,
        "progress": task.progress,
        "message": task.message,
        "degraded": task.degraded,
    }
    if task.error is not None:
        resp["error"] = task.error
    if task.video_url is not None:
        resp["video_url"] = task.video_url
    if task.duration_seconds is not None:
        resp["duration_seconds"] = task.duration_seconds
    return resp


# ============================================================================
# 兼容接口: 供既有 app/services/latentsync_service.py 客户端零改动使用
# 复用同一任务存储, 仅做协议适配
# ============================================================================
@app.post("/v1/video/upload")
async def legacy_upload(
    media: UploadFile = File(...),
    media_type: str = Form("video", alias="type"),
) -> dict[str, Any]:
    """上传媒体文件, 返回服务端文件名供后续 /v1/lipsync/submit 引用。"""
    ext_map = {"video": "mp4", "audio": "mp3", "reference": "png"}
    ext = ext_map.get(media_type, "bin")
    filename = f"{media_type}_{uuid.uuid4().hex[:12]}.{ext}"
    dst = UPLOAD_DIR / filename
    content = await media.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    dst.write_bytes(content)
    logger.info("兼容接口上传 %s -> %s", media_type, filename)
    return {"filename": filename}


@app.post("/v1/lipsync/submit")
async def legacy_submit(request: Request, payload: dict = Body(...)) -> dict[str, Any]:
    """提交唇形同步任务 (JSON)。video/audio 为 /v1/video/upload 返回的 filename。"""
    base_url = str(request.base_url)
    video_filename = payload.get("video")
    audio_filename = payload.get("audio")
    if not video_filename or not audio_filename:
        raise HTTPException(status_code=400, detail="缺少 video / audio 文件名")

    video_path = UPLOAD_DIR / video_filename
    audio_path = UPLOAD_DIR / audio_filename
    if not video_path.exists() or not audio_path.exists():
        raise HTTPException(status_code=404, detail="video/audio 文件未找到, 请先调用 /v1/video/upload")

    ref_path: Optional[Path] = None
    ref_filename = payload.get("reference_image")
    if ref_filename:
        ref_path = UPLOAD_DIR / ref_filename
        if not ref_path.exists():
            ref_path = None

    inference_steps = int(payload.get("inference_steps", 40))
    guidance_scale = float(payload.get("guidance_scale", 1.5))

    task = _new_task(base_url)
    # 兼容接口的输入文件由上传端管理, 这里不加入 temp_files 以免误删复用文件
    _submit_inference(
        task.task_id, video_path, audio_path, ref_path,
        inference_steps, guidance_scale,
    )
    logger.info("兼容接口提交任务 %s scene_id=%s", task.task_id, payload.get("scene_id"))
    return {"task_id": task.task_id}


@app.get("/v1/lipsync/status/{task_id}")
async def legacy_status(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    resp: dict[str, Any] = {
        "status": task.status.value,
        "progress": task.progress,
        "message": task.message,
    }
    if task.error is not None:
        resp["error"] = task.error
    if task.finished_at is not None and task.started_at is not None:
        resp["elapsed_seconds"] = round(task.finished_at - task.started_at, 2)
    return resp


@app.get("/v1/lipsync/result/{task_id}")
async def legacy_result(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if task.status != TaskStatus.SUCCEEDED or task.video_url is None:
        raise HTTPException(status_code=404, detail="结果未就绪")
    return {
        "video_url": task.video_url,
        "duration_seconds": task.duration_seconds or 0.0,
    }


# ----------------------------------------------------------------------------
# 入口: argparse 接收 --host / --port
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="LatentSync 1.6 唇形同步服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8289, help="监听端口 (默认 8289)")
    args = parser.parse_args()

    import uvicorn

    logger.info("启动 LatentSync 服务: %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
