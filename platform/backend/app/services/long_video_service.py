"""M20 长视频分块续写服务（PoC，默认关闭）—— H3 I2V 帧链续写。

技术路线 A（2026-08-10 长视频调研结论）：
  chunk 0:  首帧关键帧 → H3 I2V → video_0
  chunk i:  video_{i-1} 末帧（ffmpeg 抽取 → 上传 ComfyUI input）作首帧 → H3 I2V
  最终:     ffmpeg concat 拼接全部块 → 长视频（2-5 分钟起步，块数不设硬上限）

跨块一致性保障（全部复用现有能力，不新增模型）：
  - 帧链：chunk i+1 首帧 = chunk i 末帧，运动/光影/构图在接缝处天然连续
  - 角色：每块透传 reference_images（三视图定妆照，触发 ref2va 一致性路径）
  - 画风：每块透传 style（M18.4 约束层 prompt 锚定 + 检测层 VLM 质检）

默认关闭（long_video_enabled=False）：PoC 验证接缝与角色漂移前，
不影响现有高质量短剧主流程（pipeline_orchestrator 不调用本服务）。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.agents.video_agent import VideoAgent, _run_ffmpeg
from app.config import settings
from app.models.schemas import VideoRequest
from app.services.model_gateway import model_gateway

logger = logging.getLogger(__name__)


class LongVideoError(RuntimeError):
    """长视频分块续写失败。"""


@dataclass
class LongVideoResult:
    """长视频分块续写产出。

    video_path 位于 work_dir（未指定时为 tempfile.mkdtemp 创建的目录），
    文件生命周期由调用方负责（本服务不自动清理，避免返回后文件被 GC 删除）。
    """

    video_path: Path
    chunk_paths: list[Path]
    chunks_completed: int
    duration_seconds: float
    elapsed_seconds: float


async def _run_capture(cmd: list[str]) -> str:
    """异步运行命令并返回 stdout（ffprobe 取时长用），失败抛 RuntimeError。"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="ignore")[-500:]
        raise RuntimeError(f"command failed: {err}")
    return stdout.decode("utf-8", errors="ignore")


async def extract_last_frame(video_path: Path, out_path: Path) -> Path:
    """ffmpeg 抽取视频末帧 → PNG（-sseof 从尾部定位，避免全片解码）。"""
    cmd = [
        "ffmpeg", "-y",
        "-sseof", "-0.1",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]
    await _run_ffmpeg(cmd)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise LongVideoError(f"末帧抽取失败（输出为空）: {video_path}")
    return out_path


async def concat_videos(video_paths: list[Path], out_path: Path) -> Path:
    """ffmpeg concat demuxer 拼接（统一重编码，消除块间时间戳/编码参数缝隙）。

    H3 各块均带原生音频，重编码同时归一化视频（libx264 crf18）与音频（aac），
    保证接缝处时间戳严格连续；+faststart 便于后续 web 预览。
    """
    if not video_paths:
        raise LongVideoError("concat_videos 需要至少 1 个视频")
    list_file = out_path.with_suffix(".concat.txt")
    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in video_paths), encoding="utf-8"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out_path),
    ]
    await _run_ffmpeg(cmd)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise LongVideoError("视频拼接失败（输出为空）")
    return out_path


async def probe_video_duration(video_path: Path) -> float:
    """ffprobe 取视频时长（秒）。"""
    out = await _run_capture(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
    )
    return float(out.strip())


