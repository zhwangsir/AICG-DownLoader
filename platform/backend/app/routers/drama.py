"""剧本与角色 API 路由。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.agents.character_agent import character_agent
from app.agents.edit_agent import edit_agent
from app.agents.lip_sync_agent import lip_sync_agent
from app.agents.postprocess_agent import postprocess_agent
from app.agents.quality_agent import apply_subtitle_fixes, quality_agent, visual_quality_agent
from app.agents.script_agent import script_agent
from app.agents.storyboard_agent import storyboard_agent
from app.agents.subtitle_agent import subtitle_agent
from app.agents.video_agent import video_agent
from app.agents.voice_agent import voice_agent
from app.config import settings
from app.core.progress import progress_tracker
from app.models.schemas import (
    AgentResponse,
    AsyncTaskResponse,
    CharacterPreviewRequest,
    CharacterRequest,
    EditRequest,
    LipSyncRequest,
    PostprocessRequest,
    QualityCheckRequest,
    QualityVisualRequest,
    RAGOptimizeRequest,
    RAGOptimizeResponse,
    ScriptRequest,
    StoryboardBatchRequest,
    StoryboardRequest,
    SubtitleFixRequest,
    SubtitleRequest,
    VideoBatchRequest,
    VideoRequest,
    VoiceRequest,
)
from app.services.rag_service import rag_service

router = APIRouter(prefix="/api/drama", tags=["drama"])


_AGENT_REGISTRY: dict[str, tuple[Any, type]] = {
    "script": (script_agent, ScriptRequest),
    "character": (character_agent, CharacterRequest),
    "storyboard": (storyboard_agent, StoryboardRequest),
    "video": (video_agent, VideoRequest),
    "voice": (voice_agent, VoiceRequest),
    "subtitle": (subtitle_agent, SubtitleRequest),
    "edit": (edit_agent, EditRequest),
    # P4.4: 唇形同步 + 后处理 Agent
    "lipsync": (lip_sync_agent, LipSyncRequest),
    "postprocess": (postprocess_agent, PostprocessRequest),
}


@router.get("/health")
async def health() -> dict:
    """健康检查 + 基础设施状态。"""
    from app.config import settings

    return {
        "status": "ok",
        "version": "0.11.0",
        "exo_base_url": settings.exo_base_url,
        "exo_model": settings.exo_model_glm52,
        "comfyui_workers": {
            "image_hq": settings.comfyui_image_hq,
            "image_fast": settings.comfyui_image_fast,
            "video_a": settings.comfyui_video_a,
            "video_b": settings.comfyui_video_b,
        },
        "p4_services": {
            "xdit_video": {"endpoint": settings.xdit_endpoint, "enabled": settings.video_backend == "xdit"},
            "hunyuanimage": {"endpoint": settings.hunyuanimage_endpoint, "enabled": settings.image_backend == "hunyuanimage"},
            "latentsync": {"endpoint": settings.latentsync_endpoint, "enabled": settings.lip_sync_enabled},
            "video_enhance": {"endpoint": settings.postprocess_endpoint, "enabled": settings.postprocess_enabled},
            "deepfilternet": {"endpoint": settings.deepfilternet_endpoint, "enabled": settings.postprocess_audio_denoise_enabled},
            "indextts": {"endpoint": settings.indextts_endpoint, "enabled": settings.tts_backend == "indextts"},
            "qwen3_asr": {"endpoint": settings.qwen3_asr_endpoint, "enabled": settings.asr_backend == "qwen3_asr"},
        },
        "rag": {
            "enabled": settings.rag_optimize_enabled,
            "embed_model": settings.rag_embed_model,
            "top_k": settings.rag_top_k,
        },
        "agents": [
            "script_agent",
            "character_agent",
            "storyboard_agent",
            "video_agent",
            "voice_agent",
            "subtitle_agent",
            "edit_agent",
            "quality_agent",
            "visual_quality_agent",
            "lip_sync_agent",
            "postprocess_agent",
        ],
        "downloader_config_loaded": settings.downloader_config is not None,
    }


@router.post("/rag/optimize", response_model=RAGOptimizeResponse)
async def rag_optimize(request: RAGOptimizeRequest) -> RAGOptimizeResponse:
    """RAG 提示词优化：将用户描述转换为高质量英文生成提示词。"""
    try:
        result = await rag_service.optimize_prompt(
            user_prompt=request.user_prompt,
            domain=request.domain,
            style_hint=request.style_hint or None,
            extra_instruction=request.extra_instruction or None,
        )
        return RAGOptimizeResponse(**result)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("RAG 优化接口失败: %s", e)
        raise HTTPException(status_code=500, detail=f"RAG 优化失败: {e}") from e


@router.get("/rag/styles")
async def rag_styles() -> list[dict[str, Any]]:
    """返回内置风格库列表，供前端下拉选择。"""
    try:
        return rag_service.get_styles()
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("RAG 风格列表失败: %s", e)
        raise HTTPException(status_code=500, detail=f"RAG 风格列表失败: {e}") from e


@router.post("/script/generate", response_model=AgentResponse)
async def generate_script(request: ScriptRequest) -> AgentResponse:
    """剧本 Agent：一句话创意 → JSON 剧本。"""
    return await script_agent.execute(request)


@router.post("/character/generate", response_model=AgentResponse)
async def generate_character(request: CharacterRequest) -> AgentResponse:
    """角色 Agent：剧本人物 → 角色定妆照（三视图）。"""
    return await character_agent.execute(request)


@router.post("/character/preview", response_model=AgentResponse)
async def preview_character(request: CharacterPreviewRequest) -> AgentResponse:
    """角色生成预览：联网搜索 + 生成提示词，不生成图片。

    前端展示并编辑后，将 edited_prompts 传回 /character/generate 生成定妆照。
    """
    return await character_agent.preview(request)


@router.post("/storyboard/generate", response_model=AgentResponse)
async def generate_storyboard(request: StoryboardRequest) -> AgentResponse:
    """分镜 Agent：剧本场景 → 分镜关键帧图片。"""
    return await storyboard_agent.execute(request)


@router.post("/storyboard/generate_batch", response_model=AgentResponse)
async def generate_storyboard_batch(request: StoryboardBatchRequest) -> AgentResponse:
    """分镜 Agent 批量接口：多个场景并行生成分镜关键帧，分散到多个 GPU。"""
    return await storyboard_agent.batch_execute(request)


@router.post("/video/generate", response_model=AgentResponse)
async def generate_video(request: VideoRequest) -> AgentResponse:
    """视频 Agent：分镜关键帧 → 视频片段（Wan 2.2 I2V）。"""
    return await video_agent.execute(request)


@router.post("/video/generate_batch", response_model=AgentResponse)
async def generate_video_batch(request: VideoBatchRequest) -> AgentResponse:
    """视频 Agent 批量接口：多个场景并行生成视频，分散到多个 GPU。"""
    return await video_agent.batch_execute(request)


@router.post("/voice/generate", response_model=AgentResponse)
async def generate_voice(request: VoiceRequest) -> AgentResponse:
    """配音 Agent：台词 → edge-tts → 多角色语音音频。"""
    return await voice_agent.execute(request)


@router.post("/subtitle/generate", response_model=AgentResponse)
async def generate_subtitle(request: SubtitleRequest) -> AgentResponse:
    """字幕 Agent：音频 → faster-whisper ASR → SRT 字幕。"""
    return await subtitle_agent.execute(request)


@router.post("/edit/compose", response_model=AgentResponse)
async def compose_video(request: EditRequest) -> AgentResponse:
    """剪辑 Agent：视频片段 + 配音 + 字幕 → 完整短剧成片。"""
    return await edit_agent.execute(request)


@router.post("/lipsync/generate", response_model=AgentResponse)
async def generate_lip_sync(request: LipSyncRequest) -> AgentResponse:
    """唇形同步 Agent：视频 + 配音音频 → 口型对齐视频（LatentSync 1.6）。

    P4.4: 受 settings.lip_sync_enabled 总开关控制，默认关闭。
    失败时自动降级返回原视频，不影响成片流程。
    """
    return await lip_sync_agent.execute(request)


@router.post("/postprocess/generate", response_model=AgentResponse)
async def generate_postprocess(request: PostprocessRequest) -> AgentResponse:
    """后处理 Agent：超分 + 插帧 + 修复 + 降噪 + H.265 编码。

    P4.4: 受 settings.postprocess_enabled 总开关控制，默认关闭。
    单步开关在 settings 中独立控制，单步失败不阻断整体流程（best-effort）。
    """
    return await postprocess_agent.execute(request)


@router.post("/quality/check", response_model=AgentResponse)
async def check_quality(request: QualityCheckRequest) -> AgentResponse:
    """质检 Agent：剧本 + 字幕 → 结构化质检报告。"""
    return await quality_agent.execute(request)


@router.post("/quality/visual", response_model=AgentResponse)
async def check_visual_quality(request: QualityVisualRequest) -> AgentResponse:
    """视觉质检 Agent：视频 → 抽帧 → VLM 检查。"""
    return await visual_quality_agent.execute(request)


@router.post("/quality/apply_subtitle_fix", response_model=AgentResponse)
async def apply_quality_subtitle_fix(request: SubtitleFixRequest) -> AgentResponse:
    """字幕闭环：基于质检 issues 自动修正 ASR 错别字，回写 SRT 文件。

    流程：质检报告 → 提取 (wrong→right) 修正对 → 替换字幕文本 → 重建 SRT → 覆盖原文件。
    仅处理 category=subtitle 的问题，避免误改非字幕内容。
    """
    import time as _time

    start = _time.time()
    try:
        result = apply_subtitle_fixes(request)
        return AgentResponse(
            success=True,
            data=result.model_dump(),
            elapsed_seconds=_time.time() - start,
        )
    except Exception as e:
        return AgentResponse(
            success=False,
            error=f"字幕回写修正失败: {e}",
            elapsed_seconds=_time.time() - start,
        )


async def _run_agent_task(agent_name: str, agent: Any, request: Any, task_id: str) -> None:
    """后台运行 Agent，并通过进度跟踪器推送状态。"""
    progress_tracker.update(
        task_id,
        status="running",
        percent=0,
        message=f"{agent_name} 开始执行",
    )

    def progress_callback(percent: int, message: str) -> None:
        progress_tracker.update(
            task_id,
            status="running",
            percent=percent,
            message=message,
        )

    try:
        # 支持 progress_callback 的 Agent：video / lipsync / postprocess
        # 其他 Agent 忽略未知参数
        if agent_name in ("video", "lipsync", "postprocess"):
            response = await agent.execute(request, progress_callback=progress_callback)
        else:
            response = await agent.execute(request)

        if response.success:
            progress_tracker.update(
                task_id,
                status="completed",
                percent=100,
                message=f"{agent_name} 完成",
                result=response.data if isinstance(response.data, dict) else None,
            )
        else:
            progress_tracker.update(
                task_id,
                status="failed",
                percent=100,
                message=response.error or "执行失败",
                error=response.error,
            )
    except Exception as e:
        progress_tracker.update(
            task_id,
            status="failed",
            percent=100,
            message=str(e),
            error=str(e),
        )


@router.post("/{agent}/generate_async", response_model=AsyncTaskResponse)
async def generate_async(agent: str, request: dict[str, Any], background_tasks: BackgroundTasks) -> AsyncTaskResponse:
    """异步创建 Agent 任务，返回 task_id 用于 SSE/轮询进度。"""
    if agent not in _AGENT_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知 Agent: {agent}")

    agent_instance, request_model = _AGENT_REGISTRY[agent]
    try:
        validated = request_model(**request)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"请求参数校验失败: {e}") from e

    task_id = progress_tracker.create(agent, message=f"{agent} 任务已创建")
    background_tasks.add_task(_run_agent_task, agent, agent_instance, validated, task_id)

    base_url = f"http://localhost:{settings.backend_port}"
    return AsyncTaskResponse(
        task_id=task_id,
        agent=agent,
        status="pending",
        poll_url=f"{base_url}/api/progress/{task_id}",
        stream_url=f"{base_url}/api/progress/{task_id}/stream",
    )
