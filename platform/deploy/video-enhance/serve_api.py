"""
video-enhance 三合一服务 (RealBasicVSR + RIFE + ProPainter) FastAPI 包装层.

部署: workstation 192.168.71.127 GPU3, 端口 8290
设计要点:
  - 用 subprocess 调用三个工具的 CLI, 避免复杂的 Python import 冲突
    (RealBasicVSR 用 mmcv-full==1.5.3, ProPainter 用 mmcv>=2.0, 进程隔离最稳)
  - 异步任务: dict 存状态, uuid 生成 task_id, 后台线程跑推理
  - pipeline 单步失败不阻断 (best-effort), 返回部分结果 + warnings
  - 帧率协调: RealBasicVSR 输出 25fps, RIFE 插帧后变 50fps,
    pipeline 中间用 ffmpeg 重采样到目标 fps
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import aiofiles
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

# ============================================================
# 配置
# ============================================================

WORKSPACE = "/workspace"
IO_INPUT_DIR = os.path.join(WORKSPACE, "io", "input")
IO_OUTPUT_DIR = os.path.join(WORKSPACE, "io", "output")
CHECKPOINT_DIR = os.path.join(WORKSPACE, "checkpoints")

# 三个工具的路径
REALBASICVSR_DIR = os.path.join(WORKSPACE, "RealBasicVSR")
RIFE_DIR = os.path.join(WORKSPACE, "RIFE")
PROPAINTER_DIR = os.path.join(WORKSPACE, "ProPainter")

# 模型权重 (挂载自 /home/merlin/video-models)
REALBASICVSR_WEIGHT = os.path.join(CHECKPOINT_DIR, "RealBasicVSR_x4.pth")
RIFE_WEIGHT = os.path.join(CHECKPOINT_DIR, "RIFE_v4.6.pkl")
PROPAINTER_WEIGHT = os.path.join(CHECKPOINT_DIR, "ProPainter.pth")
RFC_WEIGHT = os.path.join(CHECKPOINT_DIR, "recurrent_flow_completion.pth")
RAFT_WEIGHT = os.path.join(CHECKPOINT_DIR, "raft-things.pth")

# RealBasicVSR 固定输出帧率, RIFE exp=1 后翻倍
SR_OUTPUT_FPS = 25

# 服务对外暴露的 URL 前缀 (容器内 0.0.0.0:8290, 外部访问用 workstation IP)
# 这里用相对路径 /files/, 由 StaticFiles 挂载提供下载
OUTPUT_URL_PREFIX = "/files/output"

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("video-enhance")

# ============================================================
# 异步任务管理
# ============================================================


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class TaskState:
    task_id: str
    task_type: str  # super_resolution / interpolate / inpaint / enhance_pipeline
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "warnings": self.warnings,
            "error": self.error,
            "log": self.log[-50:],  # 仅返回最后 50 行日志, 避免响应过大
        }


# 全局任务表 + 线程锁
_TASKS: dict[str, TaskState] = {}
_TASKS_LOCK = threading.Lock()

# 后台线程池: 串行执行避免显存冲突 (峰值约 15GB, 单卡 96GB 虽够但 IO 串行更稳)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ve-worker")


# ============================================================
# 工具函数
# ============================================================


def ensure_dirs() -> None:
    """确保 IO 目录存在."""
    os.makedirs(IO_INPUT_DIR, exist_ok=True)
    os.makedirs(IO_OUTPUT_DIR, exist_ok=True)


async def save_upload(upload: UploadFile, dest_dir: str, suffix: str = "") -> str:
    """异步保存上传文件, 返回保存路径."""
    ensure_dirs()
    filename = f"{uuid.uuid4().hex}{suffix}"
    # 保留原始扩展名
    if upload.filename and "." in upload.filename:
        ext = "." + upload.filename.rsplit(".", 1)[-1].lower()
        if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".png", ".jpg", ".jpeg"):
            filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(dest_dir, filename)
    async with aiofiles.open(dest, "wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            await f.write(chunk)
    return dest


def run_subprocess(
    cmd: list[str],
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    log_prefix: str = "",
) -> tuple[int, str, str]:
    """同步执行子进程, 返回 (returncode, stdout, stderr)."""
    logger.info("%s执行: %s (cwd=%s)", log_prefix, " ".join(cmd), cwd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=1800,  # 单步最多 30 分钟
        )
        if proc.stdout:
            logger.info("%s[stdout] %s", log_prefix, proc.stdout[-2000:])
        if proc.stderr:
            logger.warning("%s[stderr] %s", log_prefix, proc.stderr[-2000:])
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return -1, e.stdout or "", f"Timeout after 1800s: {e.stderr or ''}"
    except Exception as e:  # noqa: BLE001
        return -2, "", f"Exception: {e}"


def ffmpeg_resample_fps(input_path: str, output_path: str, target_fps: int) -> tuple[int, str, str]:
    """用 ffmpeg 重采样视频帧率."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-r", str(target_fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        output_path,
    ]
    return run_subprocess(cmd, log_prefix="[ffmpeg-resample] ")


