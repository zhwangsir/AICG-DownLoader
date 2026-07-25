"""HunyuanImage 2.1 FP8 图像生成服务 — OpenAI 兼容 FastAPI 包装层。

部署目标: workstation (192.168.71.127) GPU3 (NVIDIA RTX PRO 6000 96GB)
端口: 8600, 显存: ~24GB (FP8)

接口:
- POST /v1/images/generations  提交生成任务 (默认异步返回 task_id; ?sync=true 同步阻塞返回 OpenAI 格式)
- GET  /v1/tasks/{task_id}      查询异步任务状态与结果
- GET  /v1/files/{name}         下载已生成的图片 (response_format=url 时使用)
- GET  /v1/models               返回模型列表 (OpenAI 兼容)
- GET  /health                  健康检查

注意: HunyuanImage 2.1 仅支持 2K 固定分辨率组合, 请求 size 不匹配时自动选最接近组合并记录 warning。
模型懒加载: 首次请求时才加载 pipeline, 避免启动时卡死; 加载过程用 threading.Lock 保护。
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hunyuanimage-serve")

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------
MODEL_ID = "hunyuanimage-v2.1"
OUTPUT_DIR = "/workspace/io/output"

# HunyuanImage 2.1 支持的 2K 固定分辨率组合 (width, height)
SUPPORTED_RESOLUTIONS: list[tuple[int, int]] = [
    (2048, 2048),
    (2240, 2240),
    (2560, 1536),
    (1536, 2560),
    (2304, 1728),
    (1728, 2304),
    (2688, 1536),
    (1536, 2688),
]

# ---------------------------------------------------------------------------
# 全局状态: 模型懒加载 + 异步任务表
# ---------------------------------------------------------------------------
_pipe: Any = None
_pipe_lock = threading.Lock()       # 保护模型加载, 避免并发重复加载
_tasks: dict[str, dict[str, Any]] = {}  # task_id -> 任务信息
_tasks_lock = threading.Lock()      # 保护任务表并发读写


def _load_pipe() -> Any:
    """懒加载 HunyuanImagePipeline, 使用锁保护避免并发重复加载。"""
    global _pipe
    with _pipe_lock:
        if _pipe is not None:
            return _pipe
        logger.info("首次请求, 开始加载 HunyuanImagePipeline (FP8) ...")
        from hyimage.pipelines import HunyuanImagePipeline  # 延迟导入, 避免启动时卡死

        _pipe = HunyuanImagePipeline.from_pretrained(
            model_name=MODEL_ID, use_fp8=True
        ).to("cuda")
        logger.info("HunyuanImagePipeline 加载完成。")
        return _pipe


def _select_resolution(width: int, height: int) -> tuple[tuple[int, int], str]:
    """选择最接近请求尺寸的 2K 支持分辨率, 返回 ((w, h), warning)。"""
    target_area = width * height
    target_ar = (width / height) if height else 1.0

    best = (width, height)
    best_score = float("inf")
    for (w, h) in SUPPORTED_RESOLUTIONS:
        area = w * h
        ar = w / h
        # 评分: 长宽比差异为主, 归一化面积差异为辅
        ar_diff = abs(ar - target_ar)
        area_diff = (abs(area - target_area) / target_area) if target_area else 0.0
        score = ar_diff * 2.0 + area_diff
        if score < best_score:
            best_score = score
            best = (w, h)

    warning = ""
    if best != (width, height):
        warning = (
            f"请求尺寸 {width}x{height} 非原生 2K 组合, "
            f"已自动选用最接近的 {best[0]}x{best[1]}"
        )
        logger.warning(warning)
    return best, warning


def _extract_image(image: Any):
    """从 pipeline 返回值提取单张 PIL.Image。

    HunyuanImagePipeline 可能返回 PIL.Image / list[PIL.Image] / list[dict], 这里统一处理。
    """
    from PIL import Image  # 延迟导入

    if isinstance(image, Image.Image):
        return image
    if isinstance(image, (list, tuple)) and image:
        first = image[0]
        if isinstance(first, Image.Image):
            return first
        if isinstance(first, dict):
            for key in ("image", "img", "pil"):
                val = first.get(key)
                if isinstance(val, Image.Image):
                    return val
    raise RuntimeError(f"无法从 pipeline 返回值提取图像: {type(image)!r}")


def _update_task(task_id: str, **fields: Any) -> None:
    """更新任务表中的字段 (线程安全)。"""
    with _tasks_lock:
        if task_id not in _tasks:
            return
        _tasks[task_id].update(fields)
        _tasks[task_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


def _run_inference(
    task_id: str,
    prompt: str,
    width: int,
    height: int,
    n: int,
    response_format: str,
    seed: int | None,
) -> None:
    """后台线程执行推理, 完成后更新任务表。"""
    try:
        _update_task(task_id, status="processing")
        pipe = _load_pipe()

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        ts = int(time.time())
        results: list[dict[str, str]] = []

        for i in range(max(1, n)):
            # seed 为 None 时每张图随机; 否则用调用方指定种子
            cur_seed = (
                seed if seed is not None
                else int.from_bytes(os.urandom(4), "big")
            )
            logger.info(
                "task=%s 生成第 %d/%d 张: prompt=%r size=%dx%d seed=%d",
                task_id, i + 1, n, prompt[:50], width, height, cur_seed,
            )
            image = pipe(
                prompt=prompt,
                width=width,
                height=height,
                use_reprompt=False,
                use_refiner=True,
                num_inference_steps=50,
                guidance_scale=3.5,
                shift=5,
                seed=cur_seed,
            )
            pil_img = _extract_image(image)

            fname = f"{task_id}_{i}_{ts}.png"
            fpath = os.path.join(OUTPUT_DIR, fname)
            pil_img.save(fpath, format="PNG")
            logger.info("task=%s 已保存: %s", task_id, fpath)

            item: dict[str, str] = {}
            if response_format == "url":
                item["url"] = f"/v1/files/{fname}"
            else:
                buf = BytesIO()
                pil_img.save(buf, format="PNG")
                item["b64_json"] = base64.b64encode(buf.getvalue()).decode("ascii")
            results.append(item)

        _update_task(task_id, status="succeeded", result=results)
        logger.info("task=%s 完成, 共 %d 张", task_id, len(results))
    except Exception as exc:  # noqa: BLE001 - 推理线程需捕获所有异常并记录
        logger.exception("task=%s 推理失败", task_id)
        _update_task(task_id, status="failed", error=str(exc))


# ---------------------------------------------------------------------------
# Pydantic 请求模型
# ---------------------------------------------------------------------------
class ImageGenerationRequest(BaseModel):
    prompt: str
    model: str = MODEL_ID
    size: str = "2048x2048"
    n: int = Field(default=1, ge=1, le=4)
    response_format: str = "b64_json"   # "b64_json" 或 "url"
    seed: int | None = None
    negative_prompt: str = ""           # 预留字段 (HunyuanImage 2.1 通过 prompt 控制)


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title="HunyuanImage 2.1 FP8 Server", version="2.1-fp8")


@app.get("/health")
def health() -> dict[str, Any]:
    """健康检查。"""
    return {"status": "ok", "model": MODEL_ID, "loaded": _pipe is not None}


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    """返回模型列表 (OpenAI 兼容)。"""
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "tencent-hunyuan",
            }
        ],
    }


@app.post("/v1/images/generations")
def create_image_task(
    req: ImageGenerationRequest,
    sync: bool = Query(default=False, description="true=同步阻塞返回 OpenAI 格式; 默认 false=异步返回 task_id"),
) -> Any:
    """提交图像生成任务。

    - 默认 (异步): 立即返回 {"task_id": ..., "status": "pending"},
      通过 GET /v1/tasks/{task_id} 查询结果。
    - ?sync=true (同步): 阻塞等待完成, 返回 OpenAI 兼容格式
      {"created": ..., "data": [{"b64_json"|"url": ...}]}。
    """
    # 解析请求 size (如 "2048x2048")
    try:
        w_str, h_str = req.size.lower().split("x")
        width, height = int(w_str), int(h_str)
    except (ValueError, AttributeError):
        width, height = 2048, 2048

    # 自动匹配最接近的 2K 原生分辨率
    (width, height), warning = _select_resolution(width, height)

    if req.response_format not in ("b64_json", "url"):
        req.response_format = "b64_json"

    task_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with _tasks_lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "prompt": req.prompt,
            "size": f"{width}x{height}",
            "n": req.n,
            "response_format": req.response_format,
            "warning": warning,
            "created_at": now,
            "updated_at": now,
        }

    # 启动后台推理线程 (daemon=True, 随主进程退出)
    worker = threading.Thread(
        target=_run_inference,
        args=(task_id, req.prompt, width, height, req.n, req.response_format, req.seed),
        daemon=True,
    )
    worker.start()

    if sync:
        # 同步模式: 轮询任务状态直到完成
        while True:
            with _tasks_lock:
                task = dict(_tasks.get(task_id, {}))
            if task.get("status") in ("succeeded", "failed"):
                break
            time.sleep(0.5)
        if task.get("status") == "failed":
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": task.get("error", "unknown"),
                        "type": "inference_error",
                    }
                },
            )
        return {
            "created": int(time.time()),
            "data": task.get("result", []),
        }

    # 异步模式: 立即返回 task_id
    return {"task_id": task_id, "status": "pending", "created_at": now}


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    """查询异步任务状态与结果。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        task = dict(task)

    resp: dict[str, Any] = {
        "task_id": task["task_id"],
        "status": task["status"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }
    if task.get("warning"):
        resp["warning"] = task["warning"]
    if task["status"] == "succeeded":
        # OpenAI 兼容的 data 字段
        resp["data"] = task.get("result", [])
    elif task["status"] == "failed":
        resp["error"] = {
            "message": task.get("error", "unknown"),
            "type": "inference_error",
        }
    return resp


@app.get("/v1/files/{name}")
def get_file(name: str) -> FileResponse:
    """下载已生成的图片文件 (response_format=url 时返回的 URL 指向此接口)。"""
    # 防路径穿越
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="invalid file name")
    fpath = os.path.join(OUTPUT_DIR, name)
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(fpath, media_type="image/png", filename=name)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="HunyuanImage 2.1 FP8 服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8600, help="监听端口")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logger.info("启动 HunyuanImage 服务: %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
