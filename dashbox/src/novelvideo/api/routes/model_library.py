"""本地模型库路由：NAS 模型浏览 / Civitai 下载 / NSFW（R18 确认开关）/ 生图测试台。"""

from __future__ import annotations

import base64
import binascii
import asyncio
import difflib
import functools
import glob
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import make_static_url_for_context
from novelvideo.freezone.paths import output_path_for_job, outputs_dir
from novelvideo.model_library import (
    ARCH_UNSUPPORTED_REASON,
    DownloadServiceError,
    detect_checkpoint_arch,
    get_nsfw_marks,
    get_sdxl_incompatible,
    is_nsfw_name,
    model_download_service,
    nas_library_service,
    nsfw_status,
    preflight_workflow,
    set_nsfw,
    set_nsfw_mark,
    set_sdxl_incompatible,
)
from novelvideo.agents.r18_script_planner import (
    R18ScriptPlanRequest,
    plan_r18_script,
    review_r18_quality,
)
from novelvideo.project_context import require_project_home_node, resolve_project_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-library")

LOCAL_GATEWAY_URL = os.environ.get("DASHBOX_LOCAL_GATEWAY_URL", "http://127.0.0.1:8790")
GATEWAY_TIMEOUT_SECONDS = float(os.environ.get("DASHBOX_GATEWAY_TIMEOUT", "600"))
STUDIO_IMAGE_MAX_BYTES = 20 * 1024 * 1024


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
    # 附带 SDXL 不兼容标记（生成时实测失败的自学习 denylist），picker 禁选用
    incompatible = get_sdxl_incompatible()
    if incompatible:
        for entry in data["items"]:
            if entry.get("name") in incompatible:
                entry["sdxl_incompatible"] = True
                entry["sdxl_incompatible_reason"] = incompatible[entry["name"]]
    # checkpoints 条目附真实架构（读 safetensors header；flux=Flux 链可生成，
    # 其余非常规 sd 由 ARCH_UNSUPPORTED_REASON 给出禁选原因）
    for entry in data["items"]:
        if entry.get("type") == "checkpoints":
            arch = detect_checkpoint_arch(entry["rel_path"])
            entry["arch"] = arch
            if arch in ARCH_UNSUPPORTED_REASON:
                entry["sdxl_incompatible"] = True
                entry["sdxl_incompatible_reason"] = ARCH_UNSUPPORTED_REASON[arch]
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


# ---------- SDXL 不兼容清单（生成失败自学习 denylist） ----------


@router.get("/sdxl-incompatible")
def list_sdxl_incompatible(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    entries = get_sdxl_incompatible()
    return {"ok": True, "data": {"entries": entries, "count": len(entries)}}


class SdxlIncompatibleRequest(BaseModel):
    filename: str = Field(min_length=1)
    reason: str | None = Field(None, description="null=移除（误记纠正）")


@router.post("/sdxl-incompatible")
def set_sdxl_incompatible_endpoint(
    req: SdxlIncompatibleRequest, user: dict = Depends(get_api_user)
) -> dict[str, Any]:
    try:
        entries = set_sdxl_incompatible(req.filename, req.reason)
    except DownloadServiceError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "data": {"entries": entries, "count": len(entries)}}


# ---------- 生图测试台（同源代理 local_gateway，R18 门禁闭环） ----------


class GenerateImageRequest(BaseModel):
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    checkpoint: str = Field(min_length=1)
    size: str = "832x1216"
    project_id: str | None = Field(None, description="提供则把结果落盘为项目媒体并返回 url（画布节点用）")
    reference_url: str | None = Field(None, description="参考图 URL（IPAdapter 锚定，走 images/edits）")


# ---------- 提示词中→英译写（SDXL 文本编码器不识别中文） ----------

# SDXL 读不懂中文：中文角色卡/场景描述直接送 ComfyUI 会丢掉全部人物特征，
# 出图与人物介绍无关。送网关前把含 CJK 的提示词经 LLM 转写为英文 tag 串；
# 纯英文输入（如剧本规划产出的 image_prompt）零开销直通。
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")

PROMPT_TRANSLATOR_INSTRUCTIONS = """# SDXL Prompt Translator

You translate Chinese (or mixed Chinese/English) text-to-image prompts into English
danbooru-style tag strings for SDXL.

Rules:
- Preserve EVERY visual attribute from the input (hair color/style, body type, skin,
  clothing material & color, age, scene elements, lighting, mood). Never drop,
  invent, or soften attributes. NSFW anatomical terms stay explicit.
- Output comma-separated tags only - no sentences, no quotes, no explanations,
  no line breaks.
- Keep any trigger words (e.g. m15510n4ry) that appear in the input verbatim.
- Keep English fragments already present in the input as-is.
- If the input is already pure English tags, return it unchanged."""


@functools.lru_cache(maxsize=1)
def _get_prompt_translator_agent():
    from pydantic_ai import Agent

    from novelvideo.config import get_newapi_text_pydantic_model

    model = get_newapi_text_pydantic_model(
        "R18_PROMPT_TRANSLATOR_MODEL",
        "DC-freezone-story-script-writer-LLM",
    )
    return Agent(
        model,
        system_prompt=PROMPT_TRANSLATOR_INSTRUCTIONS,
        output_type=str,
        name="SDXL Prompt Translator",
    )


async def translate_prompt_to_english(prompt: str) -> str:
    """含中文的提示词 → 英文 SDXL tag（LLM 译写；失败/异常 fail-open 返回原文）。

    译写结果为空或明显短于中文信息量（正常 tag 化后英文长度 ≥ CJK 字数）
    视为失败，回退原文——生图链路永不因翻译阻断。
    """
    text = prompt.strip()
    if not text:
        return prompt
    cjk_count = len(_CJK_RE.findall(text))
    if cjk_count == 0:
        return prompt
    try:
        result = await _get_prompt_translator_agent().run(text)
        translated = (result.output or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("提示词译写失败（fail-open 用原文）: %s", e)
        return prompt
    if translated and len(translated) >= cjk_count:
        return translated
    logger.warning("提示词译写结果异常（过短），回退原文: %r", translated[:80])
    return prompt


async def _forward_to_gateway(payload: dict[str, Any], *, edits: bool = False) -> dict[str, Any]:
    """转发生图请求到 local_gateway（独立函数便于测试注入）。"""
    path = "/v1/images/edits" if edits else "/v1/images/generations"
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_SECONDS) as client:
        resp = await client.post(f"{LOCAL_GATEWAY_URL}{path}", json=payload)
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str(resp.json().get("error", {}).get("message", ""))
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise HTTPException(502, f"生图网关错误({resp.status_code}): {detail}")
    return resp.json()


def _save_b64_to_project(ctx: Any, b64: str) -> dict[str, Any]:
    """b64 PNG 落盘为项目 freezone/_outputs/nsfw_studio/<job>.png，返回 {url, rel_path, size}。"""
    try:
        payload = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(502, "生图网关返回的图片数据无效") from exc
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(502, "生图网关返回的不是 PNG 数据")
    if len(payload) > STUDIO_IMAGE_MAX_BYTES:
        raise HTTPException(413, "生成图片过大")
    project_dir = Path(ctx.output_dir)
    out = output_path_for_job(project_dir, "nsfw_studio", uuid.uuid4().hex[:16])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    rel_path = out.relative_to(project_dir).as_posix()
    return {
        "url": make_static_url_for_context(ctx, rel_path, local_path=out),
        "rel_path": rel_path,
        "size": len(payload),
    }


