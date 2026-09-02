"""剧本与角色 API 路由。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.agents.base import strip_think_tags
from app.agents.character_agent import character_agent
from app.agents.edit_agent import edit_agent
from app.agents.quality_agent import apply_subtitle_fixes, quality_agent, visual_quality_agent
from app.agents.script_agent import script_agent
from app.agents.storyboard_agent import storyboard_agent
from app.agents.subtitle_agent import subtitle_agent
from app.agents.video_agent import video_agent
from app.agents.voice_agent import voice_agent
from app.config import settings
from app.core.progress import progress_tracker
from app.models.schemas import (
    AgentAssistRequest,
    AgentAssistResponse,
    AgentResponse,
    AsyncTaskResponse,
    CharacterAssetUpdateRequest,
    CharacterPreviewRequest,
    CharacterRequest,
    EditRequest,
    MentionResolveRequest,
    PipelineRunRequest,
    PipelineTemplateItem,
    PipelineTemplateListResponse,
    QualityCheckRequest,
    QualityVisualRequest,
    RAGOptimizeRequest,
    RAGOptimizeResponse,
    RerunShotRequest,
    ScriptRequest,
    StoryboardBatchRequest,
    StoryboardRequest,
    SubtitleFixRequest,
    SubtitleRequest,
    VideoBatchRequest,
    VideoRequest,
    VoiceRequest,
)
from app.services.character_library import character_library
from app.services.failure_registry import failure_registry
from app.services.model_gateway import model_gateway
from app.services.pipeline_orchestrator import PipelineOrchestrator, pipeline_orchestrator
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
}


@router.get("/health")
async def health() -> dict:
    """健康检查 + 基础设施状态。"""
    from app.config import settings

    return {
        "status": "ok",
        "version": "0.4.0",
        "llm_base_url": settings.exo_base_url,
        "llm_model": settings.exo_model_glm52,
        "comfyui_workers": {
            "image_hq": settings.comfyui_image_hq,
            "image_fast": settings.comfyui_image_fast,
            "video_a": settings.comfyui_video_a,
            "video_b": settings.comfyui_video_b,
        },
        "services": {
            "h3": {"endpoint": settings.h3_comfyui_url, "enabled": settings.video_backend == "h3"},
            "comfyui_lb": {"endpoint": settings.comfyui_image_hq, "enabled": True},
            "sdxl_image": {"endpoint": settings.comfyui_image_hq, "enabled": settings.image_backend == "sdxl"},
            "ltx25": {"endpoint": settings.ltx_comfyui_url, "enabled": settings.ltx_enabled},
            "cosyvoice": {"endpoint": settings.cosyvoice_endpoint, "enabled": settings.tts_backend == "cosyvoice"},
            "indextts": {"endpoint": settings.indextts_endpoint, "enabled": settings.tts_backend == "indextts"},
            "ai_omni_asr": {"endpoint": settings.ai_omni_asr_endpoint, "enabled": settings.asr_backend == "ai_omni"},
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
        ],
        "downloader_config_loaded": settings.downloader_config is not None,
    }


# ---------------------------------------------------------------------------
# 本地模型网关（DramaClaw litellm/NewAPI 的本地化对等层）
# ---------------------------------------------------------------------------

@router.get("/gateway/capabilities")
async def gateway_capabilities() -> dict:
    """能力注册表：全部本地部署服务，零外部依赖。"""
    return {"capabilities": model_gateway.capabilities_report()}


@router.get("/gateway/health")
async def gateway_health() -> dict:
    """全能力健康报告（TTL 缓存 30s，服务重启后调用 /gateway/health/refresh 强制复测）。"""
    return await model_gateway.health_report()


@router.post("/gateway/health/refresh")
async def gateway_health_refresh() -> dict:
    """失效健康缓存并全量复测（服务重启/运维操作后调用）。"""
    model_gateway.invalidate_health_cache()
    return await model_gateway.health_report()


@router.get("/gateway/metrics")
async def gateway_metrics() -> dict:
    """各能力调用指标（次数/最近延迟/错误数）。"""
    return model_gateway.metrics_report()


# ---------------------------------------------------------------------------
# 失败模式注册表（M25.9 C2，DramaClaw failure_registry 本地化对等）
# ---------------------------------------------------------------------------

@router.get("/verification/failure-modes")
async def list_failure_modes(layer: str | None = None, gate_only: bool = False) -> dict:
    """失败模式清单（可按层/门禁过滤）+ 命中计数。"""
    modes = failure_registry.list_active(layer=layer, gate_only=gate_only)
    return {
        "modes": [m.model_dump() for m in modes],
        "hits": failure_registry.hits(),
    }


@router.post("/verification/failure-modes/{code}/hit")
async def bump_failure_mode_hit(code: str) -> dict:
    """命中计数 +1（质检链路回写 / 人工标注用）。"""
    if failure_registry.get(code) is None:
        raise HTTPException(status_code=404, detail=f"未注册的失败模式: {code}")
    return {"code": code, "hit_count": failure_registry.bump_hit(code)}


@router.put("/verification/failure-modes/{code}")
async def upsert_failure_mode(code: str, fields: dict[str, Any]) -> dict:
    """新增/更新失败模式（白名单字段：layer/detection/prevention_rule/
    correction_template/negative_prompt_clause/gate_enabled）。"""
    try:
        mode = failure_registry.upsert(code, **fields)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return mode.model_dump()


@router.post("/agent/assist", response_model=AgentResponse)
async def agent_assist(request: AgentAssistRequest) -> AgentResponse:
    """通用智能体辅助：根据上下文润色、扩写、精简或改写文本。"""
    start = time.time()
    action_map = {
        "polish": "润色（提升表达质量，保持原意）",
        "expand": "扩写（增加细节与画面感）",
        "shorten": "精简（保留核心信息，压缩篇幅）",
        "rewrite": "改写（用不同方式重新表达）",
    }
    action_desc = action_map.get(request.action, request.action)
    context_map = {
        "script": "剧本创作",
        "character": "角色设定",
        "storyboard": "分镜描述",
        "video": "视频生成提示词",
        "voice": "配音对白",
        "subtitle": "字幕文本",
        "edit": "成片剪辑",
        "quality": "质检报告",
    }
    context_desc = context_map.get(request.context, request.context)

    system_prompt = (
        f"你是一位专业的 {context_desc} 智能体辅助助手。"
        "请严格根据用户请求对文本进行处理，只返回处理后的文本，不要添加解释、前缀、Markdown 代码块。"
        "必须保持原文的核心语义，不引入未提及的新角色、新情节或新场景。"
    )
    user_msg = (
        f"上下文：{context_desc}\n"
        f"动作：{action_desc}\n"
        f"原文：\n{request.text}\n"
    )
    if request.extra_instruction:
        user_msg += f"\n额外要求：{request.extra_instruction}\n"
    user_msg += "\n请直接输出处理后的文本："

    try:
        from app.agents.base import get_shared_llm_client
        client = get_shared_llm_client()
        resp = await client.chat.completions.create(
            model=settings.exo_model_glm52,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.6,
            max_tokens=1500,
        )
        content = resp.choices[0].message.content or request.text
        # 去除思维链（<think>...</think> 或未闭合的思考前缀）
        if content.startswith("<think>") and "</think>" not in content:
            # 思维链未闭合时无有效输出，回退原文
            content = request.text
        else:
            content = strip_think_tags(content)
        # 去除可能的代码块
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:])
            if content.endswith("```"):
                content = content[:-3].strip()
        content = content.strip() or request.text
        return AgentResponse(
            success=True,
            data=AgentAssistResponse(text=content, action=request.action, context=request.context).model_dump(),
            elapsed_seconds=time.time() - start,
        )
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("智能体辅助失败: %s", e)
        return AgentResponse(
            success=False,
            error=f"智能体辅助失败: {e}",
            elapsed_seconds=time.time() - start,
        )


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


@router.get("/pipeline/templates", response_model=PipelineTemplateListResponse)
async def pipeline_templates(category: str | None = None) -> PipelineTemplateListResponse:
    """M25.3 模板库：返回 genre_tropes 知识库的类型片叙事镜头模板。

    供 ScriptModal「模板起手」选择模板后预填创意输入框。
    category 为空时默认返回 genre_trope 类别；KB 缺失/加载失败时兜底返回空列表（不报错）。
    """
    try:
        raw_templates = rag_service.get_templates(category=category)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("模板库读取失败（KB 缺失兜底为空列表）: %s", e)
        raw_templates = []

    items = [
        PipelineTemplateItem(
            id=t.get("id", ""),
            title=t.get("title", ""),
            category=t.get("category", "genre_trope"),
            tags=t.get("tags", []),
            summary=(t.get("content", "")[:200] + ("…" if len(t.get("content", "")) > 200 else "")),
            content=t.get("content", ""),
        )
        for t in raw_templates
    ]
    categories = sorted({t.get("category", "genre_trope") for t in raw_templates})
    return PipelineTemplateListResponse(templates=items, total=len(items), categories=categories)


@router.get("/models/registry")
async def models_registry() -> dict[str, Any]:
    """模型注册表（任务3：打通下载器↔工作台）。

    融合 lora_manifest.json（trigger_words/weight）与下载器 models.json
    （已下载事实），标注每个 LoRA 的 downloaded 状态，供前端模型卡片与
    工作流/视频 Agent 消费。下载器 models.json 缺失时按全未下载处理（不报错）。
    """
    try:
        from app.services.model_registry_service import model_registry_service

        return model_registry_service.get_registry()
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("模型注册表失败: %s", e)
        raise HTTPException(status_code=500, detail=f"模型注册表失败: {e}") from e


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


# ============================================================================
# 角色资产库（外观锁定卡，跨集/跨镜一致性）
# ============================================================================


@router.get("/character-library/list")
async def list_character_assets() -> dict:
    """列出资产库全部角色（按更新时间倒序）。"""
    assets = character_library.list()
    return {"success": True, "data": [a.model_dump() for a in assets], "total": len(assets)}


@router.get("/character-library/{character_id}")
async def get_character_asset(character_id: str) -> dict:
    """获取单个角色资产。"""
    asset = character_library.get(character_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"角色资产不存在: {character_id}")
    return {"success": True, "data": asset.model_dump()}


@router.put("/character-library/{character_id}")
async def update_character_asset(character_id: str, request: CharacterAssetUpdateRequest) -> dict:
    """局部更新角色资产（外观锁定卡/锁定状态/描述等白名单字段）。"""
    asset = character_library.update(
        character_id, **request.model_dump(exclude_none=True)
    )
    if asset is None:
        raise HTTPException(status_code=404, detail=f"角色资产不存在: {character_id}")
    return {"success": True, "data": asset.model_dump()}


@router.delete("/character-library/{character_id}")
async def delete_character_asset(character_id: str) -> dict:
    """删除角色资产。"""
    if not character_library.delete(character_id):
        raise HTTPException(status_code=404, detail=f"角色资产不存在: {character_id}")
    return {"success": True, "data": {"deleted": character_id}}


@router.post("/assets/resolve-mentions")
async def resolve_asset_mentions(request: MentionResolveRequest) -> dict:
    """@角色提及解析（M24.1 主体库 @引用可视化）。

    提取文本中全部 `@角色名` 提及，按 精确 → 大小写不敏感 → 模糊包含
    三级匹配角色资产库，返回每个提及的 角色ID/角色名/定妆照 URL/外观锁定卡，
    以及展开文本（锁定角色的外观锁定卡拼入前缀段）与定妆照 URL 列表
    （可直接作为 VideoRequest.reference_images）。

    错误：400 提及数量超限 / 422 入参校验失败（空文本、超万字符）/ 500 解析异常。
    """
    from app.services.mention_service import resolve_mentions

    try:
        data = resolve_mentions(request.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("@提及解析失败: %s", e)
        raise HTTPException(status_code=500, detail=f"@提及解析失败: {e}") from e
    return {"success": True, "data": data}


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
    """视频 Agent：分镜关键帧 → 视频片段（MiniMax-H3）。"""
    return await video_agent.execute(request)


@router.post("/video/generate_batch", response_model=AgentResponse)
async def generate_video_batch(request: VideoBatchRequest) -> AgentResponse:
    """视频 Agent 批量接口：多个场景并行生成视频，分散到多个 GPU。"""
    return await video_agent.batch_execute(request)


@router.post("/video/rerun-shot", response_model=AgentResponse)
async def rerun_video_shot(request: RerunShotRequest) -> AgentResponse:
    """单镜头锚点重拍（M25.1）。

    从 output/pipeline/{project_id}/shot_params.json 恢复该镜头的参数快照
    （prompt/seed/engine/lock_params/reference_images 等），仅重跑目标镜头：
    - `seed` 非空时覆盖快照种子（换 seed 重拍），否则沿用快照锁定值
    - `override_prompt` 非空时替换快照提示词
    - 成功后回写快照该镜头的 video_url/status；失败仅返回错误，其余镜头不受影响

    错误：404 快照或镜头不存在 / 422 入参校验失败。
    """
    snapshot = PipelineOrchestrator.load_shot_params(request.project_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"镜头参数快照不存在: {request.project_id}（需先跑过视频生成步骤）",
        )
    shot = next(
        (s for s in snapshot.get("shots", []) if s.get("scene_id") == request.scene_id),
        None,
    )
    if shot is None:
        raise HTTPException(
            status_code=404,
            detail=f"快照中无镜头 scene_id={request.scene_id}",
        )

    # 快照重建请求（剔除落盘附加字段，Pydantic 忽略未知键）
    req_data = {k: v for k, v in shot.items() if k not in ("status", "video_url", "rerun_at")}
    req = VideoRequest(**req_data)
    if request.reseed:
        req.seed = None  # 换 seed 重拍：置 None 由 Agent 随机
    elif request.seed is not None:
        req.seed = request.seed
    if request.override_prompt.strip():
        req.prompt = request.override_prompt.strip()

    response = await video_agent.execute(req)
    # 成功才回写快照；失败不动快照（保持上次成功产物，失败隔离）
    if response.success and response.data:
        PipelineOrchestrator.update_shot_result(
            request.project_id,
            request.scene_id,
            video_url=str(response.data.get("video_url", "")),
            status="success",
            seed_used=req.seed,
        )
    return response


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


@router.post("/pipeline/run", response_model=AsyncTaskResponse)
async def run_pipeline(request: PipelineRunRequest) -> AsyncTaskResponse:
    """M7 全链路自动编排：一句话创意 → 短剧成片（后台异步执行）。

    步骤：剧本 → 角色定妆照 → 分镜 → 视频 → 配音 → 字幕 → 剪辑 → 质检。
    返回 task_id，通过 poll_url 轮询或 stream_url SSE 订阅进度。
    """
    task_id = pipeline_orchestrator.start(request)
    base_url = f"http://localhost:{settings.backend_port}"
    return AsyncTaskResponse(
        task_id=task_id,
        agent="pipeline",
        status="pending",
        poll_url=f"{base_url}/api/progress/{task_id}",
        stream_url=f"{base_url}/api/progress/{task_id}/stream",
    )


@router.get("/pipeline/status/{task_id}")
async def pipeline_status(task_id: str) -> dict:
    """查询全链路任务状态（含各步骤结果报告）。"""
    record = progress_tracker.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"任务不存在或已过期: {task_id}")
    return {
        "success": True,
        "data": {
            "task_id": record.task_id,
            "status": record.status,
            "percent": record.percent,
            "message": record.message,
            "error": record.error,
            "result": record.result,
            "updated_at": record.updated_at,
        },
    }


@router.post("/pipeline/cancel/{task_id}")
async def cancel_pipeline(task_id: str) -> dict:
    """取消全链路任务（步骤间生效，长步骤完成后停止）。"""
    if not pipeline_orchestrator.cancel(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在或已结束: {task_id}")
    return {"success": True, "data": {"task_id": task_id, "cancel_requested": True}}


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
        # 支持 progress_callback 的 Agent：video
        # 其他 Agent 忽略未知参数
        if agent_name == "video":
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