class LongVideoService:
    """分块续写编排：把 VideoAgent 的单块生成能力串成长视频。

    依赖注入 VideoAgent 便于单元测试 mock；worker_url 默认 H3 专用实例
    （经本地模型网关 video_h3 能力解析），续写帧上传到该实例 input 目录。
    """

    def __init__(
        self,
        video_agent: VideoAgent | None = None,
        worker_url: str | None = None,
    ):
        self._agent = video_agent or VideoAgent()
        self._worker_url = (worker_url or model_gateway.endpoint("video_h3")).rstrip("/")

    async def generate(
        self,
        *,
        first_frame_url: str,
        chunk_prompts: list[str],
        negative_prompt: str = "",
        reference_images: list[str] | None = None,
        style: str = "",
        chunk_seconds: int | None = None,
        max_chunks: int | None = None,
        work_dir: Path | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> LongVideoResult:
        """按 prompt 序列逐块生成并拼接；任一块失败即 fail-fast 抛 LongVideoError。

        PoC 阶段不做部分块拼接放行——接缝质量未验证前，产出"看起来完整但
        中间断链"的视频比直接报错更危险（可能混入正式短剧流水线）。
        """
        if not settings.long_video_enabled:
            raise LongVideoError("长视频分块续写未启用（long_video_enabled=False）")
        if not chunk_prompts:
            raise LongVideoError("chunk_prompts 为空")

        chunk_seconds = chunk_seconds or settings.long_video_chunk_seconds
        max_chunks = max_chunks or settings.long_video_max_chunks
        prompts = chunk_prompts[: max(1, max_chunks)]

        start = time.time()
        work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="longvideo_"))
        work.mkdir(parents=True, exist_ok=True)

        def _report(pct: int, msg: str) -> None:
            if progress_callback:
                progress_callback(pct, msg)

        total = len(prompts)
        current_frame_url = first_frame_url
        chunk_paths: list[Path] = []
        for i, prompt in enumerate(prompts):
            _report(int(i / total * 90), f"长视频块 {i + 1}/{total} 生成中")
            req = VideoRequest(
                scene_id=9500 + i,
                image_url=current_frame_url,
                prompt=prompt,
                negative_prompt=negative_prompt,
                duration_seconds=chunk_seconds,
                reference_images=list(reference_images or []),
                style=style,
            )
            resp = await self._agent.execute(req)
            if not resp.success:
                raise LongVideoError(f"块 {i + 1}/{total} 生成失败: {resp.error}")
            chunk_path = work / f"chunk_{i:02d}.mp4"
            await self._agent._download_to_file(resp.data["video_url"], chunk_path)
            chunk_paths.append(chunk_path)

            if i < total - 1:
                frame_path = work / f"chain_{i:02d}.png"
                await extract_last_frame(chunk_path, frame_path)
                current_frame_url = await self._upload_frame(frame_path, i)

        _report(92, "拼接长视频")
        final_path = work / "long_video.mp4"
        await concat_videos(chunk_paths, final_path)
        duration = await probe_video_duration(final_path)
        _report(100, "长视频完成")

        logger.info(
            "长视频生成完成: chunks=%d duration=%.1fs elapsed=%.1fs path=%s",
            len(chunk_paths), duration, time.time() - start, final_path,
        )
        return LongVideoResult(
            video_path=final_path,
            chunk_paths=chunk_paths,
            chunks_completed=len(chunk_paths),
            duration_seconds=duration,
            elapsed_seconds=time.time() - start,
        )

    async def _upload_frame(self, frame_path: Path, index: int) -> str:
        """把续写末帧上传到 ComfyUI input 目录，返回可 GET 的 /view URL。

        VideoAgent 现有链路只接受 HTTP URL（upload_image_to_comfyui 会先下载
        再上传），因此这里上传后构造 type=input 的 /view URL 供下一块作首帧。
        文件名按块序确定性命名 + overwrite=true，避免多次运行在 input 目录堆积。
        """
        filename = f"{settings.long_video_frame_prefix}_{index:02d}.png"
        resp = await self._agent.http.post(
            f"{self._worker_url}/upload/image",
            files={"image": (filename, frame_path.read_bytes(), "image/png")},
            data={"type": "input", "overwrite": "true"},
        )
        resp.raise_for_status()
        name = resp.json().get("name", filename)
        return f"{self._worker_url}/view?filename={name}&type=input"