def make_output_url(output_path: str) -> str:
    """根据输出文件路径生成可下载 URL (相对路径)."""
    rel = os.path.relpath(output_path, IO_OUTPUT_DIR)
    return f"{OUTPUT_URL_PREFIX}/{rel}"


# ============================================================
# 三个核心推理函数
# ============================================================


def do_super_resolution(input_path: str, scale: int, log_prefix: str = "") -> dict[str, Any]:
    """调用 RealBasicVSR 做超分. 返回 {output_path, url} 或抛异常."""
    output_path = os.path.join(
        IO_OUTPUT_DIR, f"sr_{uuid.uuid4().hex}.mp4"
    )
    # RealBasicVSR inference 脚本参数:
    #   inference_realbasicvsr.py <config> <checkpoint> <input_path> <output_path> --fps=25
    cmd = [
        "python", os.path.join(REALBASICVSR_DIR, "inference_realbasicvsr.py"),
        os.path.join(REALBASICVSR_DIR, "configs", "realbasicvsr_x4.py"),
        REALBASICVSR_WEIGHT,
        input_path,
        output_path,
        f"--fps={SR_OUTPUT_FPS}",
    ]
    # RealBasicVSR 默认 x4, scale 参数仅用于记录 (脚本本身固定 x4)
    if scale != 4:
        logger.warning("%sRealBasicVSR 仅支持 x4, scale=%s 被忽略", log_prefix, scale)

    rc, out, err = run_subprocess(
        cmd, cwd=REALBASICVSR_DIR, log_prefix=log_prefix + "[RealBasicVSR] "
    )
    if rc != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"RealBasicVSR 失败 (rc={rc}): {err[-500:]}")

    return {"output_path": output_path, "url": make_output_url(output_path), "fps": SR_OUTPUT_FPS}


def do_interpolate(input_path: str, exp: int, log_prefix: str = "") -> dict[str, Any]:
    """调用 RIFE 做插帧. exp=1 → 2x, exp=2 → 4x."""
    # RIFE 输出到目录, 文件名 会在该目录下生成
    out_dir = os.path.join(IO_OUTPUT_DIR, f"rife_{uuid.uuid4().hex}")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "python", os.path.join(RIFE_DIR, "inference_video.py"),
        f"--exp={exp}",
        f"--video={input_path}",
        f"--output={out_dir}",
    ]
    rc, out, err = run_subprocess(
        cmd, cwd=RIFE_DIR, log_prefix=log_prefix + "[RIFE] "
    )
    if rc != 0:
        raise RuntimeError(f"RIFE 失败 (rc={rc}): {err[-500:]}")

    # RIFE 输出文件名格式: <input_basename>_fpsX.mp4, 在 out_dir 中查找
    output_path = None
    for fname in os.listdir(out_dir):
        if fname.endswith(".mp4"):
            output_path = os.path.join(out_dir, fname)
            break
    if not output_path:
        raise RuntimeError(f"RIFE 未生成输出文件, out_dir={out_dir}")

    # 移动到 output 顶层方便管理
    final_path = os.path.join(IO_OUTPUT_DIR, f"interp_{uuid.uuid4().hex}.mp4")
    shutil.move(output_path, final_path)
    shutil.rmtree(out_dir, ignore_errors=True)

    multiplier = 2 ** exp
    return {
        "output_path": final_path,
        "url": make_output_url(final_path),
        "fps_multiplier": multiplier,
    }


