"""xDiT + HunyuanVideo-I2V 视频生成服务 FastAPI 包装层。

基于 thufeifeibear/xdit-dev 镜像，在容器内通过 diffusers + xfuser Python API
调用 HunyuanVideo-I2V 模型进行图生视频推理，并通过 HTTP 对外提供 OpenAI 风格接口。

部署: workstation 192.168.71.127 GPU3, 端口 8288, 单卡模式。
"""

import argparse
import asyncio
import base64
import io
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

import httpx
import torch
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ===== 日志配置 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("xdit-video")

# ===== 路径与环境变量 =====
# 模型权重通过 HF 挂载，不在镜像内下载
MODEL_ID = os.environ.get("HUNYUANVIDEO_MODEL_ID", "hunyuanvideo/HunyuanVideo-I2V")
IO_DIR = os.environ.get("XDIT_IO_DIR", "/workspace/io")
OUTPUT_DIR = os.path.join(IO_DIR, "output")
UPLOAD_DIR = os.path.join(IO_DIR, "upload")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 对外可访问的服务地址（用于生成文件 URL，需与宿主机实际访问地址一致）
SERVICE_HOST = os.environ.get("SERVICE_HOST", "192.168.71.127")
SERVICE_PORT = os.environ.get("SERVICE_PORT", "8288")
SERVICE_BASE = f"http://{SERVICE_HOST}:{SERVICE_PORT}"

# FP8 量化开关（开启后显存约 35-45GB）
ENABLE_FP8 = os.environ.get("ENABLE_FP8", "false").lower() == "true"
# 是否启用 CPU offload（降低显存占用，但推理变慢）
ENABLE_CPU_OFFLOAD = os.environ.get("ENABLE_CPU_OFFLOAD", "true").lower() == "true"
# 默认视频帧率
VIDEO_FPS = int(os.environ.get("VIDEO_FPS", "24"))
# 同步模式最长等待秒数（视频生成通常 3-8 分钟，留足余量）
SYNC_TIMEOUT = int(os.environ.get("SYNC_TIMEOUT", "900"))

# ===== 分辨率预设 (height, width) =====
SIZE_PRESETS: Dict[str, Tuple[int, int]] = {
    "540p": (540, 960),
    "720p": (720, 1280),
    "1080p": (1080, 1920),
}

# ===== 全局状态 =====
_pipe = None  # 懒加载的 diffusers pipeline
_pipe_lock = threading.Lock()  # 保护 pipeline 懒加载，避免并发重复加载
_runtime_initialized = False  # xfuser runtime 是否已初始化
_runtime_lock = threading.Lock()

# 异步任务存储: task_id -> 任务字典
_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()

# 推理线程池（单线程，避免显存争抢）
_infer_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="infer")


# ===== 请求/响应数据模型 =====
class GenerationRequest(BaseModel):
    model: str = Field(default="hunyuanvideo-i2v")
    prompt: str = Field(default="", description="文本提示词")
    image_url: str = Field(
        ..., description="I2V 输入图，支持 http(s) URL / base64 / 本服务上传返回的 URL"
    )
    num_frames: int = Field(default=97, ge=5, le=257, description="生成帧数，自动对齐 4k+1")
    num_inference_steps: int = Field(default=30, ge=1, le=200)
    cfg: float = Field(default=6.0, ge=0.0, le=20.0, description="guidance scale")
    seed: int = Field(default=0, description="随机种子，0 表示随机")
    size: str = Field(default="720p", description="分辨率预设或 WxH")


app = FastAPI(title="xDiT HunyuanVideo-I2V Service", version="1.0.0")
# 挂载静态文件，便于通过 /files/output/xxx.mp4 访问生成结果
app.mount("/files", StaticFiles(directory=IO_DIR), name="files")


# ===== 工具函数 =====
def align_num_frames(n: int) -> int:
    """对齐帧数到 4k+1（HunyuanVideo 模型要求）。"""
    n = max(5, n)
    k = round((n - 1) / 4)
    return k * 4 + 1


