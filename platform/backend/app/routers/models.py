"""M27 模型库/下载路由：NAS 模型浏览、Civitai 搜索、后台下载任务、NSFW 设置。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    ModelDownloadRequest,
    NsfwPinChangeRequest,
    NsfwSetRequest,
)
from app.services.model_download_service import (
    DownloadServiceError,
    model_download_service,
)
from app.services.nas_library_service import nas_library_service
from app.services.settings_service import SettingsServiceError, settings_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


# ---------- NAS 模型库 ----------


@router.get("/library")
def list_library(
    type: str | None = Query(None, description="按类型子目录过滤（checkpoints/loras/...）"),
    q: str | None = Query(None, description="名称/路径模糊搜索"),
    include_nsfw: bool = Query(False),
    refresh: bool = Query(False, description="强制重扫（跳过 TTL 缓存）"),
):
    """浏览 NAS 模型库（名称/大小/类型/修改日期/NSFW 标记）。"""
    # include_nsfw=true 时仍需设置端已开启才放行（双保险：前端隐藏 + 后端校验）
    nsfw_on = settings_service.nsfw_status()["nsfw_enabled"]
    result = nas_library_service.list_models(
        type_filter=type,
        query=q,
        include_nsfw=include_nsfw and nsfw_on,
        refresh=refresh,
    )
    if result.get("error") and not result.get("items"):
        raise HTTPException(status_code=503, detail=result["error"])
    return result


# ---------- 模型搜索 ----------


@router.get("/search")
def search_models(
    q: str = Query("", description="Civitai 搜索词"),
    type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    include_nsfw: bool = Query(False),
):
    """搜索 Civitai 模型（civitai.red 镜像）。"""
    nsfw_on = settings_service.nsfw_status()["nsfw_enabled"]
    try:
        return model_download_service.civitai_search(
            query=q, model_type=type, limit=limit, include_nsfw=include_nsfw and nsfw_on
        )
    except Exception as e:
        logger.warning("Civitai 搜索失败: %s", e)
        raise HTTPException(502, f"Civitai 搜索失败: {e}") from e


# ---------- 下载任务 ----------


@router.post("/download", status_code=201)
def start_download(req: ModelDownloadRequest):
    """启动后台模型下载（写入 NAS 对应子目录，可选 SHA256 校验）。"""
    try:
        return model_download_service.start_download(
            download_url=req.download_url,
            filename=req.filename,
            subdir=req.subdir,
            sha256=req.sha256,
            nsfw=req.nsfw,
        )
    except DownloadServiceError as e:
        msg = str(e)
        if "NSFW" in msg:
            raise HTTPException(403, msg) from e
        raise HTTPException(400, msg) from e


@router.get("/downloads")
def list_downloads():
    """全部下载任务（按创建时间倒序）。"""
    return {"items": model_download_service.list_tasks()}


@router.get("/downloads/{task_id}")
def get_download(task_id: str):
    task = model_download_service.get_task(task_id)
    if not task:
        raise HTTPException(404, f"任务不存在: {task_id}")
    return task


@router.delete("/downloads/{task_id}")
def cancel_download(task_id: str):
    if not model_download_service.cancel(task_id):
        raise HTTPException(409, "任务不存在或已结束，无法取消")
    return {"task_id": task_id, "status": "cancel_requested"}


# ---------- NSFW 设置 ----------


@settings_router.get("/nsfw")
def get_nsfw():
    """NSFW 状态（开关 + 是否已设 PIN）。不含任何敏感值。"""
    return settings_service.nsfw_status()


@settings_router.post("/nsfw")
def set_nsfw(req: NsfwSetRequest):
    """开启/关闭 NSFW（首次开启需 new_pin 设 PIN，之后开关均需 PIN）。"""
    try:
        return settings_service.set_nsfw(req.enabled, req.pin, req.new_pin)
    except SettingsServiceError as e:
        raise HTTPException(403, str(e)) from e


@settings_router.post("/nsfw/pin")
def change_nsfw_pin(req: NsfwPinChangeRequest):
    """修改 NSFW 管理 PIN（需旧 PIN 验证）。"""
    try:
        return settings_service.change_pin(req.pin, req.new_pin)
    except SettingsServiceError as e:
        raise HTTPException(403, str(e)) from e