def do_inpaint(
    input_path: str,
    mask_path: str,
    task: str,
    log_prefix: str = "",
) -> dict[str, Any]:
    """调用 ProPainter 做视频修复/物体移除."""
    output_dir = os.path.join(IO_OUTPUT_DIR, f"propainter_{uuid.uuid4().hex}")
    os.makedirs(output_dir, exist_ok=True)
    cmd = [
        "python", os.path.join(PROPAINTER_DIR, "inference_propainter.py"),
        f"--video={input_path}",
        f"--mask={mask_path}",
        f"--output_dir={output_dir}",
        f"--task={task}",
    ]
    rc, out, err = run_subprocess(
        cmd, cwd=PROPAINTER_DIR, log_prefix=log_prefix + "[ProPainter] "
    )
    if rc != 0:
        raise RuntimeError(f"ProPainter 失败 (rc={rc}): {err[-500:]}")

    # ProPainter 输出在 output_dir/inpaint_out/ 下
    output_path = None
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if fname.endswith(".mp4"):
                output_path = os.path.join(root, fname)
                break
        if output_path:
            break
    if not output_path:
        raise RuntimeError(f"ProPainter 未生成输出文件, output_dir={output_dir}")

    final_path = os.path.join(IO_OUTPUT_DIR, f"inpaint_{uuid.uuid4().hex}.mp4")
    shutil.move(output_path, final_path)
    shutil.rmtree(output_dir, ignore_errors=True)

    return {"output_path": final_path, "url": make_output_url(final_path)}


# ============================================================
# Pipeline 串联
# ============================================================


