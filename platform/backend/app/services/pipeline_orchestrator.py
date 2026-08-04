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
import logging
import time
from typing import Any

from app.agents.character_agent import character_agent
from app.agents.edit_agent import edit_agent
from app.agents.quality_agent import quality_agent
from app.agents.script_agent import script_agent
from app.agents.storyboard_agent import storyboard_agent
from app.agents.subtitle_agent import subtitle_agent
from app.agents.video_agent import video_agent
from app.agents.voice_agent import voice_agent
from app.core.progress import progress_tracker
from app.models.schemas import (
    CharacterRequest,
    DialogueLine,
    EditRequest,
    EditSegment,
    PipelineRunRequest,
    QualityCheckRequest,
    Scene,
    Script,
    ScriptRequest,
    StoryboardBatchRequest,
    SubtitleRequest,
    VideoBatchRequest,
    VideoRequest,
    VoiceRequest,
)

logger = logging.getLogger(__name__)

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
            script = await self._step_script(task_id, request, report)
            self._check_cancel(cancel_event)

            if request.generate_character_refs:
                await self._step_characters(task_id, script, request, report)
                self._check_cancel(cancel_event)
            else:
                report["steps"]["character"] = {"skipped": True}

            storyboards = await self._step_storyboard(task_id, script, request, report)
            self._check_cancel(cancel_event)

            videos = await self._step_video(task_id, script, storyboards, request, report)
            self._check_cancel(cancel_event)

            voices = await self._step_voice(task_id, script, report)
            self._check_cancel(cancel_event)

            subtitles = await self._step_subtitle(task_id, voices, report)
            self._check_cancel(cancel_event)

            edit = await self._step_edit(task_id, project_id, script, videos, voices, subtitles, request, report)
            self._check_cancel(cancel_event)

            if request.run_quality_check:
                await self._step_quality(task_id, project_id, script, subtitles, report)

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
                CharacterRequest(character=character, style=request.style, consistency_level="L3")
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
        scene_prompt_map = {s.scene_id: s.prompt for s in script.scenes}
        items = [
            VideoRequest(
                scene_id=sb["scene_id"],
                image_url=sb["image_url"],
                prompt=scene_prompt_map.get(sb["scene_id"], ""),
                negative_prompt="",
                duration_seconds=request.video_duration_seconds,
            )
            for sb in storyboards
        ]
        self._progress(task_id, "video", 0, f"Step 4/8: 批量生成 {len(items)} 个视频片段...")
        response = await video_agent.batch_execute(VideoBatchRequest(items=items))
        if not response.success:
            raise RuntimeError(f"视频生成失败: {response.error}")
        data = response.data or {}
        videos = data.get("results", [])
        failed = data.get("failed_scenes", [])
        if not videos:
            raise RuntimeError("视频生成失败: 全部场景失败")
        report["steps"]["video"] = {"count": len(videos), "failed_scenes": failed}
        self._progress(task_id, "video", 1, f"视频完成: {len(videos)} 成功 / {len(failed)} 失败")
        return videos

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
            segments.append(
                EditSegment(
                    scene_id=sid,
                    video_url=video_map[sid]["video_url"],
                    audio_url=audio_urls[0]["audio_url"],
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
