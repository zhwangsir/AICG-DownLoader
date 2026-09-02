"""M7 全链路自动编排服务。

从"一句话创意"一键走完 剧本 → 角色定妆照 → 分镜 → 视频 → 配音 → 字幕 → 剪辑 → 质检 全链路。

设计约束：
- 异步执行：asyncio 后台任务，进度复用 progress_tracker（SSE/轮询通道不变）
- 步骤级容错：单场景素材失败仅跳过该场景；剧本/剪辑失败才整体失败
- 可取消：步骤间检查取消标志，视频/分镜等长步骤完成后生效
- 单实例内存管理（与 progress_tracker 一致），任务句柄用于取消
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.agents.character_agent import character_agent
from app.agents.edit_agent import edit_agent
from app.agents.quality_agent import quality_agent, visual_quality_agent
from app.agents.script_agent import script_agent
from app.agents.storyboard_agent import storyboard_agent
from app.agents.subtitle_agent import subtitle_agent
from app.agents.video_agent import group_scenes_for_multishot, video_agent
from app.agents.voice_agent import voice_agent
from app.config import settings
from app.core.node_logger import node_span
from app.core.progress import progress_tracker
from app.models.schemas import (
    AgentResponse,
    CharacterRequest,
    DialogueLine,
    EditRequest,
    EditSegment,
    PipelineRunRequest,
    QualityCheckRequest,
    QualityVisualRequest,
    Scene,
    Script,
    ScriptRequest,
    StoryboardBatchRequest,
    SubtitleRequest,
    VideoBatchRequest,
    VideoRequest,
    VoiceRequest,
)
from app.services.character_library import character_library
from app.services.long_video_planner import long_video_planner
from app.services.long_video_service import LongVideoError, LongVideoService
from app.services.style_anchor import (
    resolve_style_anchor,
    style_negative_tail,
    style_positive_tail,
)

logger = logging.getLogger(__name__)

# M24.2 锚点重拍：逐镜头生成参数快照根目录
# （output/pipeline/{project_id}/shot_params.json）
PIPELINE_OUTPUT_ROOT = Path(__file__).resolve().parent.parent.parent / "output" / "pipeline"

# 各步骤进度区间（percent）
_PROGRESS = {
    "script": (2, 10),
    "character": (10, 25),
    "storyboard": (25, 40),
    "video": (40, 70),
    "voice": (70, 80),
    "subtitle": (80, 88),
    "edit": (88, 96),
    "quality": (96, 100),
}


class PipelineCancelledError(Exception):
    """流水线被用户取消。"""


class PipelineOrchestrator:
    """全链路编排器：任务句柄 + 取消标志的内存管理。"""

    def __init__(self) -> None:
        self._handles: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def start(self, request: PipelineRunRequest) -> str:
        """创建后台流水线任务，返回 task_id。"""
        project_id = f"pipeline-{int(time.time())}"
        task_id = progress_tracker.create("pipeline", message="全链路任务已创建")
        cancel_event = asyncio.Event()
        self._cancel_events[task_id] = cancel_event
        self._handles[task_id] = asyncio.create_task(
            self._run(task_id, project_id, request, cancel_event)
        )
        logger.info("全链路任务已启动: task_id=%s project_id=%s", task_id, project_id)
        return task_id

    def cancel(self, task_id: str) -> bool:
        """请求取消任务（步骤间生效）。任务不存在返回 False。"""
        event = self._cancel_events.get(task_id)
        if event is None:
            return False
        event.set()
        logger.info("全链路任务取消请求: task_id=%s", task_id)
        return True

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def _run(
        self,
        task_id: str,
        project_id: str,
        request: PipelineRunRequest,
        cancel_event: asyncio.Event,
    ) -> None:
        report: dict[str, Any] = {
            "project_id": project_id,
            "premise": request.premise,
            "started_at": time.time(),
            "steps": {},
        }
        try:
            # T3 节点日志埋点：每个步骤记 start/ok/error（含耗时与关键参数）
            async with node_span(
                "pipeline.script", task_id=task_id,
                premise=request.premise[:80], genre=request.genre,
                episodes=request.episodes, scenes_per_episode=request.scenes_per_episode,
            ):
                script = await self._step_script(task_id, request, report)
            self._check_cancel(cancel_event)

            if request.generate_character_refs:
                async with node_span(
                    "pipeline.character", task_id=task_id,
                    characters=len(script.characters), style=request.style,
                ):
                    await self._step_characters(task_id, script, request, report)
                self._check_cancel(cancel_event)
            else:
                report["steps"]["character"] = {"skipped": True}

            async with node_span(
                "pipeline.storyboard", task_id=task_id,
                scenes=len(script.scenes), style=request.style,
                sketch_mode=settings.sketch_mode_enabled,
            ):
                storyboards = await self._step_storyboard(task_id, script, request, report)
            self._check_cancel(cancel_event)

            async with node_span(
                "pipeline.video", task_id=task_id,
                scenes=len(script.scenes), video_mode=request.video_mode,
                backend=settings.video_backend,
            ):
                videos = await self._step_video(task_id, script, storyboards, request, report)
            self._check_cancel(cancel_event)

            subtitles: list[dict[str, Any]] = []
            if request.video_mode == "long":
                # M21.3 长视频模式：视觉轨整体产出（单条长视频），逐场景配音/字幕的
                # 时间轴对齐属后续能力，本里程碑跳过；剪辑步骤以长视频直接作成片
                report["steps"]["voice"] = {"skipped": True, "reason": "长视频模式：视觉轨整体生成"}
                report["steps"]["subtitle"] = {"skipped": True, "reason": "长视频模式：视觉轨整体生成"}
                edit = {
                    "final_video_url": videos[0]["video_url"],
                    "duration_seconds": videos[0]["duration_seconds"],
                    "segments_count": 1,
                    "mode": "long",
                }
                report["steps"]["edit"] = edit
            else:
                async with node_span(
                    "pipeline.voice", task_id=task_id, scenes=len(script.scenes),
                ):
                    voices = await self._step_voice(task_id, script, report)
                self._check_cancel(cancel_event)

                async with node_span(
                    "pipeline.subtitle", task_id=task_id, backend=settings.asr_backend,
                ):
                    subtitles = await self._step_subtitle(task_id, voices, report)
                self._check_cancel(cancel_event)

                async with node_span(
                    "pipeline.edit", task_id=task_id, segments=len(videos),
                ):
                    edit = await self._step_edit(task_id, project_id, script, videos, voices, subtitles, request, report)
            self._check_cancel(cancel_event)

            if request.run_quality_check:
                async with node_span("pipeline.quality", task_id=task_id):
                    await self._step_quality(task_id, project_id, script, subtitles, report)
            if request.run_visual_check:
                async with node_span("pipeline.visual_quality", task_id=task_id):
                    await self._step_visual_quality(task_id, project_id, script, videos, report)

            report["passed"] = True
            report["total_elapsed_seconds"] = time.time() - report["started_at"]
            progress_tracker.update(
                task_id,
                status="completed",
                percent=100,
                message=f"全链路完成，成片: {edit.get('final_video_url', '')}",
                result=report,
            )
            logger.info("全链路任务完成: task_id=%s 耗时 %.1fs", task_id, report["total_elapsed_seconds"])
        except PipelineCancelledError:
            report["cancelled"] = True
            report["total_elapsed_seconds"] = time.time() - report["started_at"]
            progress_tracker.update(
                task_id,
                status="failed",
                percent=100,
                message="任务已被用户取消",
                error="cancelled by user",
                result=report,
            )
            logger.info("全链路任务已取消: task_id=%s", task_id)
        except Exception as e:
            report["error"] = str(e)
            report["total_elapsed_seconds"] = time.time() - report["started_at"]
            progress_tracker.update(
                task_id,
                status="failed",
                percent=100,
                message=f"全链路失败: {e}",
                error=str(e),
                result=report,
            )
            logger.exception("全链路任务失败: task_id=%s error=%s", task_id, e)
        finally:
            self._handles.pop(task_id, None)
            self._cancel_events.pop(task_id, None)

    # ------------------------------------------------------------------
    # 步骤实现
    # ------------------------------------------------------------------

    async def _step_script(
        self, task_id: str, request: PipelineRunRequest, report: dict
    ) -> Script:
        self._progress(task_id, "script", 0, "Step 1/8: 生成剧本...")
        response = await script_agent.execute(
            ScriptRequest(
                premise=request.premise,
                genre=request.genre,
                # M15.8: 画风必须传入剧本 Agent（此前漏传 → 场景 prompt 按默认写实
                # 清洗，国漫任务残留 cinematic realism / 负面词排斥 anime）
                style=request.style,
                episodes=request.episodes,
                scenes_per_episode=request.scenes_per_episode,
                monetization_mode=request.monetization_mode,
            )
        )
        if not response.success:
            raise RuntimeError(f"剧本生成失败: {response.error}")
        script = Script(**response.data)
        report["steps"]["script"] = {
            "title": script.title,
            "characters": len(script.characters),
            "scenes": len(script.scenes),
            # 完整剧本数据：供前端「加载到画布」回填，报告体积约几 KB 可接受
            "data": script.model_dump(),
        }
        self._progress(task_id, "script", 1, f"剧本完成: {script.title}（{len(script.scenes)} 场景）")
        return script

    async def _step_characters(
        self, task_id: str, script: Script, request: PipelineRunRequest, report: dict
    ) -> None:
        targets = script.characters[: request.max_character_refs]
        if not targets:
            report["steps"]["character"] = {"skipped": True, "reason": "no characters"}
            return
        self._progress(task_id, "character", 0, f"Step 2/8: 生成 {len(targets)} 个角色定妆照...")

        async def gen_one(index: int, character: Any) -> dict[str, Any]:
            response = await character_agent.execute(
                CharacterRequest(
                    character=character, style=request.style, consistency_level="L3",
                    # M18.7 资产血缘：定妆照入库标记所属剧本 project_id，
                    # 与 _collect_character_reference_images 的血缘校验口径一致
                    project_id=script.project_id,
                )
            )
            if not response.success:
                logger.warning("角色定妆照失败（跳过）: %s %s", character.name, response.error)
                return {"character_id": character.character_id, "name": character.name, "success": False, "error": response.error}
            self._progress(
                task_id,
                "character",
                (index + 1) / len(targets),
                f"角色定妆照 {index + 1}/{len(targets)}: {character.name}",
            )
            return {"character_id": character.character_id, "name": character.name, "success": True}

        results = await asyncio.gather(*[gen_one(i, c) for i, c in enumerate(targets)])
        report["steps"]["character"] = {"results": results}

    async def _step_storyboard(
        self, task_id: str, script: Script, request: PipelineRunRequest, report: dict
    ) -> list[dict[str, Any]]:
        self._progress(task_id, "storyboard", 0, f"Step 3/8: 批量生成 {len(script.scenes)} 个分镜关键帧...")
        response = await storyboard_agent.batch_execute(
            StoryboardBatchRequest(scenes=script.scenes, characters=script.characters, style=request.style)
        )
        if not response.success:
            raise RuntimeError(f"分镜生成失败: {response.error}")
        data = response.data or {}
        storyboards = data.get("results", [])
        failed = data.get("failed_scenes", [])
        if not storyboards:
            raise RuntimeError("分镜生成失败: 全部场景失败")
        report["steps"]["storyboard"] = {"count": len(storyboards), "failed_scenes": failed}
        self._progress(task_id, "storyboard", 1, f"分镜完成: {len(storyboards)} 成功 / {len(failed)} 失败")
        return storyboards

    async def _step_video(
        self,
        task_id: str,
        script: Script,
        storyboards: list[dict[str, Any]],
        request: PipelineRunRequest,
        report: dict,
    ) -> list[dict[str, Any]]:
        if request.video_mode == "long":
            return await self._step_video_long(task_id, script, storyboards, request, report)
        scene_prompt_map = {s.scene_id: s.prompt for s in script.scenes}
        scene_episode_map = {s.scene_id: s.episode for s in script.scenes}
        # M12.1 多镜 SHOT prompt 节拍视觉化：逐场景 narrative_beat 透传到 VideoRequest
        scene_beat_map = {s.scene_id: s.narrative_beat for s in script.scenes}
        # H3 ref2va 角色一致性：资产库三视图参考图注入每个 VideoRequest
        # M18.7：stats 回写陈旧跨剧本资产跳过计数，入报告 steps.video
        ref_stats: dict[str, int] = {}
        reference_images = self._collect_character_reference_images(script, stats=ref_stats)
        # M15.1 画风锚定：H3 视频 prompt 强制追加与剧本/角色/分镜一致的画风尾，
        # 负面词注入冲突画风（如写实风排斥 anime/cartoon），防止 footage 画风漂移
        anchor = resolve_style_anchor(request.style)
        video_style_tail = style_positive_tail(anchor)
        style_neg = style_negative_tail(anchor).lstrip(", ")
        video_negative = f"{style_neg}, blurry, low quality, distorted" if style_neg else ""
        items = [
            VideoRequest(
                scene_id=sb["scene_id"],
                image_url=sb["image_url"],
                prompt=(
                    (scene_prompt_map.get(sb["scene_id"], "") or "") + video_style_tail
                    if (scene_prompt_map.get(sb["scene_id"], "") or "").strip()
                    else ""
                ),
                negative_prompt=video_negative,
                duration_seconds=request.video_duration_seconds,
                reference_images=reference_images,
                episode=scene_episode_map.get(sb["scene_id"], 1),
                narrative_beat=scene_beat_map.get(sb["scene_id"], ""),
                # M18.4 画风基准透传：H3 约束层冲突词清洗与 QC 检测层均以
                # VideoRequest.style 为判定基准，漏传则两层 fail-open 静默跳过
                # （M18.5 联合 E2E 实测发现：本轮 QC 零日志即此缺口所致）
                style=request.style,
                # M17.4 流水线级全模态参考透传（仅 h3 后端 ref2va 消费）
                reference_videos=list(request.reference_videos),
                reference_audios=list(request.reference_audios),
            )
            for sb in storyboards
        ]
        # M17.3 FL2VA 链式末帧：同集相邻场景把「下一分镜关键帧」填入 last_frame_url，
        # fl2va 升级首帧+末帧双锚定；多镜组末场景的链式末帧即「组后一镜关键帧」，
        # 由 _execute_multishot_group 取作组末帧实现组间链式连续
        if settings.video_backend.lower() == "h3" and settings.h3_last_frame_chain_enabled:
            for cur, nxt in zip(items, items[1:]):
                if nxt.episode == cur.episode:
                    cur.last_frame_url = nxt.image_url
        # M24.2 锚点重拍：生成前落盘镜头参数快照（status=pending），
        # 供单镜头重拍恢复 seed/engine/prompt/lock_params 等参数
        project_id = str(report.get("project_id", task_id))
        self._save_shot_params(project_id, items)
        self._progress(task_id, "video", 0, f"Step 4/8: 批量生成 {len(items)} 个视频片段...")
        if settings.video_backend.lower() == "h3" and settings.h3_multishot_enabled:
            # M11 多镜叙事联合生成：同集相邻场景分组，≥2 场景组一次 H3 多镜推理
            videos, failed = await self._run_video_multishot(items)
        else:
            response = await video_agent.batch_execute(VideoBatchRequest(items=items))
            if not response.success:
                raise RuntimeError(f"视频生成失败: {response.error}")
            data = response.data or {}
            videos = data.get("results", [])
            failed = data.get("failed_scenes", [])
        # M24.2：合并生成结果（产物 URL + success/failed 状态）再次落盘快照；
        # 全部失败也保留快照，重拍仍可按 scene_id 恢复参数
        self._save_shot_params(project_id, items, videos=videos, failed=failed)
        if not videos:
            raise RuntimeError("视频生成失败: 全部场景失败")
        report["steps"]["video"] = {
            "count": len(videos),
            "failed_scenes": failed,
            # M18.7 收集防串戏：跳过的陈旧跨剧本资产数（0 = 参考图集无血缘污染）
            "reference_images_stale_skipped": ref_stats.get("reference_images_stale_skipped", 0),
        }
        self._progress(task_id, "video", 1, f"视频完成: {len(videos)} 成功 / {len(failed)} 失败")
        return videos

    async def _step_video_long(
        self,
        task_id: str,
        script: Script,
        storyboards: list[dict[str, Any]],
        request: PipelineRunRequest,
        report: dict,
    ) -> list[dict[str, Any]]:
        """M21.3 长视频模式：LongVideoPlanner 拆块 → LongVideoService 帧链续写。

        数据流：Script → LongVideoPlan(chunks) → chunk.prompt 序列 →
        LongVideoService.generate → 单条长视频。首帧取首个分镜关键帧，
        角色参考图/画风锚定与标准模式同源（约束层 prompt + 检测层 VLM 不变）。
        """
        if not settings.long_video_enabled:
            raise RuntimeError("长视频模式未启用（long_video_enabled=False），拒绝执行")
        if not storyboards:
            raise RuntimeError("长视频模式需要至少 1 个分镜关键帧作首帧")

        self._progress(task_id, "video", 0, "Step 4/8: 规划长视频分块...")
        try:
            plan = long_video_planner.plan(script)
        except ValueError as e:
            raise RuntimeError(f"长视频规划失败: {e}") from e
        if not plan.chunks:
            raise RuntimeError("长视频规划失败: 未产出任何块")
        report["steps"]["long_video_plan"] = plan.to_dict()
        for warning in plan.warnings:
            logger.warning("长视频规划警告: %s", warning)

        # 画风锚定/角色参考图：与标准模式同一来源，保证两种模式一致性约束一致
        anchor = resolve_style_anchor(request.style)
        style_neg = style_negative_tail(anchor).lstrip(", ")
        video_negative = f"{style_neg}, blurry, low quality, distorted" if style_neg else ""
        # M18.7：stats 回写陈旧跨剧本资产跳过计数，入报告 steps.video
        ref_stats: dict[str, int] = {}
        reference_images = self._collect_character_reference_images(script, stats=ref_stats)

        service = LongVideoService()
        try:
            result = await service.generate(
                first_frame_url=storyboards[0]["image_url"],
                chunk_prompts=[c.prompt for c in plan.chunks],
                negative_prompt=video_negative,
                reference_images=reference_images,
                style=request.style,
                progress_callback=lambda p, m: self._progress(task_id, "video", p / 100, m),
            )
        except LongVideoError as e:
            raise RuntimeError(f"长视频生成失败: {e}") from e

        report["steps"]["video"] = {
            "count": 1,
            "failed_scenes": [],
            "mode": "long",
            "chunks": result.chunks_completed,
            "duration_seconds": result.duration_seconds,
            # M18.7 收集防串戏：跳过的陈旧跨剧本资产数（与标准模式同口径）
            "reference_images_stale_skipped": ref_stats.get("reference_images_stale_skipped", 0),
        }
        self._progress(
            task_id, "video", 1,
            f"长视频完成: {result.chunks_completed} 块 / {result.duration_seconds:.1f}s",
        )
        return [{
            "scene_id": script.scenes[0].scene_id,
            "video_url": str(result.video_path),
            "duration_seconds": result.duration_seconds,
            "long_video": True,
            "chunk_count": result.chunks_completed,
        }]

    @staticmethod
    async def _run_video_multishot(
        items: list[VideoRequest],
    ) -> tuple[list[dict[str, Any]], list[int]]:
        """H3 多镜路径：分组后 ≥2 场景组调 execute_multi_shot，单场景走原 execute。

        返回 (videos, failed_scenes)，结果顺序与输入 items 一致。
        """
        groups = group_scenes_for_multishot(
            items, settings.h3_multishot_max_scenes, settings.h3_multishot_max_seconds
        )

        async def run_group(group: list[VideoRequest]) -> list[AgentResponse]:
            if len(group) >= 2:
                return await video_agent.execute_multi_shot(group)
            return [await video_agent.execute(group[0])]

        group_results = await asyncio.gather(*[run_group(g) for g in groups])
        videos: list[dict[str, Any]] = []
        failed: list[int] = []
        for group, responses in zip(groups, group_results):
            for req, resp in zip(group, responses):
                if resp.success and resp.data:
                    videos.append(resp.data)
                else:
                    failed.append(req.scene_id)
                    logger.warning("视频生成失败（跳过场景 %d）: %s", req.scene_id, resp.error)
        return videos, failed

    # ------------------------------------------------------------------
    # M24.2 锚点重拍：镜头级参数快照
    # ------------------------------------------------------------------

    @staticmethod
    def _shot_params_path(project_id: str) -> Path:
        """shot_params.json 路径（project_id 消毒防路径穿越）。"""
        safe = "".join(c for c in project_id if c.isalnum() or c in "-_")
        return PIPELINE_OUTPUT_ROOT / safe / "shot_params.json"

    def _save_shot_params(
        self,
        project_id: str,
        items: list[VideoRequest],
        videos: list[dict[str, Any]] | None = None,
        failed: list[int] | None = None,
    ) -> Path:
        """逐镜头生成参数快照落盘（M24.2 锚点重拍）。

        生成前调用一次（status=pending）锁定输入参数；生成结束再调用一次
        合并产物 URL 与 success/failed 状态。单镜头重拍时从本文件恢复
        seed/engine/prompt/reference_images/lock_params 等参数，
        其余镜头参数与产物不受影响。

        快照逐镜头包含 VideoRequest 全字段（含 M24.2 新增 seed/lock_params），
        经 model_dump(mode="json") 序列化，保证 JSON 落盘/读取往返完整。
        """
        video_by_scene = {v.get("scene_id"): v for v in (videos or [])}
        failed_set = set(failed or [])
        shots: list[dict[str, Any]] = []
        for req in items:
            entry = req.model_dump(mode="json")
            v = video_by_scene.get(req.scene_id)
            if v is not None:
                entry["status"] = "success"
                entry["video_url"] = v.get("video_url", "")
            elif req.scene_id in failed_set:
                entry["status"] = "failed"
                entry["video_url"] = ""
            else:
                entry["status"] = "pending"
                entry["video_url"] = ""
            shots.append(entry)
        payload = {
            "project_id": project_id,
            "saved_at": time.time(),
            "shots": shots,
        }
        path = self._shot_params_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("镜头参数快照已落盘: %s（%d 镜头）", path, len(shots))
        return path

    @staticmethod
    def load_shot_params(project_id: str) -> dict[str, Any] | None:
        """读取镜头参数快照（M25.1 锚点重拍）；不存在/损坏返回 None。"""
        path = PipelineOrchestrator._shot_params_path(project_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and "shots" in data else None
        except Exception:
            return None

    @staticmethod
    def update_shot_result(
        project_id: str,
        scene_id: int,
        *,
        video_url: str,
        status: str,
        seed_used: int | None = None,
    ) -> bool:
        """回写单个镜头的重拍结果（M25.1）。

        仅更新目标 scene 的 video_url/status/rerun_at/seed_used，
        其余镜头字段保持不变（失败隔离）。快照不存在或无该镜头返回 False。
        """
        path = PipelineOrchestrator._shot_params_path(project_id)
        snapshot = PipelineOrchestrator.load_shot_params(project_id)
        if snapshot is None:
            return False
        shot = next(
            (s for s in snapshot.get("shots", []) if s.get("scene_id") == scene_id),
            None,
        )
        if shot is None:
            return False
        shot["video_url"] = video_url
        shot["status"] = status
        shot["rerun_at"] = time.time()
        if seed_used is not None:
            shot["seed"] = seed_used
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("镜头重拍结果已回写: %s scene=%d status=%s", path, scene_id, status)
        return True

    @staticmethod
    def _collect_character_reference_images(
        script: Script, stats: dict[str, int] | None = None
    ) -> list[str]:
        """汇总剧本全部角色在资产库中的三视图参考图（H3 ref2va 角色一致性）。

        Scene 无角色关联字段，故取剧本全部角色；按角色顺序合并
        reference_images，去重、保序、过滤空 URL，总量截断到
        h3_ref_max_images（视频 Agent 侧关键帧再占 1 席后最终截断）。

        M18.7 收集防串戏：资产带 source_script_id 且与当前剧本 project_id 不一致时
        跳过该角色——陈旧跨剧本资产（M18.6 实测新剧本角色被 QC 拦截后静默命中旧剧本
        同 ID 资产，ref2va 参考与漂移对照基准双双错配）。legacy 无血缘字段资产仅在
        该角色本轮未重新生成时才可能残留（重新生成会覆盖写入血缘；QC 拦截已隔离删除），
        兜底允许使用并记 info 日志提示陈旧。stats 非空时回写
        reference_images_stale_skipped（跳过的陈旧资产角色数，报告 steps.video 消费）。
        """
        refs: list[str] = []
        seen: set[str] = set()
        stale_skipped = 0
        current_script_id = script.project_id
        for character in script.characters:
            asset = character_library.get(character.character_id)
            if asset is None:
                continue
            # M18.7 血缘校验：带血缘且与当前剧本不一致 → 陈旧跨剧本资产，跳过防串戏
            if asset.source_script_id and asset.source_script_id != current_script_id:
                stale_skipped += 1
                logger.info(
                    "角色 %s 资产血缘 %s 与当前剧本 %s 不一致，跳过陈旧参考图（防串戏）",
                    character.character_id, asset.source_script_id, current_script_id,
                )
                continue
            if not asset.source_script_id:
                # legacy 无血缘资产（M18.7 前入库）：兜底可用，提示陈旧建议重新生成
                logger.info(
                    "角色 %s 使用 legacy 无血缘资产参考图（陈旧资产兜底，建议重新生成定妆照）",
                    character.character_id,
                )
            for url in asset.reference_images.values():
                url = (url or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    refs.append(url)
        if stats is not None:
            stats["reference_images_stale_skipped"] = stale_skipped
        return refs[: settings.h3_ref_max_images]

    async def _step_voice(
        self, task_id: str, script: Script, report: dict
    ) -> list[dict[str, Any]]:
        scenes_with_dialogue = [s for s in script.scenes if s.dialogue.strip() or s.description]
        if not scenes_with_dialogue:
            report["steps"]["voice"] = {"skipped": True, "reason": "no dialogue"}
            return []
        self._progress(task_id, "voice", 0, f"Step 5/8: 生成 {len(scenes_with_dialogue)} 个场景配音...")

        async def gen_one(scene: Scene) -> dict[str, Any] | None:
            dialogues = self._build_dialogues(scene)
            response = await voice_agent.execute(VoiceRequest(scene_id=scene.scene_id, dialogues=dialogues))
            if not response.success:
                logger.warning("配音失败（跳过场景 %d）: %s", scene.scene_id, response.error)
                return None
            return response.data

        results = await asyncio.gather(*[gen_one(s) for s in scenes_with_dialogue])
        voices = [v for v in results if v is not None]
        report["steps"]["voice"] = {"count": len(voices), "failed": len(scenes_with_dialogue) - len(voices)}
        self._progress(task_id, "voice", 1, f"配音完成: {len(voices)}/{len(scenes_with_dialogue)} 场景")
        return voices

    async def _step_subtitle(
        self, task_id: str, voices: list[dict[str, Any]], report: dict
    ) -> list[dict[str, Any]]:
        if not voices:
            report["steps"]["subtitle"] = {"skipped": True, "reason": "no voices"}
            return []
        self._progress(task_id, "subtitle", 0, f"Step 6/8: 生成 {len(voices)} 个场景字幕...")

        async def gen_one(voice: dict[str, Any]) -> dict[str, Any] | None:
            audio_urls = voice.get("audio_urls") or []
            if not audio_urls:
                return None
            response = await subtitle_agent.execute(
                SubtitleRequest(scene_id=voice["scene_id"], audio_url=audio_urls[0]["audio_url"])
            )
            if not response.success:
                logger.warning("字幕失败（跳过场景 %d）: %s", voice["scene_id"], response.error)
                return None
            return response.data

        results = await asyncio.gather(*[gen_one(v) for v in voices])
        subtitles = [s for s in results if s is not None]
        report["steps"]["subtitle"] = {"count": len(subtitles), "failed": len(voices) - len(subtitles)}
        self._progress(task_id, "subtitle", 1, f"字幕完成: {len(subtitles)}/{len(voices)} 场景")
        return subtitles

    async def _step_edit(
        self,
        task_id: str,
        project_id: str,
        script: Script,
        videos: list[dict[str, Any]],
        voices: list[dict[str, Any]],
        subtitles: list[dict[str, Any]],
        request: PipelineRunRequest,
        report: dict,
    ) -> dict[str, Any]:
        video_map = {v["scene_id"]: v for v in videos}
        voice_map = {v["scene_id"]: v for v in voices}
        subtitle_map = {s["scene_id"]: s for s in subtitles}

        segments: list[EditSegment] = []
        for scene in script.scenes:
            sid = scene.scene_id
            if sid not in video_map or sid not in voice_map or sid not in subtitle_map:
                continue
            audio_urls = voice_map[sid].get("audio_urls") or []
            if not audio_urls:
                continue
            dialogue = str(getattr(scene, "dialogue", "") or "").strip()
            segments.append(
                EditSegment(
                    scene_id=sid,
                    video_url=video_map[sid]["video_url"],
                    audio_url=audio_urls[0]["audio_url"],
                    audio_type="dialogue" if dialogue else "narration",
                    subtitle_url=subtitle_map[sid].get("srt_url") or f"/static/subtitle/scene_{sid}.srt",
                    duration_seconds=video_map[sid].get("duration_seconds", request.video_duration_seconds),
                )
            )
        if not segments:
            raise RuntimeError("没有可合成的片段（视频/配音/字幕未同时就绪）")

        self._progress(task_id, "edit", 0, f"Step 7/8: 合成成片（{len(segments)} 片段）...")
        response = await edit_agent.execute(
            EditRequest(
                project_id=project_id,
                title=script.title,
                segments=segments,
                transition="fade",
                output_resolution=request.output_resolution,
                output_fps=request.output_fps,
                ai_label_enabled=request.ai_label_enabled,
                license_number=request.license_number,
            )
        )
        if not response.success:
            raise RuntimeError(f"剪辑合成失败: {response.error}")
        edit = response.data
        report["steps"]["edit"] = {
            "final_video_url": edit.get("final_video_url"),
            "duration_seconds": edit.get("duration_seconds"),
            "segments_count": edit.get("segments_count"),
        }
        self._progress(task_id, "edit", 1, f"成片完成: {edit.get('duration_seconds', 0):.1f}s")
        return edit

    async def _step_quality(
        self,
        task_id: str,
        project_id: str,
        script: Script,
        subtitles: list[dict[str, Any]],
        report: dict,
    ) -> None:
        self._progress(task_id, "quality", 0, "Step 8/8: 文本质检...")
        try:
            response = await quality_agent.execute(
                QualityCheckRequest(
                    project_id=project_id,
                    title=script.title,
                    characters=script.characters,
                    scenes=script.scenes,
                    subtitles=subtitles,
                )
            )
            if response.success:
                data = response.data or {}
                report["steps"]["quality"] = {"score": data.get("score"), "issues": len(data.get("issues", []))}
                self._progress(task_id, "quality", 1, f"质检完成: {data.get('score')} 分")
            else:
                report["steps"]["quality"] = {"skipped": True, "reason": response.error}
                logger.warning("质检失败（非致命）: %s", response.error)
        except Exception as e:
            report["steps"]["quality"] = {"skipped": True, "reason": str(e)}
            logger.warning("质检异常（非致命）: %s", e)

    async def _step_visual_quality(
        self,
        task_id: str,
        project_id: str,
        script: Script,
        videos: list[dict[str, Any]],
        report: dict,
    ) -> None:
        """M13 角色一致性对照视觉检测（可选步骤，非致命）。

        对每个已生成视频的场景，附带角色资产库定妆参考图调 visual_quality_agent
        做漂移对照检测；单场景失败仅记录，不阻断流水线。
        """
        if not videos:
            report["steps"]["visual_quality"] = {"skipped": True, "reason": "no videos"}
            return
        ref_urls = self._collect_character_reference_images(script)
        if not ref_urls:
            report["steps"]["visual_quality"] = {"skipped": True, "reason": "no character reference images"}
            return
        self._progress(task_id, "quality", 0, f"视觉质检: {len(videos)} 个场景对照检测...")
        try:
            async def check_one(video: dict[str, Any]) -> dict[str, Any]:
                scene_id = int(video.get("scene_id", 0))
                response = await visual_quality_agent.execute(
                    QualityVisualRequest(
                        project_id=project_id,
                        title=script.title,
                        scene_id=scene_id,
                        video_url=video.get("video_url", ""),
                        reference_image_urls=ref_urls,
                    )
                )
                if not response.success or not response.data:
                    return {"scene_id": scene_id, "success": False, "error": response.error}
                return {
                    "scene_id": scene_id,
                    "success": True,
                    "score": response.data.get("score"),
                    "drift_detected": bool(response.data.get("drift_detected", False)),
                }

            results = await asyncio.gather(*[check_one(v) for v in videos])
            drift_scenes = [r["scene_id"] for r in results if r.get("drift_detected")]
            failed = [r["scene_id"] for r in results if not r.get("success")]
            report["steps"]["visual_quality"] = {
                "checked": len(results) - len(failed),
                "failed_scenes": failed,
                "drift_scenes": drift_scenes,
                "results": results,
            }
            self._progress(
                task_id, "quality", 1,
                f"视觉质检完成: {len(results) - len(failed)}/{len(videos)} 场景，漂移 {len(drift_scenes)} 处",
            )
        except Exception as e:
            report["steps"]["visual_quality"] = {"skipped": True, "reason": str(e)}
            logger.warning("视觉质检异常（非致命）: %s", e)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _progress(self, task_id: str, step: str, ratio: float, message: str) -> None:
        """按步骤区间映射整体进度百分比。"""
        start, end = _PROGRESS[step]
        percent = int(start + (end - start) * max(0.0, min(1.0, ratio)))
        progress_tracker.update(task_id, status="running", percent=percent, message=message)

    @staticmethod
    def _check_cancel(cancel_event: asyncio.Event) -> None:
        if cancel_event.is_set():
            raise PipelineCancelledError

    @staticmethod
    def _build_dialogues(scene: Scene) -> list[DialogueLine]:
        """从场景台词构建配音输入（与 e2e 脚本逻辑一致）。"""
        dialogues: list[DialogueLine] = []
        for line in scene.dialogue.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            dialogues.append(
                DialogueLine(
                    text=line,
                    character_name=scene.character_actions[:10],
                    character_role="主角" if "主角" in scene.character_actions else "配角",
                )
            )
        if not dialogues:
            dialogues.append(
                DialogueLine(
                    text=scene.description or "场景无言",
                    character_name="旁白",
                    character_role="narrator",
                )
            )
        return dialogues


# 全局编排器实例
pipeline_orchestrator = PipelineOrchestrator()