def parse_size(size: str) -> Tuple[int, int]:
    """解析分辨率字符串，返回 (height, width)。

    支持 '720p' 预设，或 'WxH' / 'HxW' 形式。
    """
    size = size.strip().lower()
    if size in SIZE_PRESETS:
        return SIZE_PRESETS[size]
    if "x" in size:
        parts = size.split("x")
        if len(parts) == 2:
            try:
                a, b = int(parts[0]), int(parts[1])
                # 约定返回 (height, width)，取较小者为 height
                return (min(a, b), max(a, b))
            except ValueError:
                pass
    raise HTTPException(status_code=400, detail=f"unsupported size: {size}")


def load_image(image_ref: str):
    """从 URL / base64 / 本地路径加载 PIL.Image。

    支持:
      - data:image/...;base64,.... 数据 URI
      - http(s) URL（自动下载；本服务上传的文件直接读本地）
      - 纯 base64 字符串
      - 本地文件路径
    """
    from PIL import Image

    ref = image_ref.strip()

    # data URI: data:image/xxx;base64,....
    if ref.startswith("data:"):
        header, _, b64 = ref.partition(",")
        try:
            raw = base64.b64decode(b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid base64 image: {e}")
        return Image.open(io.BytesIO(raw)).convert("RGB")

    # http(s) URL
    if ref.lower().startswith(("http://", "https://")):
        # 本服务上传的文件可直接读本地，避免回环请求
        local_prefix = f"{SERVICE_BASE}/files/"
        if ref.startswith(local_prefix):
            rel = ref[len(local_prefix):]
            local_path = os.path.join(IO_DIR, rel)
            if os.path.exists(local_path):
                return Image.open(local_path).convert("RGB")
        # 远程下载
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                resp = client.get(ref)
                resp.raise_for_status()
                return Image.open(io.BytesIO(resp.content)).convert("RGB")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=400, detail=f"failed to download image: {e}")

    # base64 原文（无前缀）：尝试解码并校验图片魔术字节
    try:
        raw = base64.b64decode(ref, validate=True)
        if raw[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xdb"):
            return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        pass

    # 本地路径
    if os.path.exists(ref):
        return Image.open(ref).convert("RGB")

    raise HTTPException(
        status_code=400, detail=f"cannot load image from: {image_ref[:64]}..."
    )


def init_runtime_once():
    """初始化 xDiT/xfuser 分布式运行时（单卡模式），仅执行一次。"""
    global _runtime_initialized
    with _runtime_lock:
        if _runtime_initialized:
            return
        try:
            from xfuser.core.distributed import initialize_runtime

            logger.info("初始化 xfuser runtime（单卡模式 world_size=1）...")
            initialize_runtime(
                world_size=1,
                ulysses_degree=1,
                pipefusion_degree=1,
                cfg_degree=1,
            )
            logger.info("xfuser runtime 初始化完成")
        except Exception as e:
            # 单卡模式下 xfuser 初始化失败不影响原生 diffusers 推理
            logger.warning("xfuser runtime 初始化失败，回退原生 diffusers: %s", e)
        _runtime_initialized = True


def load_pipeline():
    """懒加载 HunyuanVideo-I2V pipeline（线程安全）。"""
    global _pipe
    if _pipe is not None:
        return _pipe
    with _pipe_lock:
        if _pipe is not None:
            return _pipe
        init_runtime_once()

        # I2V 优先使用 HunyuanVideoImageToVideoPipeline；不可用时回退 HunyuanVideoPipeline
        try:
            from diffusers import HunyuanVideoImageToVideoPipeline as _PipeClass

            pipe_cls_name = "HunyuanVideoImageToVideoPipeline"
        except ImportError:
            from diffusers import HunyuanVideoPipeline as _PipeClass

            pipe_cls_name = "HunyuanVideoPipeline"
            logger.warning("HunyuanVideoImageToVideoPipeline 不可用，回退 HunyuanVideoPipeline")

        logger.info("加载 pipeline %s: %s", pipe_cls_name, MODEL_ID)

        # 可选 FP8 量化加载（需 optimum-quanto）
        if ENABLE_FP8:
            try:
                from diffusers import HunyuanVideoTransformer3DModel
                from optimum.quanto import qfloat8, quantize, freeze

                logger.info("以 FP8 量化加载 transformer...")
                transformer = HunyuanVideoTransformer3DModel.from_pretrained(
                    MODEL_ID, subfolder="transformer", torch_dtype=torch.bfloat16
                )
                quantize(transformer, weights=qfloat8)
                freeze(transformer)
                _pipe = _PipeClass.from_pretrained(
                    MODEL_ID, transformer=transformer, torch_dtype=torch.bfloat16
                )
            except Exception as e:
                logger.warning("FP8 加载失败，回退 bfloat16: %s", e)
                _pipe = _PipeClass.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16)
        else:
            _pipe = _PipeClass.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16)

        if ENABLE_CPU_OFFLOAD:
            _pipe.enable_model_cpu_offload()
            logger.info("已启用 model_cpu_offload")

        # VAE tiling 降低显存峰值
        if hasattr(_pipe, "vae") and hasattr(_pipe.vae, "enable_tiling"):
            _pipe.vae.enable_tiling()

        logger.info("pipeline 加载完成")
        return _pipe


