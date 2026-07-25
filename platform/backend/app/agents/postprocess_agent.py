"""后处理 Agent — 编排超分 + 插帧 + 修复 + 降噪 + H.265 编码。

P4.4 设计：将 1080p/24fps 视频提升到 4K/60fps，并完成音频降噪与最终编码。

流程（每步 best-effort，单步失败不阻断整体）：
1. 超分 RealBasicVSR x4: 1080p → 4K（workstation GPU）
2. 插帧 RIFE: 24fps → 60fps（workstation GPU）
3. 修复 ProPainter: 去水印/去穿帮（workstation GPU，按需启用）
4. 音频降噪 DeepFilterNet3: Mac 集群 Rust 原生
5. 最终编码 Mac FFmpeg VideoToolbox H.265: 4K 成片

设计原则：
- 受 settings.postprocess_enabled 总开关控制，默认关闭
- 单步开关在 settings 中独立控制，允许部分启用
- 单步失败记录到 steps 列表并继续后续步骤（best-effort）
- 最终 H.265 编码失败时降级使用 H.264 软编码
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.agents.base import BaseAgent
from app.agents.edit_agent import FFMPEG_BIN, FFPROBE_BIN
from app.config import settings
from app.models.schemas import (
    AgentResponse,
    PostprocessRequest,
    PostprocessResult,
    PostprocessStep,
    PostprocessStepResult,
)
from app.services.postprocess_service import (
    DeepFilterNetService,
    PostprocessService,
    PostprocessServiceError,
)

logger = logging.getLogger(__name__)

# 输出目录：保存后处理最终成片
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "postprocess"


def _local_path_from_url(url: str) -> Path | None:
    """如果 URL 是本地静态资源，返回文件系统路径；否则返回 None。"""
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "static":
        asset_type = parts[1]
        filename = parts[-1]
        candidate = OUTPUT_DIR.parent / asset_type / filename
        if candidate.exists():
            return candidate
    return None


class PostprocessAgent(BaseAgent):
    """后处理 Agent：超分 + 插帧 + 修复 + 降噪 + H.265 编排。

    受 settings.postprocess_enabled 总开关控制：
    - True: 按单步开关编排执行
    - False: 跳过所有步骤，返回原视频

    单步失败时记录到 steps 并继续，不阻断整体流程。
    """

    def __init__(self):
        super().__init__("postprocess_agent")
        self._postprocess: PostprocessService | None = None
        self._deepfilternet: DeepFilterNetService | None = None

    @property
    def postprocess_service(self) -> PostprocessService:
        """懒加载 PostprocessService（RealBasicVSR/RIFE/ProPainter）。"""
        if self._postprocess is None:
            self._postprocess = PostprocessService(http_client=self.http)
        return self._postprocess

    @property
    def deepfilternet_service(self) -> DeepFilterNetService:
        """懒加载 DeepFilterNetService（音频降噪）。"""
        if self._deepfilternet is None:
            self._deepfilternet = DeepFilterNetService(http_client=self.http)
        return self._deepfilternet

    async def execute(
        self,
        request: PostprocessRequest,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> AgentResponse:
        """执行后处理编排。

        Args:
            request: PostprocessRequest 包含 video_url / audio_url / steps / output_resolution
            progress_callback: 可选进度回调

        Returns:
            AgentResponse.data 为 PostprocessResult.model_dump()
        """
        start = time.time()

        def _report(percent: int, message: str) -> None:
            if progress_callback:
                progress_callback(percent, message)

        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            # 总开关关闭：直接跳过
            if not settings.postprocess_enabled:
                _report(100, "后处理已关闭，跳过")
                logger.info("后处理已关闭 (postprocess_enabled=False)，跳过")
                result = PostprocessResult(
                    scene_id=request.scene_id,
                    final_video_url=request.video_url,
                    original_video_url=request.video_url,
                    steps=[],
                    success=True,
                    elapsed_seconds=time.time() - start,
                )
                return AgentResponse(
                    success=True,
                    data=result.model_dump(),
                    elapsed_seconds=time.time() - start,
                )

            # 确定要执行的步骤
            steps_to_run = self._resolve_steps(request.steps)
            if not steps_to_run:
                _report(100, "无启用的后处理步骤，跳过")
                result = PostprocessResult(
                    scene_id=request.scene_id,
                    final_video_url=request.video_url,
                    original_video_url=request.video_url,
                    steps=[],
                    success=True,
                    elapsed_seconds=time.time() - start,
                )
                return AgentResponse(
                    success=True,
                    data=result.model_dump(),
                    elapsed_seconds=time.time() - start,
                )

            current_url = request.video_url
            step_results: list[PostprocessStepResult] = []
            total = len(steps_to_run)
            all_success = True

            for idx, step in enumerate(steps_to_run):
                base_percent = int(idx / total * 100)
                _report(base_percent, f"开始 {step.value}")

                def step_progress(percent: int, message: str) -> None:
                    # 子步骤进度映射到整体 0-100
                    overall = int((idx + percent / 100.0) / total * 100)
                    _report(overall, f"{step.value}: {message}")

                step_start = time.time()
                try:
                    new_url = await self._run_step(
                        step=step,
                        current_url=current_url,
                        audio_url=request.audio_url,
                        scene_id=request.scene_id,
                        progress_callback=step_progress,
                    )
                    step_results.append(
                        PostprocessStepResult(
                            step=step,
                            success=True,
                            output_url=new_url,
                            elapsed_seconds=time.time() - step_start,
                            message=f"{step.value} 完成",
                        )
                    )
                    current_url = new_url
                except Exception as e:
                    all_success = False
                    logger.warning(
                        "后处理步骤 %s 失败，跳过: scene_id=%s err=%s",
                        step.value, request.scene_id, e,
                    )
                    step_results.append(
                        PostprocessStepResult(
                            step=step,
                            success=False,
                            output_url="",
                            elapsed_seconds=time.time() - step_start,
                            message=f"{step.value} 失败: {e}",
                        )
                    )
                    # 失败不阻断：current_url 保持不变，继续下一步

            # 最终编码步骤可能需要本地 FFmpeg 处理（如果上一步是远程服务返回 URL）
            _report(100, "后处理完成")
            result = PostprocessResult(
                scene_id=request.scene_id,
                final_video_url=current_url,
                original_video_url=request.video_url,
                steps=step_results,
                success=all_success,
                elapsed_seconds=time.time() - start,
            )
            return AgentResponse(
                success=True,  # best-effort：即使有步骤失败也返回成功
                data=result.model_dump(),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            logger.error(
                "后处理 Agent 严重错误: scene_id=%s err=%s",
                request.scene_id, e,
            )
            return AgentResponse(
                success=False,
                error=f"后处理失败: {e}",
                elapsed_seconds=time.time() - start,
            )

    def _resolve_steps(
        self, override: list[PostprocessStep]
    ) -> list[PostprocessStep]:
        """确定要执行的步骤顺序。

        - 若 override 非空：仅执行指定步骤（保持给定顺序，去重）
        - 若 override 为空：按 settings 单步开关决定，顺序固定为
          super_resolution → frame_interpolation → inpainting → audio_denoise → final_encode
        """
        if override:
            # 去重保序
            seen: set[str] = set()
            result: list[PostprocessStep] = []
            for s in override:
                if s.value not in seen:
                    seen.add(s.value)
                    result.append(s)
            return result

        steps: list[PostprocessStep] = []
        if settings.postprocess_super_resolution_enabled:
            steps.append(PostprocessStep.SUPER_RESOLUTION)
        if settings.postprocess_frame_interpolation_enabled:
            steps.append(PostprocessStep.FRAME_INTERPOLATION)
        if settings.postprocess_inpainting_enabled:
            steps.append(PostprocessStep.INPAINTING)
        if settings.postprocess_audio_denoise_enabled:
            steps.append(PostprocessStep.AUDIO_DENOISE)
        if settings.postprocess_final_encode_enabled:
            steps.append(PostprocessStep.FINAL_ENCODE)
        return steps

    async def _run_step(
        self,
        step: PostprocessStep,
        current_url: str,
        audio_url: str | None,
        scene_id: int,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> str:
        """执行单步后处理，返回新的视频/音频 URL。"""
        if step == PostprocessStep.SUPER_RESOLUTION:
            return await self.postprocess_service.run_super_resolution(
                video_url=current_url,
                scene_id=scene_id,
                progress_callback=progress_callback,
            )
        if step == PostprocessStep.FRAME_INTERPOLATION:
            return await self.postprocess_service.run_frame_interpolation(
                video_url=current_url,
                scene_id=scene_id,
                progress_callback=progress_callback,
            )
        if step == PostprocessStep.INPAINTING:
            return await self.postprocess_service.run_inpainting(
                video_url=current_url,
                scene_id=scene_id,
                progress_callback=progress_callback,
            )
        if step == PostprocessStep.AUDIO_DENOISE:
            if not audio_url:
                raise PostprocessServiceError(
                    "音频降噪步骤需要 audio_url，但未提供"
                )
            # DeepFilterNet3 降噪返回字节，保存到本地输出目录
            denoised_bytes = await self.deepfilternet_service.denoise(audio_url)
            return await self._save_denoised_audio(
                denoised_bytes, scene_id=scene_id
            )
        if step == PostprocessStep.FINAL_ENCODE:
            return await self._final_encode(
                video_url=current_url,
                scene_id=scene_id,
                progress_callback=progress_callback,
            )
        raise ValueError(f"未知的后处理步骤: {step}")

    async def _save_denoised_audio(
        self, audio_bytes: bytes, scene_id: int
    ) -> str:
        """保存降噪后的音频到输出目录，返回静态 URL。"""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"denoised_scene_{scene_id}_{uuid.uuid4().hex[:8]}.mp3"
        filepath = OUTPUT_DIR / filename
        filepath.write_bytes(audio_bytes)
        base_url = f"http://localhost:{settings.backend_port}"
        url = f"{base_url}/static/postprocess/{filename}"
        logger.info("降噪音频已保存: scene_id=%s -> %s", scene_id, url)
        return url

    async def _final_encode(
        self,
        video_url: str,
        scene_id: int,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> str:
        """最终编码：Mac FFmpeg VideoToolbox H.265。

        下载视频 → 检测分辨率 → H.265 编码（失败回退 H.264 软编码）→ 保存到输出目录。
        """
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="postprocess_") as tmp:
            tmp_dir = Path(tmp)
            # 1. 获取视频文件（如果是本地静态资源直接复用）
            local = _local_path_from_url(video_url)
            if local:
                input_path = local
            else:
                input_path = tmp_dir / "input.mp4"
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream("GET", video_url) as resp:
                        resp.raise_for_status()
                        with open(input_path, "wb") as f:
                            async for chunk in resp.aiter_bytes():
                                f.write(chunk)

            # 2. 探测原视频分辨率
            width, height = await self._probe_resolution(input_path)

            # 3. 计算目标分辨率（不放大，仅缩放到目标尺寸）
            target_res = settings.postprocess_final_resolution
            target_w, target_h = _parse_resolution(target_res)
            # 如果原视频分辨率高于目标，不放大；低于目标则放大到目标
            out_w = max(width, target_w) if width < target_w else target_w
            out_h = max(height, target_h) if height < target_h else target_h

            output_name = f"final_scene_{scene_id}_{uuid.uuid4().hex[:8]}.mp4"
            output_path = OUTPUT_DIR / output_name

            # 4. H.265 编码（VideoToolbox 硬件加速）
            if progress_callback:
                progress_callback(50, "Mac VideoToolbox H.265 编码中")

            codec = settings.postprocess_final_codec
            crf = settings.postprocess_final_crf
            preset = settings.postprocess_final_preset

            # scale 滤镜：保持纵横比，pad 到目标分辨率
            vf = (
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={settings.rife_target_fps if settings.postprocess_frame_interpolation_enabled else 24},"
                f"format=yuv420p"
            )

            cmd = [
                FFMPEG_BIN,
                "-y",
                "-i", str(input_path),
                "-vf", vf,
                "-c:v", codec,
                "-b:v", "0",  # CRF 模式下设置 0 让编码器按 CRF 控制
                "-crf", str(crf),
                "-preset", preset,
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-ac", "2",
                "-movflags", "+faststart",
                str(output_path),
            ]

            try:
                await self._run_ffmpeg(cmd)
                logger.info(
                    "H.265 编码成功: scene_id=%s codec=%s -> %s",
                    scene_id, codec, output_path.name,
                )
            except RuntimeError as e:
                # VideoToolbox 失败：回退 H.264 软编码
                logger.warning(
                    "H.265 (%s) 编码失败，回退 H.264 软编码: %s",
                    codec, e,
                )
                if progress_callback:
                    progress_callback(70, "H.265 失败，回退 H.264 软编码")
                cmd_fallback = [
                    FFMPEG_BIN,
                    "-y",
                    "-i", str(input_path),
                    "-vf", vf,
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "48000",
                    "-ac", "2",
                    "-movflags", "+faststart",
                    str(output_path),
                ]
                await self._run_ffmpeg(cmd_fallback)
                logger.info(
                    "H.264 软编码回退成功: scene_id=%s -> %s",
                    scene_id, output_path.name,
                )

            base_url = f"http://localhost:{settings.backend_port}"
            return f"{base_url}/static/postprocess/{output_path.name}"

    async def _probe_resolution(self, video_path: Path) -> tuple[int, int]:
        """使用 ffprobe 探测视频分辨率。"""
        cmd = [
            FFPROBE_BIN,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(video_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            w_str, h_str = stdout.decode().strip().split("x")
            return int(w_str), int(h_str)
        except (ValueError, IndexError):
            # 探测失败时返回默认 1080p
            return 1920, 1080

    async def _run_ffmpeg(self, cmd: list[str]) -> None:
        """运行 FFmpeg 命令，失败时抛出 RuntimeError。"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore")[-500:]
            raise RuntimeError(f"FFmpeg failed: {err}")


def _parse_resolution(resolution: str) -> tuple[int, int]:
    """解析 '3840x2160' 格式的分辨率为 (宽, 高)。"""
    width, _, height = resolution.partition("x")
    return int(width), int(height)


postprocess_agent = PostprocessAgent()