def run_pipeline(
    input_path: str,
    mask_path: Optional[str],
    scale: int,
    exp: int,
    task: str,
    task_state: TaskState,
) -> dict[str, Any]:
    """串联 超分 → 插帧 → 修复. 单步失败不阻断, 返回部分结果 + warnings."""

    def _log(msg: str) -> None:
        task_state.log.append(msg)
        logger.info("[task=%s] %s", task_state.task_id, msg)

    def _warn(msg: str) -> None:
        task_state.warnings.append(msg)
        logger.warning("[task=%s] %s", task_state.task_id, msg)

    result: dict[str, Any] = {"steps": {}}
    current_input = input_path
    current_fps = SR_OUTPUT_FPS  # 假设输入约 25fps

    # ---- Step 1: 超分 ----
    _log("Step 1/3: 超分 (RealBasicVSR x4)")
    try:
        sr_res = do_super_resolution(current_input, scale, log_prefix="[step1] ")
        result["steps"]["super_resolution"] = {"status": "success", **sr_res}
        current_input = sr_res["output_path"]
        current_fps = SR_OUTPUT_FPS
        _log(f"超分完成, 输出 fps={current_fps}")
    except Exception as e:  # noqa: BLE001
        _warn(f"超分失败: {e}")
        result["steps"]["super_resolution"] = {"status": "failed", "error": str(e)}
        # 超分失败则用原视频继续

    # ---- 中间: ffmpeg 帧率归一化 (确保 RIFE 输入帧率稳定) ----
    # RealBasicVSR 输出 25fps, 这里重采样一次保证后续 RIFE 输入干净
    try:
        normalized = os.path.join(IO_OUTPUT_DIR, f"norm_{uuid.uuid4().hex}.mp4")
        rc, _o, _e = ffmpeg_resample_fps(current_input, normalized, current_fps)
        if rc == 0 and os.path.exists(normalized):
            current_input = normalized
            _log(f"帧率归一化完成, target_fps={current_fps}")
        else:
            _warn("帧率归一化失败, 用原视频继续插帧")
    except Exception as e:  # noqa: BLE001
        _warn(f"帧率归一化异常: {e}")

    # ---- Step 2: 插帧 ----
    _log(f"Step 2/3: 插帧 (RIFE exp={exp}, 预期 {2**exp}x)")
    try:
        interp_res = do_interpolate(current_input, exp, log_prefix="[step2] ")
        result["steps"]["interpolate"] = {"status": "success", **interp_res}
        current_input = interp_res["output_path"]
        # RIFE exp=1 → 50fps, exp=2 → 100fps
        current_fps = current_fps * (2 ** exp)
        _log(f"插帧完成, 当前 fps={current_fps}")

        # 插帧后帧率过高 (50/100fps), 用 ffmpeg 重采样回 25fps 便于下游处理和播放
        downsampled = os.path.join(IO_OUTPUT_DIR, f"ds_{uuid.uuid4().hex}.mp4")
        rc, _o, _e = ffmpeg_resample_fps(current_input, downsampled, SR_OUTPUT_FPS)
        if rc == 0 and os.path.exists(downsampled):
            current_input = downsampled
            current_fps = SR_OUTPUT_FPS
            _log(f"插帧后重采样回 {SR_OUTPUT_FPS}fps")
        else:
            _warn("插帧后重采样失败, 用高帧率视频继续修复")
    except Exception as e:  # noqa: BLE001
        _warn(f"插帧失败: {e}")
        result["steps"]["interpolate"] = {"status": "failed", "error": str(e)}

    # ---- Step 3: 修复 ----
    if mask_path and os.path.exists(mask_path):
        _log("Step 3/3: 视频修复 (ProPainter)")
        try:
            inpaint_res = do_inpaint(current_input, mask_path, task, log_prefix="[step3] ")
            result["steps"]["inpaint"] = {"status": "success", **inpaint_res}
            current_input = inpaint_res["output_path"]
            _log("修复完成")
        except Exception as e:  # noqa: BLE001
            _warn(f"修复失败: {e}")
            result["steps"]["inpaint"] = {"status": "failed", "error": str(e)}
    else:
        _log("Step 3/3: 跳过修复 (未提供 mask)")
        result["steps"]["inpaint"] = {"status": "skipped", "reason": "no mask provided"}

    # 最终输出
    result["final_output_path"] = current_input
    result["final_url"] = make_output_url(current_input)
    result["final_fps"] = current_fps
    return result


# ============================================================
# 任务执行器 (后台线程)
# ============================================================


def _execute_task(
    task_id: str,
    task_type: str,
    fn: Any,
    *args: Any,
    **kwargs: Any,
) -> None:
    """在后台线程中执行任务, 更新 task 状态."""
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

    try:
        result = fn(*args, **kwargs)
        with _TASKS_LOCK:
            task.result = result
            task.status = TaskStatus.SUCCESS
            task.finished_at = time.time()
        logger.info("[task=%s] 成功完成", task_id)
    except Exception as e:  # noqa: BLE001
        with _TASKS_LOCK:
            task.error = str(e)
            task.status = TaskStatus.FAILED
            task.finished_at = time.time()
            task.log.append(f"FATAL: {e}")
        logger.exception("[task=%s] 失败", task_id)


def submit_task(task_type: str, fn: Any, *args: Any, **kwargs: Any) -> str:
    """提交后台任务, 返回 task_id."""
    task_id = uuid.uuid4().hex
    state = TaskState(task_id=task_id, task_type=task_type)
    with _TASKS_LOCK:
        _TASKS[task_id] = state
    _EXECUTOR.submit(_execute_task, task_id, task_type, fn, *args, **kwargs)
    return task_id


# ============================================================
# FastAPI app
# ============================================================

app = FastAPI(
    title="Video Enhance Service",
    description="RealBasicVSR 超分 + RIFE 插帧 + ProPainter 修复 三合一服务",
    version="1.0.0",
)