def save_video(frames, output_path: str, fps: int = 24) -> str:
    """将 diffusers 输出的帧序列保存为 mp4。

    frames 可以是 torch tensor (F, H, W, C)、numpy 数组或 PIL Image 列表。
    """
    from diffusers.utils import export_to_video

    # tensor 先转 numpy，export_to_video 对 numpy/PIL 兼容性最好
    if hasattr(frames, "cpu"):
        frames = frames.cpu().numpy()
    elif isinstance(frames, list) and frames and hasattr(frames[0], "cpu"):
        frames = [f.cpu().numpy() for f in frames]

    export_to_video(frames, output_path, fps=fps)
    return output_path


def _do_generate(req: GenerationRequest) -> Dict[str, Any]:
    """实际执行一次视频生成，返回结果字典。"""
    pipe = load_pipeline()
    image = load_image(req.image_url)
    h, w = parse_size(req.size)
    num_frames = align_num_frames(req.num_frames)

    # seed=0 表示随机
    seed = req.seed if req.seed != 0 else torch.seed()
    generator = torch.Generator(device="cpu").manual_seed(seed)

    logger.info(
        "生成视频: prompt=%r image=%dx%d frames=%d steps=%d cfg=%.2f seed=%d size=%dx%d",
        req.prompt[:50],
        image.width,
        image.height,
        num_frames,
        req.num_inference_steps,
        req.cfg,
        seed,
        w,
        h,
    )
    start = time.time()

    # 调用 pipeline；不同 diffusers 版本参数名略有差异，统一用关键字
    output = pipe(
        image=image,
        prompt=req.prompt,
        num_frames=num_frames,
        num_inference_steps=req.num_inference_steps,
        guidance_scale=req.cfg,
        height=h,
        width=w,
        generator=generator,
    )

    elapsed = time.time() - start
    logger.info("推理完成，耗时 %.1fs", elapsed)

    task_tag = uuid.uuid4().hex[:8]
    video_filename = f"{task_tag}.mp4"
    video_path = os.path.join(OUTPUT_DIR, video_filename)
    save_video(output.frames[0], video_path, fps=VIDEO_FPS)

    video_url = f"{SERVICE_BASE}/files/output/{video_filename}"
    return {
        "url": video_url,
        "elapsed": round(elapsed, 2),
        "num_frames": num_frames,
        "size": f"{w}x{h}",
        "seed": seed,
    }


