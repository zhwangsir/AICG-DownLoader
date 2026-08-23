"""文件下载端点（带路径遍历防护）。"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

logger = logging.getLogger("novelvideo.api.files")

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import ProjectResolution, resolve_project_scope

router = APIRouter()


def _resolve_project_file(resolved: ProjectResolution, file_path: str) -> Path:
    project_dir = resolved.project_dir
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    requested = (project_dir / file_path).resolve()
    if not requested.is_relative_to(project_dir.resolve()):
        raise HTTPException(status_code=403, detail="Access denied")

    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return requested


def _serve_or_redirect_to_oss(requested: Path, *, as_download: bool):
    """Serve a resolved project file, preferring a 302 to a presigned OSS URL.

    OUTPUT_DIR is an ossfs mount, so every file here already exists in OSS. When
    OSS delivery is enabled and the object is readable, redirect the browser
    straight to OSS so the edge router/pod stop streaming media bytes under load
    — the heavy transfer happens on a direct browser↔OSS connection. The 302 is
    marked ``no-store`` so the edge router does not cache the short-lived signed
    URL. Falls back to a local ``FileResponse`` whenever OSS delivery is disabled
    or the object is not yet readable in OSS (ossfs write-back lag), so behaviour
    degrades gracefully and same-origin frontend URLs keep working.
    """
    presigned = None
    try:
        if as_download:
            from novelvideo import config
            from novelvideo.utils.oss_client import maybe_presign_existing_output

            if getattr(config, "DOWNLOAD_VIA_OSS", False):
                presigned = maybe_presign_existing_output(requested)
        else:
            from novelvideo.utils.oss_client import maybe_presign_static

            presigned = maybe_presign_static(requested, requested.stat().st_mtime_ns)
    except Exception:
        logger.debug("OSS presign skipped for %s", requested, exc_info=True)
        presigned = None

    if presigned:
        return RedirectResponse(
            url=presigned,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    if as_download:
        return FileResponse(path=str(requested), filename=requested.name)
    return FileResponse(path=str(requested))


@router.get("/projects/{project}/files/{file_path:path}")
async def download_file(
    project: str,
    file_path: str,
    user: dict = Depends(get_api_user),
):
    """下载项目内的生成文件。

    路径相对于 output/{username}/{project}/，
    自动防止目录遍历攻击。
    """
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    requested = _resolve_project_file(resolved, file_path)

    return _serve_or_redirect_to_oss(requested, as_download=True)


@router.get("/projects/{project}/media/{file_path:path}")
async def preview_file(
    project: str,
    file_path: str,
    user: dict = Depends(get_api_user),
):
    """预览项目内媒体文件。

    与 /files 使用同样的鉴权和路径防护，但返回 inline 响应，供 React 的
    <img>/<video>/<audio> 直接使用，避免裸 /static 依赖 NiceGUI session。
    """
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    requested = _resolve_project_file(resolved, file_path)
    return _serve_or_redirect_to_oss(requested, as_download=False)


async def preview_project_media_file(project: str, file_path: str, user: dict):
    """Serve a project media file for non-/api routes such as /static/projects."""
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    requested = _resolve_project_file(resolved, file_path)
    return _serve_or_redirect_to_oss(requested, as_download=False)