# 确保 IO 目录存在 (StaticFiles 挂载需要目录已存在, 模块加载时即创建)
ensure_dirs()

# 挂载静态文件目录用于下载结果
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount("/files/output", StaticFiles(directory=IO_OUTPUT_DIR), name="output-files")


@app.get("/health")
async def health() -> dict[str, Any]:
    """健康检查: 报告模型权重是否就位."""
    weights = {
        "RealBasicVSR_x4.pth": os.path.exists(REALBASICVSR_WEIGHT),
        "RIFE_v4.6.pkl": os.path.exists(RIFE_WEIGHT),
        "ProPainter.pth": os.path.exists(PROPAINTER_WEIGHT),
        "recurrent_flow_completion.pth": os.path.exists(RFC_WEIGHT),
        "raft-things.pth": os.path.exists(RAFT_WEIGHT),
    }
    all_ok = all(weights.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "gpu": os.environ.get("NVIDIA_VISIBLE_DEVICES", "unknown"),
        "weights": weights,
        "io_input_dir": IO_INPUT_DIR,
        "io_output_dir": IO_OUTPUT_DIR,
    }


def _enqueue_or_run(
    async_mode: bool,
    task_type: str,
    fn: Any,
    *args: Any,
    **kwargs: Any,
) -> JSONResponse:
    """根据 async_mode 决定同步执行还是提交后台任务."""
    if async_mode:
        task_id = submit_task(task_type, fn, *args, **kwargs)
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "pending",
                "message": f"任务已提交, GET /tasks/{task_id} 查询进度",
            },
        )
    # 同步执行
    sync_state = TaskState(task_id=uuid.uuid4().hex, task_type=task_type)
    sync_state.status = TaskStatus.RUNNING
    sync_state.started_at = time.time()
    try:
        result = fn(*args, **kwargs)
        sync_state.result = result
        sync_state.status = TaskStatus.SUCCESS
        sync_state.finished_at = time.time()
        return JSONResponse(
            status_code=200,
            content={"status": "success", "result": result},
        )
    except Exception as e:  # noqa: BLE001
        sync_state.error = str(e)
        sync_state.status = TaskStatus.FAILED
        sync_state.finished_at = time.time()
        return JSONResponse(
            status_code=500,
            content={"status": "failed", "error": str(e)},
        )


@app.post("/super_resolution")
async def super_resolution(
    video: UploadFile = File(...),
    scale: int = Form(4),
    async_mode: bool = Form(False),
) -> JSONResponse:
    """RealBasicVSR 超分 (x4)."""
    input_path = await save_upload(video, IO_INPUT_DIR, suffix=".mp4")
    return _enqueue_or_run(
        async_mode, "super_resolution", do_super_resolution, input_path, scale
    )


@app.post("/interpolate")
async def interpolate(
    video: UploadFile = File(...),
    exp: int = Form(1),
    async_mode: bool = Form(False),
) -> JSONResponse:
    """RIFE 插帧. exp=1 → 2x (50fps), exp=2 → 4x (100fps)."""
    input_path = await save_upload(video, IO_INPUT_DIR, suffix=".mp4")
    return _enqueue_or_run(
        async_mode, "interpolate", do_interpolate, input_path, exp
    )


@app.post("/inpaint")
async def inpaint(
    video: UploadFile = File(...),
    mask: UploadFile = File(...),
    task: str = Form("object_removal"),
    async_mode: bool = Form(False),
) -> JSONResponse:
    """ProPainter 视频修复. task: object_removal / video_completion."""
    input_path = await save_upload(video, IO_INPUT_DIR, suffix=".mp4")
    mask_path = await save_upload(mask, IO_INPUT_DIR, suffix=".png")
    return _enqueue_or_run(
        async_mode, "inpaint", do_inpaint, input_path, mask_path, task
    )