def run_inference(task_id: str, req: GenerationRequest):
    """在后台线程中执行视频生成，更新任务状态。

    由 _infer_executor 调度，串行执行避免显存争抢。
    """
    with _tasks_lock:
        _tasks[task_id]["status"] = "running"
        _tasks[task_id]["started_at"] = time.time()
    try:
        result = _do_generate(req)
        with _tasks_lock:
            _tasks[task_id]["status"] = "succeeded"
            _tasks[task_id]["result"] = result
            _tasks[task_id]["finished_at"] = time.time()
    except HTTPException as e:
        logger.exception("task %s failed (HTTPException)", task_id)
        with _tasks_lock:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = e.detail
            _tasks[task_id]["finished_at"] = time.time()
    except Exception as e:
        logger.exception("task %s failed", task_id)
        with _tasks_lock:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = str(e)
            _tasks[task_id]["finished_at"] = time.time()


# ===== 路由 =====
@app.get("/health")
async def health():
    """健康检查。"""
    return {"status": "ok", "model_loaded": _pipe is not None}


@app.get("/v1/models")
async def list_models():
    """返回可用模型列表。"""
    return {
        "object": "list",
        "data": [
            {
                "id": "hunyuanvideo-i2v",
                "object": "model",
                "owned_by": "xdit",
            }
        ],
    }


@app.post("/v1/videos/generations")
async def generate(req: GenerationRequest, async_mode: bool = Query(default=False)):
    """图生视频接口。

    - 同步模式（默认）：阻塞等待结果返回，适合长超时客户端（HTTP 超时需 > 600s）。
    - 异步模式（async_mode=true）：立即返回 task_id，通过 /v1/tasks/{task_id} 轮询。
    """
    if async_mode:
        task_id = uuid.uuid4().hex
        with _tasks_lock:
            _tasks[task_id] = {
                "task_id": task_id,
                "status": "pending",
                "created_at": time.time(),
                "request": req.model_dump(),
            }
        # 提交到单线程池串行执行
        _infer_executor.submit(run_inference, task_id, req)
        return JSONResponse(
            status_code=202,
            content={"task_id": task_id, "status": "pending"},
        )

    # 同步模式：在线程中执行推理，避免阻塞事件循环
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_do_generate, req), timeout=SYNC_TIMEOUT
        )
        return {"task_id": uuid.uuid4().hex, **result}
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504, detail=f"generation timeout after {SYNC_TIMEOUT}s"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("sync generation failed")
        raise HTTPException(status_code=500, detail=f"generation failed: {e}")


@app.get("/v1/tasks/{task_id}")
async def get_task(task_id: str):
    """查询异步任务状态与结果。

    状态: pending / running / succeeded / failed
    """
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return dict(task)


@app.post("/v1/upload")
async def upload(file: UploadFile = File(...)):
    """上传输入图片，返回 image_url 供 /v1/videos/generations 使用。"""
    ext = os.path.splitext(file.filename or "image.jpg")[1].lower()
    if not ext:
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    with open(path, "wb") as f:
        f.write(content)
    return {
        "image_url": f"{SERVICE_BASE}/files/upload/{filename}",
        "size": len(content),
        "filename": filename,
    }


def main():
    """服务入口，接收 --host / --port 参数。"""
    global SERVICE_HOST, SERVICE_PORT, SERVICE_BASE
    parser = argparse.ArgumentParser(description="xDiT HunyuanVideo-I2V Service")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SERVICE_PORT", "8288")),
        help="监听端口",
    )
    args = parser.parse_args()

    # 用命令行端口覆盖对外 URL 端口（保证生成的文件 URL 可访问）
    SERVICE_PORT = str(args.port)
    SERVICE_BASE = f"http://{SERVICE_HOST}:{SERVICE_PORT}"

    import uvicorn

    logger.info(
        "启动服务: host=%s port=%s model=%s fp8=%s cpu_offload=%s",
        args.host,
        args.port,
        MODEL_ID,
        ENABLE_FP8,
        ENABLE_CPU_OFFLOAD,
    )
    # 单 worker，避免多进程重复加载模型
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", workers=1)


if __name__ == "__main__":
    main()
