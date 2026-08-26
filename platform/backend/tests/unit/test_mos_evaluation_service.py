"""M22.2 MosEvaluationService 单元测试 —— mock VLM + mock 抽帧，纯逻辑验证。

覆盖：
- _score 容错：null/bool/越界/非数值/NaN → None；1-5 边界取值
- score_frame：四维度解析、reason 截断、VLM 失败 fail-open（全 None）
- evaluate：维度均值与总 MOS 聚合、部分维度缺失时按可用值聚合、
  全失败时 MOS=None、frames_scored 统计、视频不存在报错
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.mos_evaluation_service import (
    MOS_DIMENSIONS,
    MosEvaluationService,
)


def _vlm_ok(score: int = 4):
    """构造正常 VLM 返回（四维度同分）。"""
    async def caller(content):
        return {dim: score for dim in MOS_DIMENSIONS} | {"reason": ""}

    return caller


def _vlm_fail():
    async def caller(content):
        return None

    return caller


# ---------------------------------------------------------------------------
# _score 容错
# ---------------------------------------------------------------------------
class TestScoreParsing:
    @pytest.mark.parametrize("val", [None, True, False, 0, 6, -1, "abc", float("nan"), [1]])
    def test_invalid_returns_none(self, val):
        assert MosEvaluationService._score({"visual_quality": val}, "visual_quality") is None

    @pytest.mark.parametrize("val,expected", [(1, 1.0), (5, 5.0), (3, 3.0), ("4", 4.0), (4.0, 4.0)])
    def test_valid_values(self, val, expected):
        assert MosEvaluationService._score({"visual_quality": val}, "visual_quality") == expected

    def test_missing_key_and_empty_data(self):
        assert MosEvaluationService._score({}, "visual_quality") is None
        assert MosEvaluationService._score(None, "visual_quality") is None


# ---------------------------------------------------------------------------
# score_frame
# ---------------------------------------------------------------------------
class TestScoreFrame:
    async def test_parses_four_dimensions(self, tmp_path):
        img = tmp_path / "f.png"
        img.write_bytes(b"\x89PNG fake")
        svc = MosEvaluationService(vlm_caller=_vlm_ok(5))
        res = await svc.score_frame(img, "test prompt", 1, 6)
        assert res.scores == {dim: 5.0 for dim in MOS_DIMENSIONS}
        assert res.frame_index == 0

    async def test_vlm_failure_returns_none_scores(self, tmp_path):
        img = tmp_path / "f.png"
        img.write_bytes(b"\x89PNG fake")
        svc = MosEvaluationService(vlm_caller=_vlm_fail())
        res = await svc.score_frame(img, "p", 2, 6)
        assert all(v is None for v in res.scores.values())

    async def test_reason_truncated_to_200(self, tmp_path):
        img = tmp_path / "f.png"
        img.write_bytes(b"\x89PNG fake")

        async def caller(content):
            return {dim: 3 for dim in MOS_DIMENSIONS} | {"reason": "x" * 500}

        svc = MosEvaluationService(vlm_caller=caller)
        res = await svc.score_frame(img, "p", 1, 1)
        assert len(res.reason) == 200


# ---------------------------------------------------------------------------
# evaluate 聚合
# ---------------------------------------------------------------------------
def _mock_evaluate_deps(frame_count: int = 3):
    """mock 抽帧与时长探测，返回假帧路径序列。"""
    frames = [Path(f"/tmp/mos_fake_{i}.png") for i in range(frame_count)]
    for f in frames:
        f.write_bytes(b"\x89PNG fake") if not f.exists() else None

    async def fake_duration(_p):
        return 10.0

    async def fake_extract(_vp, _t, out):
        Path(out).write_bytes(b"\x89PNG fake")
        return out

    return frames, fake_duration, fake_extract


class TestEvaluate:
    async def test_mos_aggregation(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _, fake_duration, fake_extract = _mock_evaluate_deps()

        async def caller(content):
            return {
                "visual_quality": 4, "motion_naturalness": 5,
                "temporal_consistency": 3, "text_alignment": 4, "reason": "",
            }

        svc = MosEvaluationService(vlm_caller=caller)
        with (
            patch("app.services.mos_evaluation_service.probe_video_duration", fake_duration),
            patch("app.services.mos_evaluation_service.extract_frame_at", fake_extract),
        ):
            report = await svc.evaluate(video, prompt="p", num_frames=3, work_dir=tmp_path)

        assert report.frames_scored == 3
        assert report.dimension_means == {
            "visual_quality": 4.0, "motion_naturalness": 5.0,
            "temporal_consistency": 3.0, "text_alignment": 4.0,
        }
        assert report.mos == 4.0  # (4+5+3+4)/4

    async def test_partial_dimension_missing_aggregates_available(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _, fake_duration, fake_extract = _mock_evaluate_deps()

        async def caller(content):
            return {"visual_quality": 5, "motion_naturalness": None,
                    "temporal_consistency": 3, "text_alignment": 4}

        svc = MosEvaluationService(vlm_caller=caller)
        with (
            patch("app.services.mos_evaluation_service.probe_video_duration", fake_duration),
            patch("app.services.mos_evaluation_service.extract_frame_at", fake_extract),
        ):
            report = await svc.evaluate(video, num_frames=2, work_dir=tmp_path)

        assert report.dimension_means["motion_naturalness"] is None
        assert report.dimension_means["visual_quality"] == 5.0
        # MOS 只聚合可用值：(5+3+4)*2帧 / 6 = 4.0
        assert report.mos == 4.0

    async def test_all_vlm_fail_mos_none(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _, fake_duration, fake_extract = _mock_evaluate_deps()

        svc = MosEvaluationService(vlm_caller=_vlm_fail())
        with (
            patch("app.services.mos_evaluation_service.probe_video_duration", fake_duration),
            patch("app.services.mos_evaluation_service.extract_frame_at", fake_extract),
        ):
            report = await svc.evaluate(video, num_frames=3, work_dir=tmp_path)

        assert report.mos is None
        assert report.frames_scored == 0
        assert all(v is None for v in report.dimension_means.values())

    async def test_missing_video_raises(self, tmp_path):
        svc = MosEvaluationService(vlm_caller=_vlm_ok())
        with pytest.raises(ValueError, match="视频不存在"):
            await svc.evaluate(tmp_path / "nope.mp4")

    async def test_extract_failure_skips_frame(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")

        async def fake_duration(_p):
            return 10.0

        call_count = 0

        async def flaky_extract(_vp, _t, out):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("ffmpeg boom")
            Path(out).write_bytes(b"\x89PNG fake")
            return out

        svc = MosEvaluationService(vlm_caller=_vlm_ok(4))
        with (
            patch("app.services.mos_evaluation_service.probe_video_duration", fake_duration),
            patch("app.services.mos_evaluation_service.extract_frame_at", flaky_extract),
        ):
            report = await svc.evaluate(video, num_frames=3, work_dir=tmp_path)

        # 第 1 帧抽帧失败被跳过，其余 2 帧正常评分
        assert len(report.frames) == 2
        assert report.mos == 4.0

    async def test_prev_frame_passed_for_temporal(self, tmp_path):
        """第 2 帧起应携带前一帧作时序对比（content 含 2 张图）。"""
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _, fake_duration, fake_extract = _mock_evaluate_deps()
        seen_image_counts: list[int] = []

        async def caller(content):
            seen_image_counts.append(sum(1 for c in content if c.get("type") == "image_url"))
            return {dim: 4 for dim in MOS_DIMENSIONS}

        svc = MosEvaluationService(vlm_caller=caller)
        with (
            patch("app.services.mos_evaluation_service.probe_video_duration", fake_duration),
            patch("app.services.mos_evaluation_service.extract_frame_at", fake_extract),
        ):
            await svc.evaluate(video, num_frames=3, work_dir=tmp_path)

        assert seen_image_counts == [1, 2, 2]  # 首帧 1 张，后续帧带前一帧对比
