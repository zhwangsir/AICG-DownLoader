"""剪辑 Agent — 视频片段 + 配音 + 字幕 → 短剧成片。

流程：
1. 下载每个片段的视频、配音音频、SRT 字幕到临时目录
2. 逐片段标准化：统一分辨率/帧率、混合配音、烧录字幕
3. 使用 FFmpeg concat 拼接所有片段
4. 可选添加 BGM，输出最终成片 MP4
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

from app.agents.base import BaseAgent
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    EditRequest,
    EditResult,
    EditSegment,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "video"
DEFAULT_RESOLUTION = (1080, 1920)

# drawtext 可用的中文字体候选路径（按优先级）
_CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",                       # macOS
    "/System/Library/Fonts/STHeiti Light.ttc",                  # macOS 备选
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # Linux Noto
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",             # Linux 文泉驿
    "/usr/share/fonts/truetype/arphic/uming.ttc",               # Linux 文鼎
]


def _find_cjk_font() -> str | None:
    """返回第一个存在的中文字体路径；找不到返回 None。"""
    for path in _CJK_FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None

# macOS Homebrew 的默认 ffmpeg 可能未启用 libass/drawtext，优先使用 ffmpeg-full
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
_FFPROBE_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
FFMPEG_BIN = str(_FFMPEG_FULL) if _FFMPEG_FULL.exists() else (shutil.which("ffmpeg") or "ffmpeg")
FFPROBE_BIN = str(_FFPROBE_FULL) if _FFPROBE_FULL.exists() else (shutil.which("ffprobe") or "ffprobe")


def _parse_resolution(resolution: str) -> tuple[int, int]:
    """解析 '1080x1920' 格式的分辨率为 (宽, 高)。"""
    width, _, height = resolution.partition("x")
    return int(width), int(height)


def _local_path_from_url(url: str, local_dir: Path) -> Path | None:
    """如果 URL 是本地静态资源，直接返回文件系统路径；否则返回 None。"""
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        return None

    # /static/audio/foo.mp3 -> output/audio/foo.mp3
    # /static/subtitle/foo.srt -> output/subtitle/foo.srt
    # /static/video/foo.mp4 -> output/video/foo.mp4
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "static":
        asset_type = parts[1]
        filename = parts[-1]
        candidate = OUTPUT_DIR.parent / asset_type / filename
        if candidate.exists():
            return candidate
    return None


class EditAgent(BaseAgent):
    """剪辑 Agent：多场景素材 → 完整短剧成片。"""

    def __init__(self):
        super().__init__("edit_agent")

    async def execute(self, request: EditRequest) -> AgentResponse:
        start = time.time()
        output_path: Path | None = None
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            width, height = _parse_resolution(request.output_resolution)

            with tempfile.TemporaryDirectory(prefix="edit_") as tmp:
                tmp_dir = Path(tmp)
                segment_files: list[Path] = []

                # 1. 逐片段处理
                for seg in request.segments:
                    seg_file = await self._process_segment(
                        seg,
                        tmp_dir,
                        width,
                        height,
                        request.output_fps,
                    )
                    segment_files.append(seg_file)

                # 2. 拼接所有片段
                concat_list = tmp_dir / "concat.txt"
                concat_list.write_text(
                    "\n".join(f"file '{p.as_posix()}'" for p in segment_files),
                    encoding="utf-8",
                )

                final_name = f"final_{request.project_id or uuid.uuid4().hex[:8]}.mp4"
                final_path = OUTPUT_DIR / final_name

                await self._concat_segments(
                    concat_list,
                    final_path,
                    request.transition,
                )

                # 3. 可选 BGM
                if request.bgm_url:
                    final_path = await self._mix_bgm(
                        final_path,
                        request.bgm_url,
                        tmp_dir,
                    )

                # 4. 合规标识：烧录「AI生成」（+备案号），2026-09-01 新规要求
                if request.ai_label_enabled:
                    final_path = await self._burn_ai_label(final_path, request.license_number)

                output_path = final_path

            # 获取成片时长
            duration = await self._probe_duration(output_path)
            base_url = f"http://localhost:{settings.backend_port}"

            return AgentResponse(
                success=True,
                data=EditResult(
                    project_id=request.project_id,
                    title=request.title,
                    final_video_url=f"{base_url}/static/video/{output_path.name}",
                    duration_seconds=duration,
                    segments_count=len(request.segments),
                ).model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return AgentResponse(
                success=False,
                error=f"剪辑合成失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    async def _process_segment(
        self,
        segment: EditSegment,
        work_dir: Path,
        width: int,
        height: int,
        fps: int,
    ) -> Path:
        """处理单个片段：下载素材 → 标准化 → 混音 → 烧录字幕。"""
        seg_dir = work_dir / f"scene_{segment.scene_id}"
        seg_dir.mkdir(exist_ok=True)

        video_path = await self._download(segment.video_url, seg_dir / "video.mp4")
        audio_path = await self._download(segment.audio_url, seg_dir / "audio.mp3")
        subtitle_path = await self._download(segment.subtitle_url, seg_dir / "subtitle.srt")

        output_path = seg_dir / "segment_final.mp4"

        # 统一分辨率/帧率，烧录字幕，替换/混合配音音频
        # subtitles 滤镜需要字幕文件为绝对路径并转义特殊字符
        sub_path_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
        vf = (
            f"subtitles='{sub_path_escaped}',"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},format=yuv420p"
        )

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-shortest",
            str(output_path),
        ]
        await self._run_ffmpeg(cmd)
        return output_path

    async def _concat_segments(
        self,
        concat_list: Path,
        output_path: Path,
        transition: str,
    ) -> None:
        """使用 concat demuxer 拼接片段。"""
        # 先统一格式，避免 concat 时参数不一致
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output_path),
        ]
        await self._run_ffmpeg(cmd)

        # 简单 fade 转场：对每个片段头尾做 fade（可选，MVP 中先直接拼接）
        if transition == "fade":
            await self._apply_fade(output_path)

    async def _apply_fade(self, video_path: Path) -> None:
        """对成片应用整体淡入淡出（MVP 级简单转场）。"""
        tmp_path = video_path.with_suffix(".fade.mp4")
        duration = await self._probe_duration(video_path)
        fade_out_start = max(0.0, duration - 1.0)
        vf = f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_start}:d=0.5"
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(video_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            str(tmp_path),
        ]
        await self._run_ffmpeg(cmd)
        shutil.move(str(tmp_path), str(video_path))

    async def _burn_ai_label(self, video_path: Path, license_number: str = "") -> Path:
        """在成片右上角烧录「AI生成」标识（含可选备案号）。

        2026-09-01《微短剧发展管理办法》：AI 生成微短剧须在每集明显位置添加提示标识。
        找不到中文字体时跳过烧录并记录 warning（不阻断主流程）。
        """
        font = _find_cjk_font()
        if not font:
            logger.warning("未找到可用中文字体，跳过「AI生成」标识烧录: %s", video_path.name)
            return video_path

        # 备案号消毒：drawtext 文本中 ':' '\\' '%' 有特殊含义，需剔除
        safe_license = re.sub(r"[:\\'%]", "", license_number).strip()
        label = f"AI生成  备案号:{safe_license}" if safe_license else "AI生成"

        tmp_path = video_path.with_suffix(".label.mp4")
        vf = (
            f"drawtext=fontfile='{font}':text='{label}':"
            "fontsize=36:fontcolor=white@0.85:"
            "box=1:boxcolor=black@0.4:boxborderw=12:"
            "x=w-tw-30:y=30"
        )
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(video_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            str(tmp_path),
        ]
        await self._run_ffmpeg(cmd)
        shutil.move(str(tmp_path), str(video_path))
        logger.info("已烧录「AI生成」标识: %s", video_path.name)
        return video_path

    async def _mix_bgm(
        self,
        video_path: Path,
        bgm_url: str,
        work_dir: Path,
    ) -> Path:
        """混合背景音乐（自动循环并降低音量）。"""
        bgm_path = await self._download(bgm_url, work_dir / "bgm.mp3")
        output_path = video_path.with_suffix(".bgm.mp4")

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(video_path),
            "-stream_loop", "-1",
            "-i", str(bgm_path),
            "-filter_complex",
            "[1:a]volume=0.3,apad[bgm];[0:a][bgm]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
        await self._run_ffmpeg(cmd)
        return output_path

    async def _download(self, url: str, dest: Path) -> Path:
        """下载 URL 到本地路径；如果是本地静态资源则直接复用。"""
        local = _local_path_from_url(url, OUTPUT_DIR)
        if local:
            return local

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
        return dest

    async def _probe_duration(self, video_path: Path) -> float:
        """使用 ffprobe 获取视频时长。"""
        cmd = [
            FFPROBE_BIN,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0

    async def _run_ffmpeg(self, cmd: list[str]) -> None:
        """运行 FFmpeg 命令，失败时抛出 RuntimeError。"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore")[-500:]
            raise RuntimeError(f"FFmpeg failed: {err}")


edit_agent = EditAgent()
