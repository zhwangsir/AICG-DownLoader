"""FastAPI 入口 — AI 短剧一条龙工作台后端。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents.character_agent import character_agent
from app.agents.edit_agent import edit_agent
from app.agents.lip_sync_agent import lip_sync_agent
from app.agents.postprocess_agent import postprocess_agent
from app.agents.quality_agent import quality_agent, visual_quality_agent
from app.agents.script_agent import script_agent
from app.agents.storyboard_agent import storyboard_agent
from app.agents.subtitle_agent import subtitle_agent
from app.agents.video_agent import video_agent
from app.agents.voice_agent import voice_agent
from app.config import settings
from app.routers import drama, progress

logger = logging.getLogger(__name__)

# 输出目录 —— 与 agents 的 OUTPUT_DIR 保持一致（platform/backend/output/）
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
AUDIO_DIR = OUTPUT_DIR / "audio"
SUBTITLE_DIR = OUTPUT_DIR / "subtitle"
VIDEO_DIR = OUTPUT_DIR / "video"
# P4.3: 新增图像生成服务返回字节的输出目录
CHARACTER_DIR = OUTPUT_DIR / "character"
STORYBOARD_DIR = OUTPUT_DIR / "storyboard"
# P4.4: 新增唇形同步与后处理输出目录（降噪音频 + 最终 4K 成片）
POSTPROCESS_DIR = OUTPUT_DIR / "postprocess"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)
POSTPROCESS_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时输出配置摘要；关闭时释放所有 Agent 的 HTTP 连接池。"""
    dc = settings.downloader_config
    lines = [
        "=" * 60,
        "AI 短剧工作台后端启动",
        f"  EXO LLM    : {settings.exo_base_url} ({settings.exo_model_glm52})",
        f"  ComfyUI HQ : {settings.comfyui_image_hq}",
        f"  ComfyUI Fast: {settings.comfyui_image_fast}",
        f"  ComfyUI Video A: {settings.comfyui_video_a}",
        f"  图像后端   : {settings.image_backend} (LTX 预览: {settings.ltx_video_enabled})",
        f"  视频后端   : {settings.video_backend}",
        f"  ASR/TTS    : {settings.asr_backend} / {settings.tts_backend}",
        f"  唇形同步   : {settings.lip_sync_enabled} (LatentSync)",
        f"  后处理     : {settings.postprocess_enabled} (RealBasicVSR+RIFE+ProPainter+DeepFilterNet3+H.265)",
    ]
    if dc:
        lines.append(f"  下载器配置  : comfy_root={dc.comfy_root}, torch={dc.torch_index}")
    lines.append("  静态资源   : /static/audio, /static/subtitle, /static/video, /static/character, /static/storyboard, /static/postprocess")
    lines.append(f"  CORS       : {settings.cors_origin_list}")
    lines.append("=" * 60)
    logger.info("\n".join(lines))
    yield
    # shutdown 阶段：关闭所有 Agent 单例的 httpx 连接池
    agents = [
        script_agent, character_agent, storyboard_agent, video_agent,
        voice_agent, subtitle_agent, edit_agent, quality_agent,
        visual_quality_agent, lip_sync_agent, postprocess_agent,
    ]
    for a in agents:
        try:
            await a.aclose()
        except Exception:
            logger.warning("关闭 Agent HTTP 客户端失败: %s", a.name, exc_info=True)


app = FastAPI(
    title="AI 短剧一条龙工作台",
    description="剧本/角色/分镜/视频/配音/字幕 Agent 全流程",
    version="0.11.0",
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
# P4.3: HunyuanImage / FLUX+PuLID 返回的字节图保存目录
app.mount("/static/character", StaticFiles(directory=str(CHARACTER_DIR)), name="character")
app.mount("/static/storyboard", StaticFiles(directory=str(STORYBOARD_DIR)), name="storyboard")
# P4.4: 后处理输出目录（DeepFilterNet3 降噪音频 + Mac FFmpeg H.265 4K 成片）
app.mount("/static/postprocess", StaticFiles(directory=str(POSTPROCESS_DIR)), name="postprocess")

app.include_router(drama.router)
app.include_router(progress.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "AI 短剧一条龙工作台",
        "version": "0.11.0",
        "milestone": "M5 — 系统性优化",
        "docs": "/docs",
        "health": "/api/drama/health",
    }
