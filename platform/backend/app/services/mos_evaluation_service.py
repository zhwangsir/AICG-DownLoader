"""M22.2 路线对比实验 —— 视频质量 MOS 评分服务。

MOS（Mean Opinion Score）实现：对视频均匀抽帧，VLM 按四维度 1-5 分制评分，
全帧全维度取均值得 MOS。四维度：
1. visual_quality      画质清晰度（细节/噪点/压缩伪影）
2. motion_naturalness  运动自然度（动作流畅/物理合理/无抖动）
3. temporal_consistency 时序一致性（与前一采样帧对比：身份/光影/场景无跳变）
4. text_alignment      文本对齐度（画面内容与输入 prompt 语义的匹配程度）

设计约束与 DriftMetricsService 一致：VLM 调用 fail-open（失败帧记 None 不阻断），
复用其 _default_vlm_caller（spark02 qwen3.6-uncensored 多模态）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.drift_metrics_service import (
    DriftMetricsService,
    VlmJsonCaller,
    extract_frame_at,
)
from app.services.long_video_service import probe_video_duration

logger = logging.getLogger(__name__)

MOS_DIMENSIONS = (
    "visual_quality",
    "motion_naturalness",
    "temporal_consistency",
    "text_alignment",
)

MOS_SCORE_PROMPT = """这是从一段视频中抽取的第 {frame_no} 帧（共抽取 {total_frames} 帧）。{prev_note}
该视频的输入 prompt 是：
「{prompt}」
请按 1-5 分制为以下四个维度评分（1=很差，2=较差，3=一般，4=良好，5=优秀）：
- visual_quality：画质清晰度（细节丰富度、噪点、压缩伪影）
- motion_naturalness：运动自然度（从画面姿态/运动模糊判断动作是否流畅合理）
- temporal_consistency：时序一致性（与前一帧对比，人物身份/服装/光影/场景是否连续无跳变；首帧无对比对象时按画面自身稳定性评分）
- text_alignment：文本对齐度（画面内容与 prompt 语义的匹配程度）

只输出一个 JSON 对象：
- visual_quality / motion_naturalness / temporal_consistency / text_alignment：各 1-5 整数
- reason：一句话说明主要扣分点（无扣分填空字符串）
不要 markdown 代码块，不要解释。"""


@dataclass
class MosFrameScores:
    """单帧四维度评分（VLM 失败维度为 None）。"""

    frame_index: int
    scores: dict[str, float | None] = field(default_factory=dict)
    reason: str = ""


@dataclass
class MosReport:
    """MOS 评分报告：逐帧明细 + 维度均值 + 总 MOS。"""

    frames: list[MosFrameScores] = field(default_factory=list)
    dimension_means: dict[str, float | None] = field(default_factory=dict)
    mos: float | None = None  # 全维度全帧均值（1-5）
    frames_scored: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": [
                {"frame_index": f.frame_index, "scores": f.scores, "reason": f.reason}
                for f in self.frames
            ],
            "dimension_means": self.dimension_means,
            "mos": self.mos,
            "frames_scored": self.frames_scored,
        }


class MosEvaluationService:
    """视频质量 MOS 评分：抽帧 → VLM 四维度打分 → 均值聚合。"""

    def __init__(self, vlm_caller: VlmJsonCaller | None = None):
        self._vlm_caller = vlm_caller or DriftMetricsService._default_vlm_caller

    @staticmethod
    def _score(data: dict[str, Any] | None, key: str) -> float | None:
        """从 VLM JSON 安全取 1-5 整数分（含 null/越界/非数值容错）。"""
        if not data:
            return None
        v = data.get(key)
        if v is None or isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if math.isnan(f) or not 1 <= f <= 5:
            return None
        return f

    async def score_frame(
        self,
        frame_path: Path,
        prompt: str,
        frame_no: int,
        total_frames: int,
        prev_frame_path: Path | None = None,
    ) -> MosFrameScores:
        """单帧四维度 MOS 打分（可选前一帧作时序一致性对比）。"""
        from app.services.drift_metrics_service import DriftMetricsService as _dms

        prev_note = "同时提供了该视频的前一采样帧（第 2 张图）供时序一致性对比。" if prev_frame_path else "这是首个采样帧，无前一帧对比对象。"
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": MOS_SCORE_PROMPT.format(
                frame_no=frame_no, total_frames=total_frames,
                prompt=prompt or "未指定", prev_note=prev_note,
            ),
        }]
        if prev_frame_path is not None:
            content.append(_dms._image_part(prev_frame_path))
        content.append(_dms._image_part(frame_path))

        data = await self._vlm_caller(content)
        scores = {dim: self._score(data, dim) for dim in MOS_DIMENSIONS}
        reason = str(data.get("reason", "") or "") if data else ""
        return MosFrameScores(frame_index=frame_no - 1, scores=scores, reason=reason[:200])

    async def evaluate(
        self,
        video_path: Path,
        prompt: str = "",
        num_frames: int = 6,
        work_dir: Path | None = None,
    ) -> MosReport:
        """对整段视频抽帧评分，产出 MOS 报告。"""
        video_path = Path(video_path)
        if not video_path.exists():
            raise ValueError(f"视频不存在: {video_path}")
        duration = await probe_video_duration(video_path)
        work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="mos_eval_"))
        work.mkdir(parents=True, exist_ok=True)

        # 均匀取点（避开首尾 0.2s 编码边界）
        lo, hi = 0.2, max(0.2, duration - 0.2)
        if num_frames <= 1:
            times = [duration / 2]
        else:
            step = (hi - lo) / (num_frames - 1)
            times = [lo + step * i for i in range(num_frames)]

        frames: list[Path] = []
        for i, t in enumerate(times):
            fp = work / f"mos_frame{i:02d}.png"
            try:
                await extract_frame_at(video_path, t, fp)
                frames.append(fp)
            except Exception as e:  # noqa: BLE001 —— 单帧失败跳过
                logger.warning("MOS 抽帧失败 @%.2fs: %s", t, e)

        report = MosReport()
        # 逐帧打分（并发；时序一致性传前一帧）
        prev_map = {i: frames[i - 1] for i in range(1, len(frames))}

        async def _one(i: int, fp: Path) -> MosFrameScores:
            return await self.score_frame(
                fp, prompt, i + 1, len(frames), prev_map.get(i),
            )

        results = await asyncio.gather(*(_one(i, fp) for i, fp in enumerate(frames)))
        report.frames = list(results)
        report.frames_scored = sum(
            1 for f in results if any(v is not None for v in f.scores.values())
        )

        # 维度均值 + 总 MOS
        all_vals: list[float] = []
        for dim in MOS_DIMENSIONS:
            vals = [f.scores[dim] for f in results if f.scores.get(dim) is not None]
            report.dimension_means[dim] = (
                round(sum(vals) / len(vals), 2) if vals else None
            )
            all_vals.extend(vals)
        report.mos = round(sum(all_vals) / len(all_vals), 2) if all_vals else None
        return report