@router.post("/generate-image")
async def generate_image(
    req: GenerateImageRequest, user: dict = Depends(get_api_user)
) -> dict[str, Any]:
    """NSFW 生图测试台：按 checkpoint 出图（R18 未开启时 NSFW 底模 403）。

    project_id 提供时结果落盘项目媒体（画布 R18 节点回填 imageUrl 用）；
    reference_url 提供时走 IPAdapter 参考图锚定（local_gateway images/edits）。
    """
    if is_nsfw_name(req.checkpoint) and not nsfw_status()["nsfw_enabled"]:
        raise HTTPException(403, "所选底模为 NSFW 内容，请先在模型库开启 R18")
    # 架构探测路由：读 safetensors header 判定家族。Flux 完整 checkpoint 走
    # FluxGuidance 链（local_gateway workflow=flux）；Krea2 unet 在
    # diffusion_models/ 目录，走 UNETLoader 链（workflow=krea2）；不支持架构
    # 直接 422 带原因（不再浪费一次 ComfyUI 执行才失败）。
    arch = detect_checkpoint_arch(f"checkpoints/{req.checkpoint}")
    if arch == "unknown":
        arch = detect_checkpoint_arch(f"diffusion_models/{req.checkpoint}")
    if arch in ARCH_UNSUPPORTED_REASON:
        raise HTTPException(
            422,
            f"底模「{req.checkpoint}」{ARCH_UNSUPPORTED_REASON[arch]}；"
            "请选择 SD/SDXL 完整底模、Flux checkpoint 或 Krea2 unet。",
        )
    ctx = None
    if req.project_id:
        ctx = await resolve_project_context(user=user, project_id=req.project_id, required_role="editor")
        require_project_home_node(ctx, operation="access freezone project files")
    # 中文提示词先译写为英文 tag（SDXL 不识别中文；失败 fail-open 用原文）
    translated_prompt = await translate_prompt_to_english(req.prompt)
    translated_negative = (
        await translate_prompt_to_english(req.negative_prompt)
        if req.negative_prompt.strip()
        else req.negative_prompt
    )
    payload: dict[str, Any] = {
        "model": "DC-sdxl",
        "prompt": translated_prompt,
        "negative_prompt": translated_negative,
        "size": req.size,
        "checkpoint": req.checkpoint,
    }
    if arch == "flux":
        payload["workflow"] = "flux"
    elif arch == "krea2":
        payload["workflow"] = "krea2"
    if req.reference_url:
        payload["image"] = req.reference_url
    try:
        data = await _forward_to_gateway(payload, edits=bool(req.reference_url))
    except httpx.HTTPError as e:
        logger.warning("生图网关不可达: %s", e)
        raise HTTPException(502, f"生图网关不可达（local_gateway :8790 是否已启动）: {e}") from e
    except HTTPException as e:
        # unet-only / Flux 系底模不含文本编码器 → 记入自学习不兼容清单，
        # 下拉禁选 + 明确报错（替代原始 ComfyUI 日志），避免反复踩坑。
        detail = str(getattr(e, "detail", ""))
        if "clip input is invalid" in detail or "does not contain a valid clip" in detail:
            set_sdxl_incompatible(
                req.checkpoint, "不含文本编码器（unet-only/Flux 架构），与 SDXL 工作流不兼容"
            )
            raise HTTPException(
                422,
                f"底模「{req.checkpoint}」不含文本编码器（疑似 unet-only/Flux 架构），"
                "无法用于 SDXL 文生图工作流；请更换完整 SDXL 底模（如 majicMIX）。",
            ) from e
        raise
    # 生成成功 → 自动洗白该底模的历史 denylist 记录（自学习清单只记不清
    # 会在架构接入后留下禁选残留，如 krea2/ltx 先期失败误记）
    if req.checkpoint in get_sdxl_incompatible():
        set_sdxl_incompatible(req.checkpoint, None)
    if ctx:
        b64 = (data.get("data") or [{}])[0].get("b64_json", "")
        if b64:
            data.update(_save_b64_to_project(ctx, b64))
    return {"ok": True, "data": data}


# ---------- R18 短剧分镜规划（画布 R18 剧本节点，LLM 同步端点） ----------


@router.post("/r18-script/plan")
async def r18_script_plan(req: R18ScriptPlanRequest, user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """梗概+角色卡 → 结构化 scenes JSON（类型路由/预设/首帧提示词/对白）。

    同步调用本地 LLM（qwen3.6-uncensored 经 NewAPI/local_gateway 链路），
    照 ai-staging-prop 先例不走任务系统；R18 未开启直接 403。
    """
    if not nsfw_status()["nsfw_enabled"]:
        raise HTTPException(403, "R18 未开启，请先在模型库确认年满 18 岁")
    try:
        plan = await plan_r18_script(req)
    except ValueError as e:
        raise HTTPException(503, f"LLM 网关未配置: {e}") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"分镜规划失败: {e}") from e
    return {"ok": True, "data": plan.model_dump()}


# ---------- R18 配音（CosyVoice2 代理，画布 R18 出片节点用） ----------

# CosyVoice2-0.5B（workstation :9201，OpenAI /v1/audio/speech 兼容，MP3）。
# 真机 6 音色（2026-08-18 /health 实测）：晓晓/晓涵/晓伊（女）+ 云健/云希/云扬（男）。
COSYVOICE_URL = os.environ.get("DASHBOX_COSYVOICE_URL", "http://192.168.71.127:9201").rstrip("/")
# 真人配音演员音色（2026-08-19 注册到 workstation spk2info，推荐优先）：
# 中文 = 原神中文配音（genshin_ch_10npc），日文 = 动漫声优（japanese-anime-speech-v2）。
# edge-tts 系列（zh-CN-*Neural）为播报腔合成音色，保留兜底。
R18_TTS_VOICES = (
    # --- 真人中文（配音演员） ---
    "human-zh-paimon",
    "human-zh-ganyu",
    "human-zh-nahida",
    "human-zh-barbara",
    "human-zh-hutao",
    "human-zh-klee",
    "human-zh-raiden",
    "human-zh-keqing",
    "human-zh-yae",
    "human-zh-ayaka",
    # --- 真人日文（动漫声优，可说中文） ---
    "human-ja-moan",
    "human-ja-oneesan",
    "human-ja-panting",
    "human-ja-soft",
    "human-ja-timid",
    # --- edge-tts 合成（兜底） ---
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaohanNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunyangNeural",
)


class R18TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str = R18_TTS_VOICES[0]
    emotion: str = Field("", description="情感指令（中文 2-6 字如 温柔/羞涩/急切；空=普通合成）")
    speed: float = Field(1.0, ge=0.5, le=2.0, description="语速（对白 1.0 / 旁白 1.05 更自然）")
    source: Literal["dialogue", "narration"] = Field(
        "dialogue", description="dialogue=角色对白 / narration=旁白（旁白自动 1.05 语速）"
    )
    project_id: str | None = Field(None, description="提供则 mp3 落盘项目媒体并返回 url")


# 情感词 → 丰富 instruct 映射（2026-08-19 真机调研：受限指令集 issue #1802
# 必须「请用……的语气说」短格式；情感词越具体、带语速/声音修饰， instruct2
# 效果越明显——单纯「温柔」起伏弱，「温柔缠绵、语速稍缓」实测拖长出情感曲线）。
_EMOTION_INSTRUCTIONS: dict[str, str] = {
    "平静": "请用自然平和、像朋友聊天的语气说",
    "温柔": "请用温柔缠绵、语速稍缓的语气说",
    "羞涩": "请用害羞羞涩、声音轻柔的语气说",
    "急切": "请用急切恳求、语速稍快的语气说",
    "紧张": "请用紧张忐忑、声音发紧的语气说",
    "愉悦": "请用开心愉悦、带着笑意的语气说",
    "俏皮": "请用俏皮调皮、带着玩笑的语气说",
    "慵懒": "请用慵懒放松、声音低柔的语气说",
    "满足": "请用满足惬意、声音松软的语气说",
    "喘息轻颤": "请用气声喘息、声音轻颤的语气说",
    "低沉": "请用低沉磁性的语气说",
    "悲伤": "请用悲伤低落、语速缓慢的语气说",
    "愤怒": "请用愤怒有力、语速加快的语气说",
    "惊讶": "请用惊讶疑问、声音扬起的语气说",
}
_EMOTION_PREFIX = "请用"


