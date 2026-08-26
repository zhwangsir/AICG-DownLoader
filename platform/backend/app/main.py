"""FastAPI 入口 — AI 短剧一条龙工作台后端。"""

from __future__ import annotations

import os

# HF 镜像：必须在导入 fastembed/huggingface_hub 之前设置（ENDPOINT 常量在 import 时固化）。
# 集群设备直连 huggingface.co 超时（Errno 110），统一走 hf-mirror.com；
# setdefault 保留外部环境变量的优先权。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 禁用 Xet 存储后端：hf-mirror 不代理 cas-server.xethub.hf.co，
# Xet 分块重建会 401（2026-08-04 core 实测），禁用后回退普通 HTTP 走镜像。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import logging
from contextlib import asynccontextmanager
from pathlib import Path

# T3 节点日志：统一初始化 root logging（含时间戳），否则 aicg.node 等
# 业务 logger 的 INFO 埋点被默认 WARNING 级别静默，连接超时/数据异常无从排查。
# uvicorn 已配置自身 logger，basicConfig 只影响 root 与业务 logger。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents.base import close_shared_llm_client
from app.agents.character_agent import character_agent
from app.agents.edit_agent import edit_agent
from app.agents.quality_agent import quality_agent, visual_quality_agent
from app.agents.script_agent import script_agent
from app.agents.storyboard_agent import storyboard_agent
from app.agents.subtitle_agent import subtitle_agent
from app.agents.video_agent import video_agent
from app.agents.voice_agent import voice_agent
from app.config import settings
from app.routers import drama, models, panel, progress

logger = logging.getLogger(__name__)

# 输出目录 —— 与 agents 的 OUTPUT_DIR 保持一致（platform/backend/output/）
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
AUDIO_DIR = OUTPUT_DIR / "audio"
SUBTITLE_DIR = OUTPUT_DIR / "subtitle"
VIDEO_DIR = OUTPUT_DIR / "video"
# 角色定妆照 / 分镜关键帧的输出目录
CHARACTER_DIR = OUTPUT_DIR / "character"
STORYBOARD_DIR = OUTPUT_DIR / "storyboard"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时输出配置摘要；关闭时释放所有 Agent 的 HTTP 连接池。"""
    dc = settings.downloader_config
    lines = [
        "=" * 60,
        "AIGCPannel 后端启动",
        f"  EXO LLM    : {settings.exo_base_url} ({settings.exo_model_glm52})",
        f"  ComfyUI HQ : {settings.comfyui_image_hq}",
        f"  ComfyUI Fast: {settings.comfyui_image_fast}",
        f"  ComfyUI Video A: {settings.comfyui_video_a}",
        f"  图像后端   : {settings.image_backend}",
        f"  视频后端   : {settings.video_backend}",
        f"  LTX-2.5    : {settings.ltx_comfyui_url} (enabled: {settings.ltx_enabled})",
        f"  ASR/TTS    : {settings.asr_backend} / {settings.tts_backend}",
    ]
    if dc:
        lines.append(f"  下载器配置  : comfy_root={dc.comfy_root}, torch={dc.torch_index}")
    lines.append("  静态资源   : /static/audio, /static/subtitle, /static/video, /static/character, /static/storyboard")
    lines.append(f"  CORS       : {settings.cors_origin_list}")
    lines.append("=" * 60)
    logger.info("\n".join(lines))
    yield
    # shutdown 阶段：关闭所有 Agent 单例的 httpx 连接池
    agents = [
        script_agent, character_agent, storyboard_agent, video_agent,
        voice_agent, subtitle_agent, edit_agent, quality_agent,
        visual_quality_agent,
    ]
    for a in agents:
        try:
            await a.aclose()
        except Exception:
            logger.warning("关闭 Agent HTTP 客户端失败: %s", a.name, exc_info=True)
    # 关闭模块级共享 LLM 客户端连接池（ai_optimizer/rag_service/智能体辅助复用）
    try:
        await close_shared_llm_client()
    except Exception:
        logger.warning("关闭共享 LLM 客户端失败", exc_info=True)


app = FastAPI(
    title="AIGCPannel",
    description="剧本/角色/分镜/视频/配音/字幕 Agent 全流程",
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-NSFW", "Accept"],
)

# 静态文件服务：音频、字幕、视频成片、角色图、分镜图
app.mount("/static/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")
app.mount("/static/subtitle", StaticFiles(directory=str(SUBTITLE_DIR)), name="subtitle")
app.mount("/static/video", StaticFiles(directory=str(VIDEO_DIR)), name="video")
app.mount("/static/character", StaticFiles(directory=str(CHARACTER_DIR)), name="character")
app.mount("/static/storyboard", StaticFiles(directory=str(STORYBOARD_DIR)), name="storyboard")

app.include_router(drama.router)
app.include_router(models.router)
app.include_router(models.settings_router)
app.include_router(progress.router)
app.include_router(panel.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "AIGCPannel",
        "version": "0.4.0",
        "milestone": "M23 — 下线服务死代码激进清理",
        "docs": "/docs",
        "health": "/api/drama/health",
        "panel": "/api/panel/status",
    }
