"""本地模型库路由：NAS 模型浏览 / Civitai 下载 / NSFW（R18 确认开关）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user
from novelvideo.model_library import (
    DownloadServiceError,
    get_nsfw_marks,
    model_download_service,
    nas_library_service,
    nsfw_status,
    preflight_workflow,
    set_nsfw,
    set_nsfw_mark,
)

router = APIRouter(prefix="/model-library")


class DownloadRequest(BaseModel):
    download_url: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    subdir: str = Field(min_length=1)
    sha256: str | None = None
    nsfw: bool = False


class NsfwSetRequest(BaseModel):
    enabled: bool


class NsfwMarkRequest(BaseModel):
    rel_path: str = Field(min_length=1)
    nsfw: bool | None = None  # true=标 NSFW，false=标 SFW，null=清除覆盖回退关键词


class PreflightRequest(BaseModel):
    workflow: dict[str, Any]


# ---------- NAS 模型库 ----------


@router.get("/models")
def list_models(
    type: str | None = Query(None, description="按类型子目录过滤（checkpoints/loras/...）"),
    q: str | None = Query(None, description="名称/路径模糊搜索"),
    include_nsfw: bool = Query(False),
    refresh: bool = Query(False, description="强制重扫（跳过 TTL 缓存）"),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    nsfw_on = nsfw_status()["nsfw_enabled"]
    data = nas_library_service.list_models(
        type_filter=type,
        query=q,
        include_nsfw=include_nsfw and nsfw_on,
        refresh=refresh,
    )
    return {"ok": True, "data": data}


# ---------- Civitai 搜索与下载 ----------


@router.get("/search")
def search_models(
    q: str = Query("", description="搜索关键词"),
    type: str | None = Query(None, description="Civitai 模型类型（Checkpoint/LORA/...）"),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    include_nsfw = nsfw_status()["nsfw_enabled"]
    try:
        data = model_download_service.civitai_search(
            query=q, model_type=type, limit=limit, include_nsfw=include_nsfw
        )
    except Exception as e:
        raise HTTPException(502, f"Civitai 搜索失败: {e}") from e
    return {"ok": True, "data": data}


@router.get("/downloads")
def list_downloads(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    return {"ok": True, "data": {"items": model_download_service.list_tasks()}}


@router.post("/downloads", status_code=201)
def start_download(
    req: DownloadRequest, user: dict = Depends(get_api_user)
) -> dict[str, Any]:
    try:
        task = model_download_service.start_download(
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
    return {"ok": True, "data": task}


@router.delete("/downloads/{task_id}")
def cancel_download(task_id: str, user: dict = Depends(get_api_user)) -> dict[str, Any]:
    if not model_download_service.cancel(task_id):
        raise HTTPException(404, "任务不存在或已结束")
    return {"ok": True, "data": {"task_id": task_id}}


# ---------- 生成前预检 ----------


@router.post("/preflight")
def preflight(req: PreflightRequest, user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """提取 workflow 内模型文件引用并比对模型库，返回逐项在位/缺失明细。"""
    return {"ok": True, "data": preflight_workflow(req.workflow)}


# ---------- NSFW（R18 确认开关） ----------


@router.get("/nsfw")
def get_nsfw(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    return {"ok": True, "data": nsfw_status()}


@router.post("/nsfw")
def set_nsfw_endpoint(
    req: NsfwSetRequest, user: dict = Depends(get_api_user)
) -> dict[str, Any]:
    return {"ok": True, "data": set_nsfw(req.enabled)}


@router.get("/nsfw/marks")
def list_nsfw_marks(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    marks = get_nsfw_marks()
    return {"ok": True, "data": {"marks": marks, "count": len(marks)}}


@router.post("/nsfw/marks")
def set_nsfw_mark_endpoint(
    req: NsfwMarkRequest, user: dict = Depends(get_api_user)
) -> dict[str, Any]:
    try:
        data = set_nsfw_mark(req.rel_path, req.nsfw)
    except DownloadServiceError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "data": data}
