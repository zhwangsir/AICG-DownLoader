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


def compute_ambience_gain(voice_seconds: float, video_seconds: float) -> float:
    """M12.2 按对白密度（人声/视频时长比）选择 H3 环境音增益档位（纯函数）。

    - ratio ≥ 0.85（对白密集）：dense 档，压低环境音避免盖过人声
    - 0.4 ≤ ratio < 0.85：基准档 h3_ambience_gain
    - ratio < 0.4（大量留白）：sparse 档，提升环境音营造氛围
    - 时长非法（探测失败为 0/负值）：回退基准增益，主链路不中断
    """
    if voice_seconds <= 0 or video_seconds <= 0:
        return settings.h3_ambience_gain
    ratio = voice_seconds / video_seconds
    if ratio >= 0.85:
        return settings.h3_ambience_gain_dense
    if ratio >= 0.4:
        return settings.h3_ambience_gain
    return settings.h3_ambience_gain_sparse


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
        """处理单个片段：下载素材 → 标准化 → 混音 → 烧录字幕。

        H3 原生音轨处理（settings.h3_native_audio_enabled）：
        - dialogue 镜头且视频自带音轨：保留 H3 原生立体声为人声，不叠 IndexTTS
        - narration 镜头且视频自带音轨：IndexTTS 人声 + H3 环境音垫底混音
        - 视频无音轨：IndexTTS 仅人声（H3 未产出语音时的回退）
        - 视频自带音轨但无人声 TTS（纯场景）：原音轨直接随视频保留
        - 混音 ffmpeg 失败：降级为纯人声并记 warning，不阻断主链路
        """
        seg_dir = work_dir / f"scene_{segment.scene_id}"
        seg_dir.mkdir(exist_ok=True)

        video_path = await self._download(segment.video_url, seg_dir / "video.mp4")

        # 字幕可选：空 URL 表示不烧字幕（Studio 无字幕时仍走 drama 合成）
        subtitle_filter = ""
        if (segment.subtitle_url or "").strip():
            subtitle_path = await self._download(segment.subtitle_url, seg_dir / "subtitle.srt")
            # subtitles 滤镜需要字幕文件为绝对路径并转义特殊字符
            sub_path_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
            subtitle_filter = f"subtitles='{sub_path_escaped}',"

        # 人声可选：audio_url 为空表示纯场景镜头
        audio_path: Path | None = None
        if segment.audio_url:
            audio_path = await self._download(segment.audio_url, seg_dir / "audio.mp3")

        output_path = seg_dir / "segment_final.mp4"

        # 统一分辨率/帧率；有字幕才烧录
        vf = (
            f"{subtitle_filter}"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},format=yuv420p"
        )

        # 用 ffprobe 探测视频是否自带音轨（不依赖 video_backend 字符串，
        # 回退后端无声轨时自动跳过混音，更健壮）
        audio_type = str(getattr(segment, "audio_type", "") or "narration").strip().lower()
        need_probe = settings.h3_native_audio_enabled and (
            audio_type == "dialogue" or audio_path is not None
        )
        has_native_stream = need_probe and await self._probe_has_audio(video_path)
        # dialogue：H3 已生成对白，禁止与 IndexTTS 双轨叠音
        if audio_type == "dialogue" and has_native_stream:
            await self._run_ffmpeg(self._build_keep_original_cmd(video_path, vf, output_path))
            return output_path

        has_native_audio = has_native_stream and audio_path is not None

        if audio_path is None:
            # 纯场景镜头：单输入直出，默认映射保留原音轨（若有）
            await self._run_ffmpeg(self._build_keep_original_cmd(video_path, vf, output_path))
        elif has_native_audio:
            # M12.2 按对白密度动态调环境音增益：人声/视频时长比分档，
            # 探测失败（返回 0.0）时 compute_ambience_gain 自动回退基准增益
            gain: float | None = None
            if settings.h3_dynamic_gain_enabled:
                video_secs = await self._probe_duration(video_path)
                voice_secs = await self._probe_duration(audio_path)
                gain = compute_ambience_gain(voice_secs, video_secs)
            try:
                await self._run_ffmpeg(
                    self._build_native_mix_cmd(video_path, audio_path, vf, output_path, gain=gain)
                )
            except RuntimeError as e:
                logger.warning(
                    "H3 原生音轨混音失败，降级为纯人声: scene_id=%s err=%s",
                    segment.scene_id, e,
                )
                await self._run_ffmpeg(
                    self._build_voice_only_cmd(video_path, audio_path, vf, output_path)
                )
        else:
            await self._run_ffmpeg(
                self._build_voice_only_cmd(video_path, audio_path, vf, output_path)
            )
        return output_path

    def _build_voice_only_cmd(
        self, video_path: Path, audio_path: Path, vf: str, output_path: Path
    ) -> list[str]:
        """构建「人声替换原声」命令（视频无音轨时的原行为）。"""
        return [
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

    def _build_native_mix_cmd(
        self,
        video_path: Path,
        audio_path: Path,
        vf: str,
        output_path: Path,
        gain: float | None = None,
    ) -> list[str]:
        """构建「人声 + H3 原生环境音」混音命令。

        - 人声 volume=1.0 为主；原生音轨按 gain（缺省 settings.h3_ambience_gain）衰减垫底
        - M12.2：调用方可按对白密度（compute_ambience_gain）传入动态增益
        - amix duration=longest：环境音铺满整个镜头而非随人声结束而中断
          （人声短于视频时 duration=first 会把混音连同画面一起截短，浪费 H3 镜头）；
          输出端 -shortest 仍以视频长度封顶（人声超长时截断，与原行为一致）
        """
        ambience_gain = settings.h3_ambience_gain if gain is None else gain
        filter_complex = (
            f"[0:v]{vf}[vout];"
            f"[0:a]volume={ambience_gain}[amb];"
            f"[1:a]volume=1.0[voice];"
            f"[voice][amb]amix=inputs=2:duration=longest[aout]"
        )
        return [
            FFMPEG_BIN,
            "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
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

    def _build_keep_original_cmd(
        self, video_path: Path, vf: str, output_path: Path
    ) -> list[str]:
        """构建「纯场景镜头」命令：无人声，原音轨（若有）随视频保留。"""
        return [
            FFMPEG_BIN,
            "-y",
            "-i", str(video_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            str(output_path),
        ]

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

    async def _probe_has_audio(self, video_path: Path) -> bool:
        """用 ffprobe 探测视频是否包含音频流。

        探测失败（ffprobe 不可用/文件损坏）视为无音轨，走纯人声分支，保证主链路健壮。
        """
        cmd = [
            FFPROBE_BIN,
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(video_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except OSError:
            return False
        return proc.returncode == 0 and b"audio" in stdout

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
