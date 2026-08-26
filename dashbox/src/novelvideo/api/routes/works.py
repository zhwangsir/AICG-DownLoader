"""作品库路由：样本视频矩阵画廊（套用 ToIV 作品库形态）。

- GET /works：列表（R18 条目仅在 R18 确认开启后返回）
- GET /works/{id}/media：成片文件（R18 未开启时 403）
- GET /works/{id}/cover：封面（R18 未开启时 403）
- POST /works/refresh：清缓存重扫（生成期间前端手动刷新）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from novelvideo.api.auth import get_api_user
from novelvideo.model_library import nsfw_status
from novelvideo.works_library import get_work, invalidate_cache, list_works, work_media_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/works", tags=["works"])


@router.get("")
async def api_list_works(
    category: str = "",
    feature: str = "",
    q: str = "",
    user: dict = Depends(get_api_user),
) -> dict[str, Any]:
    items = list_works(category=category, feature=feature, q=q)
    return {"ok": True, "data": {"items": items, "total": len(items)}}


@router.post("/refresh")
async def api_refresh_works(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    invalidate_cache()
    items = list_works()
    return {"ok": True, "data": {"total": len(items)}}


@router.get("/{work_id}/media")
async def api_work_media(work_id: str, user: dict = Depends(get_api_user)) -> FileResponse:
    return _serve_media(work_id, "video")


@router.get("/{work_id}/cover")
async def api_work_cover(work_id: str, user: dict = Depends(get_api_user)) -> FileResponse:
    return _serve_media(work_id, "cover")


def _serve_media(work_id: str, kind: str) -> FileResponse:
    work = get_work(work_id)
    if work is None:
        raise HTTPException(404, "作品不存在")
    if work.get("nsfw") and not nsfw_status().get("nsfw_enabled"):
        raise HTTPException(403, "R18 未开启，请先在模型库确认年满 18 岁")
    p = work_media_path(work_id, kind)
    if p is None:
        raise HTTPException(404, "媒体文件不存在")
    media_type = "video/mp4" if kind == "video" else "image/png"
    return FileResponse(p, media_type=media_type, filename=p.name)