@app.post("/enhance_pipeline")
async def enhance_pipeline(
    video: UploadFile = File(...),
    mask: Optional[UploadFile] = File(None),
    scale: int = Form(4),
    exp: int = Form(1),
    task: str = Form("object_removal"),
    async_mode: bool = Form(True),  # pipeline 默认异步 (耗时长)
) -> JSONResponse:
    """串联 超分 → 插帧 → 修复. 单步失败不阻断, 返回部分结果 + warnings."""
    input_path = await save_upload(video, IO_INPUT_DIR, suffix=".mp4")
    mask_path = None
    if mask is not None and mask.filename:
        mask_path = await save_upload(mask, IO_INPUT_DIR, suffix=".png")

    # pipeline 需要访问 task_state 写日志, 单独构造
    if async_mode:
        task_id = uuid.uuid4().hex
        state = TaskState(task_id=task_id, task_type="enhance_pipeline")
        with _TASKS_LOCK:
            _TASKS[task_id] = state
        _EXECUTOR.submit(
            _execute_pipeline_task, task_id, input_path, mask_path, scale, exp, task, state
        )
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "pending",
                "message": f"pipeline 已提交, GET /tasks/{task_id} 查询进度",
            },
        )

    # 同步 pipeline
    sync_state = TaskState(task_id=uuid.uuid4().hex, task_type="enhance_pipeline")
    sync_state.status = TaskStatus.RUNNING
    sync_state.started_at = time.time()
    try:
        result = run_pipeline(input_path, mask_path, scale, exp, task, sync_state)
        sync_state.result = result
        sync_state.status = TaskStatus.SUCCESS
        sync_state.finished_at = time.time()
        return JSONResponse(
            status_code=200,
            content={"status": "success", "result": result, "warnings": sync_state.warnings},
        )
    except Exception as e:  # noqa: BLE001
        sync_state.error = str(e)
        sync_state.status = TaskStatus.FAILED
        sync_state.finished_at = time.time()
        return JSONResponse(
            status_code=500,
            content={"status": "failed", "error": str(e), "warnings": sync_state.warnings},
        )


def _execute_pipeline_task(
    task_id: str,
    input_path: str,
    mask_path: Optional[str],
    scale: int,
    exp: int,
    task: str,
    state: TaskState,
) -> None:
    """后台线程执行 pipeline."""
    with _TASKS_LOCK:
        state.status = TaskStatus.RUNNING
        state.started_at = time.time()
    try:
        result = run_pipeline(input_path, mask_path, scale, exp, task, state)
        with _TASKS_LOCK:
            state.result = result
            state.status = TaskStatus.SUCCESS
            state.finished_at = time.time()
        logger.info("[task=%s] pipeline 成功完成", task_id)
    except Exception as e:  # noqa: BLE001
        with _TASKS_LOCK:
            state.error = str(e)
            state.status = TaskStatus.FAILED
            state.finished_at = time.time()
            state.log.append(f"FATAL: {e}")
        logger.exception("[task=%s] pipeline 失败", task_id)


@app.get("/tasks/{task_id}")
async def get_task(task_id: str) -> JSONResponse:
    """查询任务状态."""
    with _TASKS_LOCK:
        state = _TASKS.get(task_id)
    if state is None:
        return JSONResponse(status_code=404, content={"error": "task not found"})
    return JSONResponse(status_code=200, content=state.to_dict())


@app.on_event("startup")
async def _on_startup() -> None:
    ensure_dirs()
    logger.info("video-enhance 服务启动")
    logger.info("IO_INPUT_DIR=%s", IO_INPUT_DIR)
    logger.info("IO_OUTPUT_DIR=%s", IO_OUTPUT_DIR)
    logger.info("CHECKPOINT_DIR=%s", CHECKPOINT_DIR)


# ============================================================
# 入口
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="video-enhance FastAPI 服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8290, help="监听端口")
    parser.add_argument("--workers", type=int, default=1, help="uvicorn workers (建议 1, GPU 串行)")
    args = parser.parse_args()

    uvicorn.run(
        "serve_api:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
