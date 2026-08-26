"""M21 漂移量化评估服务单元测试 —— mock VLM / ffmpeg，不触真实 GPU。

覆盖：
- compute_pixel_diff / linear_slope / _sample_times 确定性工具
- 三项指标打分（fidelity / emotion / action_continuity）的 VLM 解析与 fail-open
- evaluate 主编排：逐块逐缝采集、趋势斜率、异常标注、建议生成
- render_markdown_report 结构
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.drift_metrics_service import (
    ACTION_ANOMALY_THRESHOLD,
    FIDELITY_ANOMALY_THRESHOLD,
    SEAM_DIFF_ANOMALY_THRESHOLD,
    ChunkMetrics,
    DriftMetricsReport,
    DriftMetricsService,
    SeamMetrics,
    compute_pixel_diff,
    linear_slope,
    render_markdown_report,
)


def _png(path: Path, gray: int) -> Path:
    """生成纯色灰度 PNG（像素差测试用）。"""
    from PIL import Image

    Image.new("L", (32, 32), color=gray).save(path, "PNG")
    return path


# ---------------------------------------------------------------------------
# 确定性工具
# ---------------------------------------------------------------------------
class TestComputePixelDiff:
    def test_identical_images_zero_diff(self, tmp_path):
        a = _png(tmp_path / "a.png", 128)
        b = _png(tmp_path / "b.png", 128)
        assert compute_pixel_diff(a, b) == pytest.approx(0.0, abs=0.01)

    def test_black_white_max_diff(self, tmp_path):
        a = _png(tmp_path / "a.png", 0)
        b = _png(tmp_path / "b.png", 255)
        assert compute_pixel_diff(a, b) == pytest.approx(255.0, abs=1.0)

    def test_small_diff_proportional(self, tmp_path):
        a = _png(tmp_path / "a.png", 100)
        b = _png(tmp_path / "b.png", 120)
        diff = compute_pixel_diff(a, b)
        assert 15 < diff < 25  # ≈20，容差吸收缩放/色深误差


class TestLinearSlope:
    def test_constant_series_zero_slope(self):
        assert linear_slope([80.0, 80.0, 80.0]) == pytest.approx(0.0)

    def test_declining_series_negative_slope(self):
        # 90/80/70/60 → 斜率 -10
        assert linear_slope([90.0, 80.0, 70.0, 60.0]) == pytest.approx(-10.0)

    def test_single_point_none(self):
        assert linear_slope([80.0]) is None
        assert linear_slope([]) is None


class TestSampleTimes:
    def test_three_points_evenly_spaced(self):
        times = DriftMetricsService._sample_times(14.0, 3)
        assert len(times) == 3
        assert times[0] == pytest.approx(0.1)
        assert times[1] == pytest.approx(7.0, abs=0.2)
        assert times[2] == pytest.approx(13.9)

    def test_short_duration_single_midpoint(self):
        assert DriftMetricsService._sample_times(0.1, 3) == [0.05]


# ---------------------------------------------------------------------------
# 分数解析容错
# ---------------------------------------------------------------------------
class TestScoreParsing:
    @pytest.mark.parametrize("raw,expected", [
        (85, 85.0),
        ("72", 72.0),
        (None, None),
        (True, None),
        (-5, None),
        (120, None),
        ("abc", None),
        (float("nan"), None),
    ])
    def test_score_edge_cases(self, raw, expected):
        assert DriftMetricsService._score({"score": raw}, "score") == expected

    def test_missing_key(self):
        assert DriftMetricsService._score({}, "score") is None
        assert DriftMetricsService._score(None, "score") is None


# ---------------------------------------------------------------------------
# 指标 1：角色特征保持度
# ---------------------------------------------------------------------------
class TestCharacterFidelity:
    async def test_present_scored(self, tmp_path):
        frame = _png(tmp_path / "f.png", 100)
        ref = _png(tmp_path / "r.png", 100)

        async def vlm(content):
            return {"character_present": True, "score": 88, "reason": ""}

        svc = DriftMetricsService(vlm_caller=vlm)
        score, drift, present, _ = await svc.score_character_fidelity([ref], frame, 1)
        assert score == 88.0
        assert drift is False
        assert present is True

    async def test_low_score_marks_drift(self, tmp_path):
        frame = _png(tmp_path / "f.png", 100)
        ref = _png(tmp_path / "r.png", 100)

        async def vlm(content):
            return {"character_present": True, "score": 40, "reason": "服装颜色不同"}

        svc = DriftMetricsService(vlm_caller=vlm)
        score, drift, present, detail = await svc.score_character_fidelity([ref], frame, 2)
        assert score == 40.0
        assert drift is True
        assert "服装" in detail

    async def test_character_absent_exempt(self, tmp_path):
        frame = _png(tmp_path / "f.png", 100)
        ref = _png(tmp_path / "r.png", 100)

        async def vlm(content):
            return {"character_present": False, "score": None, "reason": "参考角色未出镜"}

        svc = DriftMetricsService(vlm_caller=vlm)
        score, drift, present, _ = await svc.score_character_fidelity([ref], frame, 3)
        assert score is None
        assert drift is False
        assert present is False

    async def test_no_refs_returns_none(self, tmp_path):
        frame = _png(tmp_path / "f.png", 100)
        svc = DriftMetricsService(vlm_caller=AsyncMock())
        score, drift, present, _ = await svc.score_character_fidelity([], frame, 1)
        assert (score, drift, present) == (None, False, True)

    async def test_vlm_failure_fail_open(self, tmp_path):
        frame = _png(tmp_path / "f.png", 100)
        ref = _png(tmp_path / "r.png", 100)

        async def vlm(content):
            return None

        svc = DriftMetricsService(vlm_caller=vlm)
        score, drift, present, _ = await svc.score_character_fidelity([ref], frame, 1)
        assert (score, drift, present) == (None, False, True)


# ---------------------------------------------------------------------------
# 指标 2/3：情感一致性 / 动作连贯性
# ---------------------------------------------------------------------------
class TestEmotionAndAction:
    async def test_emotion_scored(self, tmp_path):
        frame = _png(tmp_path / "f.png", 100)

        async def vlm(content):
            # 意图文本应进入 prompt
            assert "雨夜告别" in content[0]["text"]
            return {"observed_emotion": "悲伤", "score": 90, "reason": ""}

        svc = DriftMetricsService(vlm_caller=vlm)
        score, observed = await svc.score_emotion_consistency(frame, "雨夜告别的悲伤", 1)
        assert score == 90.0
        assert observed == "悲伤"

    async def test_emotion_vlm_fail_open(self, tmp_path):
        frame = _png(tmp_path / "f.png", 100)
        svc = DriftMetricsService(vlm_caller=AsyncMock(return_value=None))
        score, observed = await svc.score_emotion_consistency(frame, "x", 1)
        assert (score, observed) == (None, "")

    async def test_action_continuity_scored(self, tmp_path):
        a = _png(tmp_path / "a.png", 100)
        b = _png(tmp_path / "b.png", 105)

        async def vlm(content):
            return {"score": 85, "motion_jump": False, "reason": ""}

        svc = DriftMetricsService(vlm_caller=vlm)
        score, jump, _ = await svc.score_action_continuity(a, b)
        assert score == 85.0
        assert jump is False

    async def test_action_jump_detected(self, tmp_path):
        a = _png(tmp_path / "a.png", 100)
        b = _png(tmp_path / "b.png", 200)

        async def vlm(content):
            return {"score": 30, "motion_jump": True, "reason": "场景突变"}

        svc = DriftMetricsService(vlm_caller=vlm)
        score, jump, detail = await svc.score_action_continuity(a, b)
        assert score == 30.0
        assert jump is True
        assert "突变" in detail


# ---------------------------------------------------------------------------
# evaluate 主编排（mock 抽帧与 VLM）
# ---------------------------------------------------------------------------
def _mock_ffmpeg_extractors(monkeypatch):
    """抽帧全部 mock 为生成纯色 PNG；时长探测 mock 为 14s。"""

    async def fake_extract_at(video: Path, t: float, out: Path) -> Path:
        return _png(out, 100)

    async def fake_extract_last(video: Path, out: Path) -> Path:
        return _png(out, 100)

    monkeypatch.setattr(
        "app.services.drift_metrics_service.extract_frame_at", fake_extract_at
    )
    monkeypatch.setattr(
        "app.services.long_video_service.extract_last_frame", fake_extract_last
    )
    monkeypatch.setattr(
        DriftMetricsService, "_safe_duration", AsyncMock(return_value=14.0)
    )


class TestEvaluate:
    async def test_full_flow_healthy(self, monkeypatch, tmp_path):
        _mock_ffmpeg_extractors(monkeypatch)
        chunks = [tmp_path / f"chunk_{i:02d}.mp4" for i in range(4)]
        for c in chunks:
            c.write_bytes(b"fake")

        async def vlm(content):
            text = content[0]["text"]
            if "角色特征保持度" in text:
                return {"character_present": True, "score": 85, "reason": ""}
            if "情绪基调" in text:
                return {"observed_emotion": "紧张", "score": 80, "reason": ""}
            return {"score": 88, "motion_jump": False, "reason": ""}

        svc = DriftMetricsService(vlm_caller=vlm)
        report = await svc.evaluate(
            chunks,
            reference_image_paths=[tmp_path / "ref.png"] if _png(tmp_path / "ref.png", 100) else None,
            chunk_intents=["紧张追逐"] * 4,
            work_dir=tmp_path / "work",
        )
        assert len(report.chunks) == 4
        assert len(report.seams) == 3
        assert all(c.character_fidelity == 85.0 for c in report.chunks)
        assert report.fidelity_slope == pytest.approx(0.0)
        assert report.anomalies == []
        assert any("健康区间" in s for s in report.suggestions)

    async def test_drift_accumulation_detected(self, monkeypatch, tmp_path):
        """保持度逐块 90→60 下滑：斜率告警 + 累积建议 + 末块异常标注。"""
        _mock_ffmpeg_extractors(monkeypatch)
        chunks = [tmp_path / f"chunk_{i:02d}.mp4" for i in range(4)]
        for c in chunks:
            c.write_bytes(b"fake")
        _png(tmp_path / "ref.png", 100)

        # 按调用次序模拟逐块递减（每块 3 帧 fidelity + 3 帧 emotion + 缝 action）
        fidelity_iter = iter([90.0] * 3 + [80.0] * 3 + [70.0] * 3 + [60.0] * 3)

        async def vlm(content):
            text = content[0]["text"]
            if "角色特征保持度" in text:
                return {"character_present": True, "score": next(fidelity_iter), "reason": ""}
            if "情绪基调" in text:
                return {"observed_emotion": "平静", "score": 85, "reason": ""}
            return {"score": 90, "motion_jump": False, "reason": ""}

        svc = DriftMetricsService(vlm_caller=vlm)
        report = await svc.evaluate(
            chunks,
            reference_image_paths=[tmp_path / "ref.png"],
            chunk_intents=[""] * 4,
            work_dir=tmp_path / "work",
        )
        assert report.fidelity_slope == pytest.approx(-10.0)
        assert report.fidelity_endpoint_delta == pytest.approx(-30.0)
        assert any("漂移累积" in s for s in report.suggestions)

    async def test_seam_anomaly_annotated(self, monkeypatch, tmp_path):
        """接缝像素差超阈值 → 异常标注 + 接缝建议。"""
        chunks = [tmp_path / f"chunk_{i:02d}.mp4" for i in range(2)]
        for c in chunks:
            c.write_bytes(b"fake")

        async def fake_extract_at(video: Path, t: float, out: Path) -> Path:
            return _png(out, 100)

        async def fake_extract_last(video: Path, out: Path) -> Path:
            return _png(out, 250)  # 末帧与下块首帧差异巨大

        monkeypatch.setattr("app.services.drift_metrics_service.extract_frame_at", fake_extract_at)
        monkeypatch.setattr("app.services.long_video_service.extract_last_frame", fake_extract_last)
        monkeypatch.setattr(DriftMetricsService, "_safe_duration", AsyncMock(return_value=14.0))

        async def vlm(content):
            text = content[0]["text"]
            if "情绪基调" in text:
                return {"observed_emotion": "平静", "score": 85, "reason": ""}
            return {"score": 40, "motion_jump": True, "reason": "场景突变"}

        svc = DriftMetricsService(vlm_caller=vlm)
        report = await svc.evaluate(chunks, chunk_intents=["", ""], work_dir=tmp_path / "work")
        assert report.seams[0].pixel_diff > SEAM_DIFF_ANOMALY_THRESHOLD
        assert any("接缝跳变" in a or "跳剪" in a for a in report.anomalies)
        assert any("交叉淡化" in s for s in report.suggestions)

    async def test_empty_chunks_raises(self, tmp_path):
        svc = DriftMetricsService(vlm_caller=AsyncMock())
        with pytest.raises(ValueError, match="chunk_paths 为空"):
            await svc.evaluate([], work_dir=tmp_path)


# ---------------------------------------------------------------------------
# 趋势分析规则（纯逻辑，不经 evaluate）
# ---------------------------------------------------------------------------
class TestTrendRules:
    def _analyze(self, chunks, seams):
        report = DriftMetricsReport(chunks=chunks, seams=seams)
        DriftMetricsService(vlm_caller=AsyncMock())._analyze_trends(report)
        return report

    def test_first_chunk_low_suggestion(self):
        chunks = [ChunkMetrics(chunk_index=i, character_fidelity=v)
                  for i, v in enumerate([50.0, 55.0, 52.0])]
        report = self._analyze(chunks, [])
        assert any("首块" in s for s in report.suggestions)

    def test_emotion_decline_suggestion(self):
        chunks = [ChunkMetrics(chunk_index=i, character_fidelity=85.0, emotion_consistency=e)
                  for i, e in enumerate([90.0, 78.0, 66.0, 54.0])]
        report = self._analyze(chunks, [])
        assert report.emotion_slope == pytest.approx(-12.0)
        assert any("情绪基调" in s for s in report.suggestions)
        # 末块 54 < 60 → 异常标注
        assert any("情感一致性" in a for a in report.anomalies)

    def test_action_low_anomaly(self):
        seams = [SeamMetrics(seam_index=0, pixel_diff=10.0,
                             action_continuity=ACTION_ANOMALY_THRESHOLD - 5)]
        report = self._analyze([ChunkMetrics(chunk_index=0)], seams)
        assert any("动作连贯性" in a for a in report.anomalies)

    def test_none_metrics_no_false_anomaly(self):
        """VLM 全失败（None）时不产生误报异常。"""
        chunks = [ChunkMetrics(chunk_index=i) for i in range(3)]
        report = self._analyze(chunks, [SeamMetrics(seam_index=0), SeamMetrics(seam_index=1)])
        assert report.anomalies == []
        assert report.fidelity_slope is None


# ---------------------------------------------------------------------------
# Markdown 报告渲染
# ---------------------------------------------------------------------------
class TestRenderReport:
    def test_structure(self):
        report = DriftMetricsReport(
            chunks=[ChunkMetrics(chunk_index=0, character_fidelity=85.0, emotion_consistency=80.0)],
            seams=[SeamMetrics(seam_index=0, pixel_diff=9.0, action_continuity=88.0)],
            fidelity_slope=-1.5,
            anomalies=["样例异常"],
            suggestions=["样例建议"],
        )
        md = render_markdown_report(report, {"测试时长": "56s", "块数": 4})
        assert "角色漂移累积观测报告" in md
        assert "测试时长" in md
        assert "| 1 | 85 | 80 |" in md
        assert "9.0" in md
        assert "样例异常" in md
        assert "样例建议" in md
        assert "-1.50/块" in md