def _build_instructions(emotion: str) -> str:
    emo = emotion.strip().strip("。.")
    if not emo:
        return ""
    if emo in _EMOTION_INSTRUCTIONS:
        return _EMOTION_INSTRUCTIONS[emo]
    # 未收录情感词拼通用格式（保留受限指令集格式，自由长指令会被念出来）
    return f"{_EMOTION_PREFIX}{emo}的语气说"


def _is_mp3_bytes(data: bytes) -> bool:
    """宽松校验：ID3v2 头 或 MPEG 帧同步（CosyVoice 实测返回 ID3+ADTS III）。"""
    return len(data) > 4 and (data[:3] == b"ID3" or data[0] == 0xFF)


@router.post("/r18-tts")
async def r18_tts(req: R18TtsRequest, user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """R18 短剧对白/旁白配音：代理 CosyVoice2（~1s/句），mp3 落盘项目媒体。

    剧本 scenes 里 audio=tts 的镜头由「R18 出片」节点逐句调用；h3-aio 的
    native 镜头（音画同出）不走这里。无 project_id 时返回 b64（测试/调试用）。
    """
    if not nsfw_status()["nsfw_enabled"]:
        raise HTTPException(403, "R18 未开启，请先在模型库确认年满 18 岁")
    if req.voice not in R18_TTS_VOICES:
        raise HTTPException(422, f"未知音色: {req.voice}（可选: {', '.join(R18_TTS_VOICES)}）")
    # 情感指令透传（CosyVoice2 instruct2，2026-08-18 服务端已支持；副语言
    # 标签 [laughter]/[breath] 等由文本层携带，指令只管情绪语气）
    instructions = _build_instructions(req.emotion)
    # 旁白默认 1.05 语速（显式传非默认值时尊重调用方）
    speed = req.speed if req.speed != 1.0 or req.source == "dialogue" else 1.05
    payload: dict[str, Any] = {
        "model": "cosyvoice2",
        "input": req.text,
        "voice": req.voice,
        "response_format": "mp3",
        "speed": speed,
    }
    if instructions:
        payload["instructions"] = instructions
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(f"{COSYVOICE_URL}/v1/audio/speech", json=payload)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"CosyVoice2 不可达（{COSYVOICE_URL}）: {e}") from e
    if resp.status_code != 200 or not _is_mp3_bytes(resp.content):
        raise HTTPException(
            502,
            f"CosyVoice2 返回异常 status={resp.status_code} bytes={len(resp.content)}",
        )
    if not req.project_id:
        return {
            "ok": True,
            "data": {
                "audio_b64": base64.b64encode(resp.content).decode("ascii"),
                "format": "mp3",
                "size": len(resp.content),
            },
        }
    ctx = await resolve_project_context(user=user, project_id=req.project_id, required_role="editor")
    require_project_home_node(ctx, operation="access freezone project files")
    out_dir = outputs_dir(Path(ctx.output_dir), "nsfw_studio")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tts_{uuid.uuid4().hex[:16]}.mp3"
    out.write_bytes(resp.content)
    rel_path = out.relative_to(Path(ctx.output_dir)).as_posix()
    return {
        "ok": True,
        "data": {
            "url": make_static_url_for_context(ctx, rel_path, local_path=out),
            "rel_path": rel_path,
            "size": len(resp.content),
        },
    }


# ---------- R18 成片合成（r18-compose：镜头视频 concat + 分层混音 + 字幕烧录） ----------

# ffmpeg 自动探测：标准 formula 不带 libass（无 subtitles filter，2026-08-18
# 实测 homebrew ffmpeg 9.0.1 亦缺），ffmpeg-full 才有——按候选序找第一个
# 支持 subtitles 的（env 显式指定最优先）。
_SUBTITLE_FILTER_PROBE = b"subtitles"
_FFMPEG_CANDIDATE_GLOBS = (
    "/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg",
    "/usr/local/Cellar/ffmpeg-full/*/bin/ffmpeg",
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "ffmpeg",
)


def _ffmpeg_supports_subtitles(binary: str) -> bool:
    try:
        out = subprocess.run(
            [binary, "-hide_banner", "-filters"],
            capture_output=True, timeout=15,
        ).stdout
        return _SUBTITLE_FILTER_PROBE in out
    except Exception:  # noqa: BLE001
        return False


def _resolve_ffmpeg_bin() -> str:
    explicit = os.environ.get("DASHBOX_FFMPEG_BIN", "").strip()
    if explicit:
        return explicit
    for pattern in _FFMPEG_CANDIDATE_GLOBS:
        for cand in sorted(glob.glob(pattern), reverse=True) or [pattern]:
            if _ffmpeg_supports_subtitles(cand):
                return cand
    return "ffmpeg"


def _resolve_ffprobe_bin(ffmpeg_bin: str) -> str:
    explicit = os.environ.get("DASHBOX_FFPROBE_BIN", "").strip()
    if explicit:
        return explicit
    # 与 ffmpeg 同目录的 ffprobe（ffmpeg-full 的 Cellar 路径派生）
    if "/" in ffmpeg_bin:
        sibling = Path(ffmpeg_bin).parent / "ffprobe"
        if sibling.is_file():
            return str(sibling)
    return "ffprobe"


_FFMPEG_BIN = _resolve_ffmpeg_bin()
FFPROBE_BIN = _resolve_ffprobe_bin(_FFMPEG_BIN)
COMPOSE_TIMEOUT_SECONDS = float(os.environ.get("DASHBOX_COMPOSE_TIMEOUT", "600"))
# 字幕字体：macOS 系统中文字体（subtitles filter 的 FontName 匹配）
COMPOSE_SUBTITLE_STYLE = os.environ.get(
    "DASHBOX_SUBTITLE_STYLE",
    "FontName=PingFang SC,FontSize=22,Outline=2,Shadow=1,MarginV=36",
)


class R18ComposeShot(BaseModel):
    video_url: str = Field(min_length=1, description="镜头 mp4 的项目静态 URL")
    tts_url: str | None = Field(None, description="镜头配音 mp3（audio=tts 时提供）")
    audio_mode: Literal["native", "tts", "none"] = "none"


class R18TitleCard(BaseModel):
    """片头/片尾卡：纯色底 + 居中标题文字（工厂流水线后期合成工序）。"""

    text: str = Field(default="", max_length=60)
    duration_sec: float = Field(default=2.0, ge=0.5, le=8.0)
    bg_color: str = Field(default="black", max_length=20)


class R18ComposeRequest(BaseModel):
    title: str = ""
    shots: list[R18ComposeShot] = Field(min_length=1)
    srt: str | None = Field(None, description="SRT 字幕文本（空则不烧字幕）")
    subtitles: list[str | None] = Field(
        default_factory=list,
        description="逐镜头字幕文本（与 shots 对齐；提供时按真实时长+片头卡+xfade 重叠重建 SRT，优先生于此处的 srt）",
    )
    project_id: str = Field(min_length=1)
    # ---- 工厂后期合成 v2（2026-08-19，全部可选、缺省=旧行为） ----
    bgm_url: str | None = Field(None, description="BGM 项目静态 URL（循环铺满全片）")
    bgm_volume: float = Field(default=0.35, ge=0.0, le=1.0)
    sfx_url: str | None = Field(None, description="环境音效项目静态 URL（循环铺满全片，如雨声/街道）")
    sfx_volume: float = Field(default=0.25, ge=0.0, le=1.0)
    color_profile: Literal["none", "warm", "cool", "film"] = Field(
        "none", description="调色：warm 暖调 / cool 冷调 / film 电影感"
    )
    transition: Literal["none", "fade"] = Field("none", description="镜头间转场")
    transition_sec: float = Field(default=0.5, ge=0.2, le=1.5)
    opening: R18TitleCard | None = Field(None, description="片头卡（标题+时长）")
    closing: R18TitleCard | None = Field(None, description="片尾卡")


# drawtext 中文字体（macOS 系统字体；无该文件时片卡退化为纯色无字）
DRAWTEXT_FONTFILE = os.environ.get(
    "DASHBOX_DRAWTEXT_FONT", "/System/Library/Fonts/PingFang.ttc"
)

_COLOR_PROFILE_FILTERS: dict[str, str] = {
    "warm": "colorbalance=rs=0.035:ms=0.02:bs=-0.035,eq=saturation=1.06:gamma=1.02",
    "cool": "colorbalance=rs=-0.035:ms=-0.01:bs=0.035,eq=saturation=1.02:gamma=0.98",
    "film": "eq=contrast=1.09:saturation=0.9:brightness=-0.012:gamma=1.03",
}


def _resolve_project_media(output_dir: Path, url: str, project_id: str) -> Path:
    """项目静态 URL（/static/projects/{pid}/{rel}?v=…）→ 本地文件路径。"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    prefix = f"/static/projects/{project_id}/"
    if not path.startswith(prefix):
        # 兜底：已是 rel_path（freezone/_outputs/...）直接拼
        rel = path.lstrip("/")
    else:
        rel = path[len(prefix):]
    local = (output_dir / rel).resolve()
    if not str(local).startswith(str(output_dir.resolve())):
        raise HTTPException(400, f"媒体路径越界: {url}")
    if not local.is_file():
        raise HTTPException(400, f"媒体文件不存在: {rel}")
    return local


def _build_compose_filter(
    *,
    num_videos: int,
    video_has_audio: list[bool],
    tts_offsets_ms: list[int | None],
    has_srt: bool,
    target_w: int,
    target_h: int,
    opening: R18TitleCard | None = None,
    closing: R18TitleCard | None = None,
    color_profile: str = "none",
    transition: str = "none",
    transition_sec: float = 0.5,
    bgm_input_index: int | None = None,
    bgm_volume: float = 0.35,
    sfx_input_index: int | None = None,
    sfx_volume: float = 0.25,
    total_duration_sec: float | None = None,
    video_durations: list[float] | None = None,
) -> tuple[str, str, str]:
    """构造 ffmpeg filter_complex（纯函数，便于单测）。

    视频轨：片头卡 + 逐镜头 scale/pad + 片尾卡 → concat（或 xfade 链）→
    可选调色 → 字幕烧录。
    音频轨：native 音轨 + tts 配音（+ BGM 循环）amix；无音频时 anullsrc 兜底。
    """
    parts: list[str] = []

    # ---- 段落（卡 + 镜头）统一规格化 ----
    seg_labels: list[str] = []
    seg_durations: list[float] = []

    def _card_filter(card: R18TitleCard, label: str) -> None:
        text = card.text.strip()
        draw = ""
        if text and Path(DRAWTEXT_FONTFILE).exists():
            safe = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
            draw = (
                f",drawtext=fontfile={DRAWTEXT_FONTFILE}:text='{safe}'"
                f":fontcolor=white:fontsize={max(24, target_h // 14)}"
                f":x=(w-text_w)/2:y=(h-text_h)/2"
            )
        parts.append(
            f"color=c={card.bg_color}:s={target_w}x{target_h}:d={card.duration_sec:.2f},"
            f"fps=24,format=yuv420p,setsar=1{draw}[{label}]"
        )

    if opening is not None:
        _card_filter(opening, "cardO")
        seg_labels.append("[cardO]")
        seg_durations.append(opening.duration_sec)

    for i in range(num_videos):
        parts.append(
            f"[{i}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:-1:-1:color=black,setsar=1,fps=24,"
            f"format=yuv420p[v{i}]"
        )
        seg_labels.append(f"[v{i}]")

    if closing is not None:
        _card_filter(closing, "cardC")
        seg_labels.append("[cardC]")
        seg_durations.append(closing.duration_sec)

    # ---- 拼接：无转场 concat / 有转场 xfade 链 ----
    if transition == "fade" and len(seg_labels) >= 2 and transition_sec > 0:
        # 完整段时长表：片头卡 + 镜头（endpoint probe 传入）+ 片尾卡
        vd = list(video_durations or [4.0] * num_videos)
        all_durations: list[float] = []
        if opening is not None:
            all_durations.append(opening.duration_sec)
        all_durations.extend(vd[:num_videos])
        if closing is not None:
            all_durations.append(closing.duration_sec)
        return _build_xfade_chain(
            parts=parts,
            seg_labels=seg_labels,
            seg_durations=all_durations,
            transition_sec=transition_sec,
            has_srt=has_srt,
            color_profile=color_profile,
            num_videos=num_videos,
            video_has_audio=video_has_audio,
            tts_offsets_ms=tts_offsets_ms,
            bgm_input_index=bgm_input_index,
            bgm_volume=bgm_volume,
            sfx_input_index=sfx_input_index,
            sfx_volume=sfx_volume,
            total_duration_sec=total_duration_sec,
        )

    concat_in = "".join(seg_labels)
    parts.append(f"{concat_in}concat=n={len(seg_labels)}:v=1:a=0[vcat]")
    vcur = "[vcat]"

    if color_profile in _COLOR_PROFILE_FILTERS:
        parts.append(f"{vcur}{_COLOR_PROFILE_FILTERS[color_profile]}[vgrad]")
        vcur = "[vgrad]"

    if has_srt:
        style = COMPOSE_SUBTITLE_STYLE.replace("'", "")
        parts.append(f"{vcur}subtitles=sub.srt:force_style='{style}'[vout]")
        vout = "[vout]"
    else:
        vout = vcur

    # ---- 音频轨 ----
    audio_labels: list[str] = []
    for i, has_audio in enumerate(video_has_audio):
        if not has_audio:
            continue
        parts.append(f"[{i}:a]aresample=24000,aformat=channel_layouts=mono[v{i}a]")
        audio_labels.append(f"[v{i}a]")
    vid_idx = num_videos
    for i, offset in enumerate(tts_offsets_ms):
        if offset is None:
            continue
        # 配音从镜头起始 +250ms 淡入（避免压视频第一帧环境声）
        delay = max(0, offset + 250)
        parts.append(
            f"[{vid_idx}:a]aresample=24000,aformat=channel_layouts=mono,"
            f"adelay={delay}:all=1[t{i}]"
        )
        audio_labels.append(f"[t{i}]")
        vid_idx += 1
    if bgm_input_index is not None and total_duration_sec:
        parts.append(
            f"[{bgm_input_index}:a]aresample=24000,aformat=channel_layouts=mono,"
            f"volume={bgm_volume},atrim=0:{total_duration_sec:.3f}[bgm]"
        )
        audio_labels.append("[bgm]")
    if sfx_input_index is not None and total_duration_sec:
        parts.append(
            f"[{sfx_input_index}:a]aresample=24000,aformat=channel_layouts=mono,"
            f"volume={sfx_volume},atrim=0:{total_duration_sec:.3f}[sfx]"
        )
        audio_labels.append("[sfx]")

    if audio_labels:
        mix_in = "".join(audio_labels)
        parts.append(
            f"{mix_in}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95[aout]"
        )
        aout = "[aout]"
    else:
        # 全静音镜头：anullsrc 无限长静音轨，由输出 -t 截断
        parts.append("anullsrc=r=24000:cl=mono[aout]")
        aout = "[aout]"

    return ";".join(parts), vout, aout


def _build_xfade_chain(
    *,
    parts: list[str],
    seg_labels: list[str],
    seg_durations: list[float],
    transition_sec: float,
    has_srt: bool,
    color_profile: str,
    num_videos: int,
    video_has_audio: list[bool],
    tts_offsets_ms: list[int | None],
    bgm_input_index: int | None,
    bgm_volume: float,
    sfx_input_index: int | None,
    sfx_volume: float,
    total_duration_sec: float | None,
) -> tuple[str, str, str]:
    """xfade 版 filter（transition=fade）。段时长必须由调用方传齐
    （endpoint 先 ffprobe 镜头真实时长再进来）。"""
    # 视频 xfade 链
    prev = seg_labels[0]
    offset_acc = 0.0
    for i in range(1, len(seg_labels)):
        offset_acc += seg_durations[i - 1] - transition_sec
        out_label = f"[x{i}]"
        parts.append(
            f"{prev}{seg_labels[i]}xfade=transition=fade:duration={transition_sec:.2f}"
            f":offset={max(0.0, offset_acc):.3f}{out_label}"
        )
        prev = out_label
    vcur = prev

    if color_profile in _COLOR_PROFILE_FILTERS:
        parts.append(f"{vcur}{_COLOR_PROFILE_FILTERS[color_profile]}[vgrad]")
        vcur = "[vgrad]"
    if has_srt:
        style = COMPOSE_SUBTITLE_STYLE.replace("'", "")
        parts.append(f"{vcur}subtitles=sub.srt:force_style='{style}'[vout]")
        vout = "[vout]"
    else:
        vout = vcur

    # 音频：与视频同样的偏移收缩（native/tts 按收缩后时间轴对齐）
    audio_labels: list[str] = []
    seg_start: list[float] = []
    acc = 0.0
    for i in range(len(seg_labels)):
        seg_start.append(acc)
        acc += seg_durations[i] - (transition_sec if i < len(seg_labels) - 1 else 0.0)
    vi = 0
    for seg_i, label_src in enumerate(seg_labels):
        if not label_src.startswith("[v"):
            continue
        if vi < len(video_has_audio) and video_has_audio[vi]:
            start_ms = int(seg_start[seg_i] * 1000)
            parts.append(
                f"[{vi}:a]aresample=24000,aformat=channel_layouts=mono,"
                f"adelay={start_ms}:all=1[v{vi}a]"
            )
            audio_labels.append(f"[v{vi}a]")
        vi += 1
    vid_idx = num_videos
    for i, offset in enumerate(tts_offsets_ms):
        if offset is None:
            continue
        delay = max(0, offset + 250)
        parts.append(
            f"[{vid_idx}:a]aresample=24000,aformat=channel_layouts=mono,"
            f"adelay={delay}:all=1[t{i}]"
        )
        audio_labels.append(f"[t{i}]")
        vid_idx += 1
    if bgm_input_index is not None and total_duration_sec:
        parts.append(
            f"[{bgm_input_index}:a]aresample=24000,aformat=channel_layouts=mono,"
            f"volume={bgm_volume},atrim=0:{total_duration_sec:.3f}[bgm]"
        )
        audio_labels.append("[bgm]")
    if sfx_input_index is not None and total_duration_sec:
        parts.append(
            f"[{sfx_input_index}:a]aresample=24000,aformat=channel_layouts=mono,"
            f"volume={sfx_volume},atrim=0:{total_duration_sec:.3f}[sfx]"
        )
        audio_labels.append("[sfx]")

    if audio_labels:
        mix_in = "".join(audio_labels)
        parts.append(
            f"{mix_in}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95[aout]"
        )
        aout = "[aout]"
    else:
        parts.append("anullsrc=r=24000:cl=mono[aout]")
        aout = "[aout]"
    return ";".join(parts), vout, aout


async def _probe_media_duration(path: Path) -> float:
    """ffprobe 探测媒体时长（秒）；失败返回 0。"""
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


async def _probe_has_audio(path: Path) -> bool:
    """ffprobe 判断媒体是否含音频流。"""
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return bool(out.decode().strip())


async def _probe_video_size(path: Path) -> tuple[int, int]:
    """ffprobe 取首个视频流分辨率（16 对齐）；失败兜底 832x1216。"""
    proc = await asyncio.create_subprocess_exec(
        FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    try:
        w_raw, h_raw = (int(v) for v in out.decode().strip().split(","))
    except Exception:  # noqa: BLE001
        w_raw, h_raw = 832, 1216
    return max(16, w_raw - w_raw % 16), max(16, h_raw - h_raw % 16)


def _format_srt_ts(total_sec: float) -> str:
    """秒 → SRT 时间戳（HH:MM:SS,mmm）。"""
    ms = max(0, round(total_sec * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, rest = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{rest:03d}"


def _build_compose_srt(
    subtitles: list[str | None],
    shot_starts: list[float],
    durations: list[float],
    *,
    transition_sec: float,
    use_xfade: bool,
) -> str:
    """按镜头真实起始时间构建 SRT（与 TTS adelay 同一时间轴）。

    字幕块 = [镜头起始, 起始+真实时长-转场重叠]；空文本镜头不出块。
    """
    blocks: list[str] = []
    index = 0
    for i, raw in enumerate(subtitles):
        text = (raw or "").strip()
        if not text:
            continue
        start = shot_starts[i] if i < len(shot_starts) else 0.0
        dur = durations[i] if i < len(durations) else 4.0
        end = start + dur - (transition_sec if use_xfade else 0.0)
        if end <= start:
            end = start + 0.5
        index += 1
        blocks.append(f"{index}\n{_format_srt_ts(start)} --> {_format_srt_ts(end)}\n{text}\n")
    return "\n".join(blocks)


@router.post("/r18-compose")
async def r18_compose(req: R18ComposeRequest, user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """R18 短剧成片合成：镜头视频顺序拼接 + 配音/native 分层混音 + SRT 字幕烧录。

    时间轴以各镜头视频真实时长（ffprobe）为准；tts 配音按镜头起始时间
    adelay 对齐；subtitles 提供时按同一时间轴重建 SRT 烧录并在响应返回
    （供第 8 工序 QC 的 ASR 回读比对使用）；输出统一 24fps H.264 + AAC。
    """
    if not nsfw_status()["nsfw_enabled"]:
        raise HTTPException(403, "R18 未开启，请先在模型库确认年满 18 岁")
    ctx = await resolve_project_context(user=user, project_id=req.project_id, required_role="editor")
    require_project_home_node(ctx, operation="access freezone project files")
    output_dir = Path(ctx.output_dir)

    # 1. 解析全部输入本地路径（视频顺序在前，tts 顺序在后，bgm 最后）
    video_paths: list[Path] = []
    for shot in req.shots:
        video_paths.append(_resolve_project_media(output_dir, shot.video_url, req.project_id))
    tts_paths: list[Path] = []
    for shot in req.shots:
        if shot.tts_url:
            tts_paths.append(_resolve_project_media(output_dir, shot.tts_url, req.project_id))
    bgm_path: Path | None = None
    if req.bgm_url:
        bgm_path = _resolve_project_media(output_dir, req.bgm_url, req.project_id)
    sfx_path: Path | None = None
    if req.sfx_url:
        sfx_path = _resolve_project_media(output_dir, req.sfx_url, req.project_id)

    # 2. 探测时长 / 音轨，计算 tts 起始偏移（镜头真实时长累计；
    #    片头卡前移 + xfade 重叠收缩）
    durations: list[float] = []
    has_audio: list[bool] = []
    for vp in video_paths:
        durations.append(await _probe_media_duration(vp))
        has_audio.append(await _probe_has_audio(vp))

    use_xfade = req.transition == "fade" and len(req.shots) >= 1 and req.transition_sec > 0
    opening_sec = req.opening.duration_sec if req.opening else 0.0
    closing_sec = req.closing.duration_sec if req.closing else 0.0
    overlap_total = req.transition_sec * max(0, len(video_paths) + (1 if req.opening else 0) + (1 if req.closing else 0) - 1) if use_xfade else 0.0
    total_duration = sum(durations) + opening_sec + closing_sec - overlap_total

    tts_offsets: list[int | None] = []
    shot_starts: list[float] = []
    cursor_ms = int(opening_sec * 1000)
    for idx, shot in enumerate(req.shots):
        shot_starts.append(cursor_ms / 1000.0)
        if shot.tts_url:
            tts_offsets.append(int(cursor_ms))
        else:
            tts_offsets.append(None)
        cursor_ms += int(durations[idx] * 1000)
        if use_xfade:
            cursor_ms -= int(req.transition_sec * 1000)

    # 字幕：subtitles（逐镜头文本）优先——按真实时间轴重建 SRT，与 TTS 对齐；
    # 未提供时回落请求方自带 srt（旧消费方）。两者皆空则不烧字幕。
    subtitles = [s if s else "" for s in req.subtitles]
    built_srt = ""
    if subtitles and len(subtitles) == len(req.shots) and any(subtitles):
        built_srt = _build_compose_srt(
            subtitles,
            shot_starts,
            durations,
            transition_sec=req.transition_sec,
            use_xfade=use_xfade,
        )
    srt_final = built_srt or (req.srt or "")

    # 3. 目标分辨率：取第一个视频的原始分辨率（16 对齐）
    target_w, target_h = await _probe_video_size(video_paths[0])

    has_srt = bool(srt_final.strip())
    bgm_input_index = (len(video_paths) + len(tts_paths)) if bgm_path else None
    # 环境音效输入序号：BGM 之后（输入顺序 视频→tts→bgm→sfx）
    sfx_input_index = (len(video_paths) + len(tts_paths) + (1 if bgm_path else 0)) if sfx_path else None
    filter_complex, vout_label, aout_label = _build_compose_filter(
        num_videos=len(video_paths),
        video_has_audio=has_audio,
        tts_offsets_ms=tts_offsets,
        has_srt=has_srt,
        target_w=target_w,
        target_h=target_h,
        opening=req.opening,
        closing=req.closing,
        color_profile=req.color_profile,
        transition=req.transition,
        transition_sec=req.transition_sec,
        bgm_input_index=bgm_input_index,
        bgm_volume=req.bgm_volume,
        sfx_input_index=sfx_input_index,
        sfx_volume=req.sfx_volume,
        total_duration_sec=total_duration,
        video_durations=durations,
    )

    # 4. 临时目录执行 ffmpeg（字幕文件用相对路径 sub.srt 避免 filter 转义）
    with tempfile.TemporaryDirectory(prefix="r18compose_") as tmpdir:
        tmp = Path(tmpdir)
        if has_srt:
            (tmp / "sub.srt").write_text(srt_final, encoding="utf-8")
        out_dir = outputs_dir(output_dir, "nsfw_studio")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"compose_{uuid.uuid4().hex[:16]}.mp4"

        cmd = [
            _FFMPEG_BIN, "-y",
            *[arg for vp in video_paths for arg in ("-i", str(vp))],
            *[arg for tp in tts_paths for arg in ("-i", str(tp))],
            # BGM / 环境音效循环铺满全片（filter 内 atrim 截断）
            *(["-stream_loop", "-1", "-i", str(bgm_path)] if bgm_path else []),
            *(["-stream_loop", "-1", "-i", str(sfx_path)] if sfx_path else []),
            "-filter_complex", filter_complex,
            "-map", vout_label, "-map", aout_label,
            "-t", f"{total_duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=tmpdir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=COMPOSE_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            tail = stderr.decode(errors="replace")[-600:]
            raise HTTPException(502, f"ffmpeg 合成失败: {tail}")

    rel_path = out_path.relative_to(output_dir).as_posix()
    size = out_path.stat().st_size
    return {
        "ok": True,
        "data": {
            "url": make_static_url_for_context(ctx, rel_path, local_path=out_path),
            "rel_path": rel_path,
            "size": size,
            "duration_sec": round(total_duration, 2),
            "shots": len(video_paths),
            # 烧录用的最终 SRT（subtitles 重建版或请求方自带），QC 工序回读比对用
            "srt": srt_final,
        },
    }


# ---------- R18 工厂质检（第 8 工序：时长/AV同步/字幕ASR回读/剧情LLM） ----------

# ASR 回读服务（FireRedASR OpenAI 兼容 shim，workstation :8300；实测 2026-08-19）
QC_ASR_URL = os.environ.get("DASHBOX_QC_ASR_URL", "http://192.168.71.127:8300/v1/asr/transcribe")
QC_ASR_TIMEOUT = float(os.environ.get("DASHBOX_QC_ASR_TIMEOUT", "120"))

_SRT_TIMESTAMP_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}\s*$", re.MULTILINE
)
_PUNCT_RE = re.compile(r"[\s，。！？!?.,;；:：「」『』\"'“”‘’()\[\]（）\-—…·~～]+")


class R18FactoryQcScene(BaseModel):
    scene_no: int = Field(ge=1)
    shot_description: str = ""
    dialogue: str = ""
    narration: str = ""
    duration_sec: int = Field(default=5, ge=1, le=60)


class R18FactoryQcRequest(BaseModel):
    compose_url: str = Field(min_length=1, description="成片项目静态 URL")
    srt: str = Field(default="", description="烧录用 SRT（回读比对）")
    scenes: list[R18FactoryQcScene] = Field(default_factory=list)
    project_id: str = Field(min_length=1)
    llm_review: bool = Field(True, description="剧情逻辑/内容适配 LLM 审查（网关不可达自动跳过）")


def _qc_clean_text(text: str) -> str:
    """去掉标点/空白的小写比对串（CER 近似用 difflib ratio）。"""
    return _PUNCT_RE.sub("", text).lower()


def _qc_extract_srt_text(srt: str) -> str:
    """SRT → 纯台词文本（去序号/时间戳/空行）。"""
    blocks = _SRT_TIMESTAMP_RE.split(srt or "")
    texts: list[str] = []
    for block in blocks[1:]:  # 第一段是首时间戳前的无效内容
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        # 去掉块尾可能的下一块序号行
        if lines and lines[-1].isdigit():
            lines = lines[:-1]
        texts.extend(lines)
    return "".join(texts)


def _qc_similarity(expected: str, actual: str) -> float:
    """ASR 回读 vs 字幕/台词 的相似度（0~1，difflib ratio 近似 CER）。"""
    a, b = _qc_clean_text(expected), _qc_clean_text(actual)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


@router.post("/r18-factory/qc")
async def r18_factory_qc(req: R18FactoryQcRequest, user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """R18 工厂第 8 工序质检：

    ① 时长/AV 同步：ffprobe 成片时长 vs 分镜时长合计（容差 max(3s, 15%)）
    ② 音轨存在性：无音轨报错（配音/BGM 工序失效）
    ③ 字幕准确率：抽音轨 ASR 回读 vs SRT 台词相似度（ASR 不可达跳过）
    ④ 剧情逻辑/内容适配：LLM 审查（失败跳过，fail-open）
    """
    if not nsfw_status()["nsfw_enabled"]:
        raise HTTPException(403, "R18 未开启，请先在模型库确认年满 18 岁")
    ctx = await resolve_project_context(user=user, project_id=req.project_id, required_role="editor")
    require_project_home_node(ctx, operation="access freezone project files")
    output_dir = Path(ctx.output_dir)
    compose_path = _resolve_project_media(output_dir, req.compose_url, req.project_id)

    # ① 时长 / ② 音轨
    probe_duration = await _probe_media_duration(compose_path)
    has_audio = await _probe_has_audio(compose_path)
    expected_duration = float(sum(s.duration_sec for s in req.scenes))
    tolerance = max(3.0, expected_duration * 0.15) if req.scenes else 3.0
    av_sync_ok = abs(probe_duration - expected_duration) <= tolerance if req.scenes else None

    # ③ 字幕 ASR 回读
    asr_similarity: float | None = None
    expected_text = _qc_extract_srt_text(req.srt)
    if expected_text:
        try:
            with tempfile.TemporaryDirectory(prefix="r18qc_") as tmpdir:
                wav_path = Path(tmpdir) / "audio.wav"
                proc = await asyncio.create_subprocess_exec(
                    _FFMPEG_BIN, "-y", "-v", "quiet", "-i", str(compose_path),
                    "-vn", "-ar", "16000", "-ac", "1", str(wav_path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=120)
                if wav_path.exists():
                    async with httpx.AsyncClient(timeout=QC_ASR_TIMEOUT) as client:
                        with wav_path.open("rb") as fh:
                            resp = await client.post(
                                QC_ASR_URL, files={"audio": ("audio.wav", fh, "audio/wav")}
                            )
                    if resp.status_code == 200:
                        data = resp.json()
                        actual = data if isinstance(data, str) else data.get("text", "")
                        asr_similarity = round(_qc_similarity(expected_text, str(actual)), 3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("QC ASR 回读跳过: %s", exc)

    subtitle_ok = (asr_similarity >= 0.6) if asr_similarity is not None else None

    # ④ LLM 剧情逻辑审查（fail-open）
    llm_result: dict[str, Any] | None = None
    if req.llm_review and req.scenes:
        try:
            lines = [
                f"镜头{s.scene_no}（{s.duration_sec}s）画面：{s.shot_description or '（无描述）'}"
                + (f" 台词：{s.dialogue}" if s.dialogue else "")
                + (f" 旁白：{s.narration}" if s.narration else "")
                for s in req.scenes
            ]
            review = await review_r18_quality(lines)
            llm_result = review.model_dump()
        except Exception as exc:  # noqa: BLE001
            logger.warning("QC LLM 审查跳过: %s", exc)

    checks_pass = (
        (av_sync_ok is not False)
        and has_audio
        and (subtitle_ok is not False)
        and (llm_result is None or llm_result.get("passed") is not False)
    )
    return {
        "ok": True,
        "data": {
            "passed": checks_pass,
            "duration_sec": round(probe_duration, 2),
            "expected_duration_sec": round(expected_duration, 2),
            "av_sync_ok": av_sync_ok,
            "has_audio": has_audio,
            "asr_similarity": asr_similarity,
            "subtitle_ok": subtitle_ok,
            "llm": llm_result,
        },
    }


# ---------- R18 视频生成（画布节点：内置预设，ComfyUI 直提） ----------

# 预设清单：id → (文件名, 路线)。路线决定提交目标与首帧分发范围。
NSFW_VIDEO_PRESETS: dict[str, dict[str, str]] = {
    "wan22-missionary": {
        "file": "wan22-nsfw-missionary.json",
        "route": "wan",
        "label": "传教士（Wan 2.2 I2V）",
        "trigger": "m15510n4ry",
    },
    "wan22-doggie-twerk": {
        "file": "wan22-nsfw-doggie-twerk.json",
        "route": "wan",
        "label": "后入/Twerk（Wan 2.2 I2V）",
        "trigger": "d0gg1e, twerk",
    },
    "wan22-blowjob-closeup": {
        "file": "wan22-nsfw-blowjob-closeup.json",
        "route": "wan",
        "label": "口交特写（Wan 2.2 I2V）",
        "trigger": "bl0wj0b",
    },
    "h3-aio": {
        "file": "h3-nsfw-fl2va-aio.json",
        "route": "h3",
        "label": "全能动作+音画（MiniMax H3）",
        "trigger": "hmmotion",
    },
    "h3-clean": {
        "file": "h3-clean.json",
        "route": "h3",
        "label": "剧情镜头·无LoRA 音画（MiniMax H3）",
        # 由 h3-aio 派生：摘除 HMNSFW/VBVR 双 LoRA，model 直连 UNETLoader——
        # R18 短剧的 plot/portrait 镜头路由到这里（无 NSFW 触发词污染）
        "trigger": "",
    },
}

PRESET_DIR = Path(
    os.environ.get("DASHBOX_PRESET_DIR", str(Path(__file__).resolve().parents[4] / "presets" / "nsfw"))
)
# Wan 路线：LB 三后端直连（首帧必须全覆盖，否则 LB 随机路由 2/3 概率
# LoadImage 找不到文件）；H3 路线：专用实例单点。
WAN_BACKENDS = [
    u.strip().rstrip("/")
    for u in os.environ.get(
        "DASHBOX_WAN_BACKENDS",
        "http://192.168.71.127:8189,http://192.168.71.115:8188,http://192.168.71.114:8193",
    ).split(",")
    if u.strip()
]
WAN_LB_URL = os.environ.get("DASHBOX_WAN_LB_URL", "http://192.168.71.127:8188").rstrip("/")
H3_URL = os.environ.get("DASHBOX_H3_URL", "http://192.168.71.127:8195").rstrip("/")
VIDEO_POLL_INTERVAL = 3.0
VIDEO_MAX_WAIT_SECONDS = float(os.environ.get("DASHBOX_VIDEO_TIMEOUT", "900"))


class GenerateVideoRequest(BaseModel):
    preset_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    negative_prompt: str | None = Field(None, description="仅遗留 Wan 工作流生效（短剧成片不选 Wan）")
    first_frame_url: str = Field(min_length=1, description="首帧图绝对 URL（必填）")
    width: int = Field(768, ge=64, le=2048)
    height: int = Field(1344, ge=64, le=2048)
    length: int = Field(124, ge=9, le=241, description="帧数（H3 124≈5s / 241≈10s）")
    seed: int | None = Field(None, description="缺省随机")
    project_id: str | None = Field(None, description="提供则 mp4 落盘项目媒体并返回 url")


def _drama_video_preset_ids() -> list[str]:
    """短剧/漫剧成片可选预设：仅 H3。Wan JSON 留在磁盘，不进 generate 目录。"""
    return [pid for pid, meta in NSFW_VIDEO_PRESETS.items() if meta.get("route") == "h3"]


def _load_preset_workflow(preset_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    meta = NSFW_VIDEO_PRESETS.get(preset_id)
    if not meta:
        raise HTTPException(400, f"未知预设: {preset_id}（可选: {', '.join(_drama_video_preset_ids())}）")
    path = PRESET_DIR / meta["file"]
    if not path.is_file():
        raise HTTPException(500, f"预设文件缺失: {path}")
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"预设 JSON 解析失败: {e}") from e
    return workflow, meta


def _patch_video_workflow(
    workflow: dict[str, Any],
    *,
    prompt: str,
    negative_prompt: str | None,
    first_frame_name: str,
    width: int,
    height: int,
    length: int,
    seed: int,
) -> dict[str, Any]:
    """按 class_type 定位节点改参（不依赖预设节点号，预设升级不改这里）。

    - WanImageToVideo / MiniMaxH3ImageToVideo → 尺寸/帧数（H3 的 prompt 一段式）
    - CLIPTextEncode ×2（Wan）→ 正/负向（按 _meta.title 区分，兜底按顺序）
    - LoadImage → 首帧文件名
    - KSamplerAdvanced ×2 / RandomNoise → 同一种子
    """
    clip_encoders: list[dict[str, Any]] = []
    for node in workflow.values():
        ct = node.get("class_type", "")
        inputs = node.get("inputs", {})
        if ct in ("WanImageToVideo", "MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"):
            inputs["width"] = width
            inputs["height"] = height
            inputs["length"] = length
            if ct in ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"):
                inputs["prompt"] = prompt
        elif ct == "UNETLoader":
            name = str(inputs.get("unet_name") or "")
            if "minimax_h3_fl2va" in name:
                inputs["unet_name"] = "10Eros_Max_h3_fl2va_beta2_pruned_int8_convrot.safetensors"
            elif "minimax_h3_ref2va" in name:
                inputs["unet_name"] = "10Eros_Max_h3_ref2va_beta2_pruned_int8_convrot.safetensors"
        elif ct == "CLIPTextEncode":
            clip_encoders.append(node)
        elif ct == "LoadImage":
            inputs["image"] = first_frame_name
        elif ct in ("KSamplerAdvanced", "RandomNoise"):
            inputs["noise_seed"] = seed
    if clip_encoders:
        # 预设约定：title 含「正向」为正、含「负向」为负；兜底第一个为正。
        pos = next(
            (n for n in clip_encoders if "正向" in (n.get("_meta", {}).get("title") or "")),
            clip_encoders[0],
        )
        pos["inputs"]["text"] = prompt
        if negative_prompt is not None:
            neg = next(
                (n for n in clip_encoders if n["inputs"] is not pos["inputs"]),
                None,
            )
            if neg is not None:
                neg["inputs"]["text"] = negative_prompt
    return workflow


async def _upload_first_frame(
    client: httpx.AsyncClient, image_url: str, backends: list[str]
) -> str:
    """下载首帧并以同名文件覆盖上传到全部目标后端 input，返回文件名。

    LB /upload 轮询单实例而 /prompt 加权随机选实例——只传入口必然有
    2/3 概率 LoadImage 在另一后端找不到文件（画布实测复现，2026-08-17）。
    支持 data:image/png;base64 URI（上游图片节点旧产物为 b64 内嵌时 httpx
    无法 GET，直接解码）。
    """
    if image_url.startswith("data:image/"):
        try:
            b64 = image_url.split(",", 1)[1]
            content = base64.b64decode(b64)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, "首帧 data URI 解析失败") from e
    else:
        img = await client.get(image_url)
        if img.status_code != 200:
            raise HTTPException(502, f"下载首帧失败: {image_url} status={img.status_code}")
        content = img.content
    filename = f"dc_{uuid.uuid4().hex[:8]}.png"
    ok = 0
    last_err = ""
    for target in backends:
        try:
            up = await client.post(
                f"{target}/upload/image",
                files={"image": (filename, content, "image/png")},
                data={"type": "input", "overwrite": "true"},
            )
            if up.status_code == 200:
                ok += 1
            else:
                last_err = f"{target} status={up.status_code}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{target}: {e}"
    if ok == 0:
        raise HTTPException(502, f"首帧上传全部后端失败: {last_err}")
    return filename


async def _wait_video_output(
    client: httpx.AsyncClient, base_url: str, prompt_id: str
) -> dict[str, str]:
    """轮询 ComfyUI history 直到产物视频就绪，返回 {filename, subfolder, type}。"""
    waited = 0.0
    while waited < VIDEO_MAX_WAIT_SECONDS:
        await asyncio.sleep(VIDEO_POLL_INTERVAL)
        waited += VIDEO_POLL_INTERVAL
        hist = await client.get(f"{base_url}/history/{prompt_id}")
        if hist.status_code != 200:
            continue
        data = hist.json().get(prompt_id) or {}
        status = data.get("status") or {}
        # 失败快速失败：把节点异常带出来。
        if status.get("status_str") == "error":
            messages = "; ".join(
                m.get("message", "") for m in (status.get("messages") or [])
            )
            raise HTTPException(502, f"ComfyUI 执行失败: {messages[:300]}")
        outputs = data.get("outputs") or {}
        for node_out in outputs.values():
            for out_val in node_out.values():
                if (
                    isinstance(out_val, list)
                    and out_val
                    and isinstance(out_val[0], dict)
                    and "filename" in out_val[0]
                    and str(out_val[0].get("filename", "")).endswith((".mp4", ".webm", ".mov"))
                ):
                    return out_val[0]
        if status.get("completed"):
            break
    raise HTTPException(504, f"视频生成超时（>{int(VIDEO_MAX_WAIT_SECONDS)}s）")


async def _submit_and_collect(
    workflow: dict[str, Any], first_frame_url: str
) -> dict[str, Any]:
    """提交 workflow 到对应路线后端并等待 mp4 产物，返回视频字节与元信息。"""
    is_wan = any(
        n.get("class_type") == "WanImageToVideo" for n in workflow.values()
    )
    submit_url = WAN_LB_URL if is_wan else H3_URL
    upload_targets = WAN_BACKENDS if is_wan else [H3_URL]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=VIDEO_MAX_WAIT_SECONDS)) as client:
        frame_name = await _upload_first_frame(client, first_frame_url, upload_targets)
        # 回写真实首帧文件名（上传前文件名不可知，patch 时留空占位）。
        for node in workflow.values():
            if node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = frame_name
        resp = await client.post(f"{submit_url}/prompt", json={"prompt": workflow})
        if resp.status_code >= 400:
            raise HTTPException(502, f"ComfyUI /prompt 拒绝 status={resp.status_code}: {resp.text[:300]}")
        prompt_id = resp.json().get("prompt_id", "")
        if not prompt_id:
            raise HTTPException(502, "ComfyUI 未返回 prompt_id")
        artifact = await _wait_video_output(client, submit_url, prompt_id)
        view = await client.get(
            f"{submit_url}/view",
            params={
                "filename": artifact["filename"],
                "subfolder": artifact.get("subfolder", ""),
                "type": artifact.get("type", "output"),
            },
        )
        if view.status_code != 200:
            raise HTTPException(502, f"下载产物失败 status={view.status_code}")
        return {
            "video_bytes": view.content,
            "filename": artifact["filename"],
            "backend": submit_url,
        }


@router.get("/video-presets")
def list_video_presets(user: dict = Depends(get_api_user)) -> dict[str, Any]:
    """R18 视频预设清单（未开启 R18 时返回空列表——内容不暴露）。"""
    if not nsfw_status()["nsfw_enabled"]:
        return {"ok": True, "data": {"items": []}}
    items = [
        {"id": pid, "label": m["label"], "trigger": m["trigger"], "route": m["route"]}
        for pid, m in NSFW_VIDEO_PRESETS.items()
        if m.get("route") == "h3"
    ]
    return {"ok": True, "data": {"items": items}}


@router.post("/generate-video")
async def generate_video(
    req: GenerateVideoRequest, user: dict = Depends(get_api_user)
) -> dict[str, Any]:
    """R18 视频生成：内置预设直提 ComfyUI，mp4 落盘项目媒体（画布节点用）。

    预设全部为 NSFW 内容——未开启 R18 时一律 403（无 SFW 例外）。
    Wan 路线 81 帧 ≈ 5s；H3 路线宽高必须 32 倍数、length 按 17k+5（124/241）。
    """
    if not nsfw_status()["nsfw_enabled"]:
        raise HTTPException(403, "R18 视频生成需要先在模型库开启 R18")
    meta = NSFW_VIDEO_PRESETS.get(req.preset_id)
    if not meta or meta.get("route") != "h3":
        raise HTTPException(
            400,
            f"短剧/漫剧成片引擎为 MiniMax-H3，可选预设: {', '.join(_drama_video_preset_ids())}",
        )
    workflow, meta = _load_preset_workflow(req.preset_id)
    seed = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
    workflow = _patch_video_workflow(
        workflow,
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        first_frame_name="",  # 占位，_submit_and_collect 上传后回写
        width=req.width,
        height=req.height,
        length=req.length,
        seed=seed,
    )
    ctx = None
    if req.project_id:
        ctx = await resolve_project_context(user=user, project_id=req.project_id, required_role="editor")
        require_project_home_node(ctx, operation="access freezone project files")
    try:
        result = await _submit_and_collect(workflow, req.first_frame_url)
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        logger.warning("R18 视频后端不可达: %s", e)
        raise HTTPException(502, f"视频后端不可达（ComfyUI 集群）: {e}") from e
    data: dict[str, Any] = {
        "seed": seed,
        "preset_id": req.preset_id,
        "filename": result["filename"],
        "backend": result["backend"],
    }
    if ctx:
        video_bytes: bytes = result["video_bytes"]
        project_dir = Path(ctx.output_dir)
        out_dir = outputs_dir(project_dir, "nsfw_studio")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{uuid.uuid4().hex[:16]}.mp4"
        out.write_bytes(video_bytes)
        rel_path = out.relative_to(project_dir).as_posix()
        data.update(
            {
                "url": make_static_url_for_context(ctx, rel_path, local_path=out),
                "rel_path": rel_path,
                "size": len(video_bytes),
            }
        )
    return {"ok": True, "data": data}
