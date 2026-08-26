"""漂移量化评估服务覆盖率补全测试。

针对既有 test_drift_metrics_service.py 未覆盖的分支：
- DriftMetricsReport.to_dict（129）
- extract_frame_at 真实 ffmpeg 命令组装与失败抛错（146-157）
- _default_vlm_caller：URL 未配置 / 正常解析 / markdown 围栏剥离 /
  reasoning_content 回退 / json_repair 修复 / 非字典 / 异常 fail-open（210-246）
- score_action_continuity VLM 失败 fail-open（337）
- evaluate：单帧抽帧失败跳过（381-382）、fidelity 细节聚合（402）、
  像素差计算失败（443-444）、接缝帧抽取失败（446-447）
- _analyze_trends：drift_detected 布尔异常标注（488）
- _safe_duration 成功 / 异常回退（532-537）
- _sample_times 两点采样（546）
- render_markdown_report：情感斜率行（586）、无异常分支（594）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.drift_metrics_service import (
    ChunkMetrics,
    DriftMetricsReport,
    DriftMetricsService,
    SeamMetrics,
    extract_frame_at,
    render_markdown_report,
)


def _png(path: Path, gray: int) -> Path:
    """生成纯色灰度 PNG（与既有测试同口径）。"""
    from PIL import Image

    Image.new("L", (32, 32), color=gray).save(path, "PNG")
    return path


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------
class TestReportToDict:
    def test_to_dict_structure(self):
        report = DriftMetricsReport(
            chunks=[ChunkMetrics(chunk_index=0, character_fidelity=88.0, drift_detected=True)],
            seams=[SeamMetrics(seam_index=0, pixel_diff=5.0, motion_jump=True)],
            fidelity_slope=-1.5,
            emotion_slope=-2.0,
            fidelity_endpoint_delta=-6.0,
            emotion_endpoint_delta=-8.0,
            anomalies=["异常A"],
            suggestions=["建议B"],
        )
        d = report.to_dict()
        assert d["chunks"][0]["character_fidelity"] == 88.0
        assert d["chunks"][0]["drift_detected"] is True
        assert d["seams"][0]["pixel_diff"] == 5.0
        assert d["seams"][0]["motion_jump"] is True
        assert d["fidelity_slope"] == -1.5
        assert d["emotion_slope"] == -2.0
        assert d["fidelity_endpoint_delta"] == -6.0
        assert d["emotion_endpoint_delta"] == -8.0
        assert d["anomalies"] == ["异常A"]
        assert d["suggestions"] == ["建议B"]


# ---------------------------------------------------------------------------
# extract_frame_at（mock _run_ffmpeg，不触真实 ffmpeg）
# ---------------------------------------------------------------------------
class TestExtractFrameAt:
    async def test_command_assembly_and_success(self, monkeypatch, tmp_path):
        """命令含 ffmpeg -ss 时间点；产物文件存在且非空 → 返回 out_path。"""
        captured: dict[str, list[str]] = {}

        async def fake_run_ffmpeg(cmd: list[str]) -> None:
            captured["cmd"] = cmd
            Path(cmd[-1]).write_bytes(b"fake-png")

        monkeypatch.setattr(
            "app.services.drift_metrics_service._run_ffmpeg", fake_run_ffmpeg
        )

        out = tmp_path / "frame.png"
        result = await extract_frame_at(tmp_path / "chunk.mp4", 3.5, out)

        assert result == out
        cmd = captured["cmd"]
        assert cmd[0] == "ffmpeg"
        assert "3.50" in cmd  # -ss 时间点两位小数
        assert str(tmp_path / "chunk.mp4") in cmd

    async def test_missing_output_raises(self, monkeypatch, tmp_path):
        """ffmpeg「成功」但产物缺失/为空 → RuntimeError 抽帧失败。"""
        monkeypatch.setattr(
            "app.services.drift_metrics_service._run_ffmpeg", AsyncMock()
        )
        with pytest.raises(RuntimeError, match="抽帧失败"):
            await extract_frame_at(tmp_path / "v.mp4", 1.0, tmp_path / "out.png")


# ---------------------------------------------------------------------------
# _default_vlm_caller（mock AsyncOpenAI，不触真实 VLM）
# ---------------------------------------------------------------------------
def _make_vlm_client(content: str, reasoning_content: str = "") -> MagicMock:
    message = MagicMock()
    message.content = content
    message.reasoning_content = reasoning_content
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


class TestDefaultVlmCaller:
    async def test_no_url_returns_none(self, monkeypatch):
        """visual_model_url 未配置 → None（不创建客户端）。"""
        monkeypatch.setattr(settings, "visual_model_url", "")
        result = await DriftMetricsService._default_vlm_caller(
            [{"type": "text", "text": "x"}]
        )
        assert result is None

    async def test_plain_json_parsed(self, monkeypatch):
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm.test/v1")
        client = _make_vlm_client('{"score": 85, "reason": ""}')
        with patch(
            "app.services.drift_metrics_service.AsyncOpenAI", return_value=client
        ):
            result = await DriftMetricsService._default_vlm_caller(
                [{"type": "text", "text": "x"}]
            )
        assert result == {"score": 85, "reason": ""}
        # enable_thinking=False 注入
        extra = client.chat.completions.create.call_args.kwargs["extra_body"]
        assert extra["chat_template_kwargs"]["enable_thinking"] is False

    async def test_markdown_fence_stripped(self, monkeypatch):
        """```json 围栏包裹的 JSON → 剥离围栏后解析。"""
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm.test/v1")
        client = _make_vlm_client('```json\n{"score": 77}\n```')
        with patch(
            "app.services.drift_metrics_service.AsyncOpenAI", return_value=client
        ):
            result = await DriftMetricsService._default_vlm_caller(
                [{"type": "text", "text": "x"}]
            )
        assert result == {"score": 77}

    async def test_reasoning_content_fallback(self, monkeypatch):
        """content 为空 → 回退 reasoning_content 解析。"""
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm.test/v1")
        client = _make_vlm_client("", reasoning_content='{"score": 66}')
        with patch(
            "app.services.drift_metrics_service.AsyncOpenAI", return_value=client
        ):
            result = await DriftMetricsService._default_vlm_caller(
                [{"type": "text", "text": "x"}]
            )
        assert result == {"score": 66}

    async def test_broken_json_repaired(self, monkeypatch):
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm.test/v1")
        client = _make_vlm_client('{"score": 55,')
        with patch(
            "app.services.drift_metrics_service.AsyncOpenAI", return_value=client
        ):
            result = await DriftMetricsService._default_vlm_caller(
                [{"type": "text", "text": "x"}]
            )
        assert result == {"score": 55}

    async def test_non_dict_returns_none(self, monkeypatch):
        """解析结果非字典 → None。"""
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm.test/v1")
        client = _make_vlm_client('"just a string"')
        with patch(
            "app.services.drift_metrics_service.AsyncOpenAI", return_value=client
        ):
            result = await DriftMetricsService._default_vlm_caller(
                [{"type": "text", "text": "x"}]
            )
        assert result is None

    async def test_exception_fail_open(self, monkeypatch):
        """VLM 调用抛异常 → None（fail-open）。"""
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm.test/v1")
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("vlm down"))
        with patch(
            "app.services.drift_metrics_service.AsyncOpenAI", return_value=client
        ):
            result = await DriftMetricsService._default_vlm_caller(
                [{"type": "text", "text": "x"}]
            )
        assert result is None


# ---------------------------------------------------------------------------
# score_action_continuity fail-open
# ---------------------------------------------------------------------------
class TestActionContinuityFailOpen:
    async def test_vlm_none_returns_defaults(self, tmp_path):
        a = _png(tmp_path / "a.png", 100)
        b = _png(tmp_path / "b.png", 100)
        svc = DriftMetricsService(vlm_caller=AsyncMock(return_value=None))
        score, jump, detail = await svc.score_action_continuity(a, b)
        assert (score, jump, detail) == (None, False, "")


# ---------------------------------------------------------------------------
# evaluate 边界分支
# ---------------------------------------------------------------------------
class TestEvaluateEdge:
    async def test_frame_extract_failure_skipped_and_detail_aggregated(
        self, monkeypatch, tmp_path
    ):
        """单帧抽帧失败 → 跳过该帧；有效帧 fidelity 细节聚合进 ChunkMetrics。"""
        async def fake_extract_at(video: Path, t: float, out: Path) -> Path:
            if "frame1" in out.name:
                raise RuntimeError("解码失败")
            return _png(out, 100)

        monkeypatch.setattr(
            "app.services.drift_metrics_service.extract_frame_at", fake_extract_at
        )
        monkeypatch.setattr(
            DriftMetricsService, "_safe_duration", AsyncMock(return_value=14.0)
        )

        async def vlm(content):
            text = content[0]["text"]
            if "角色特征保持度" in text:
                return {"character_present": True, "score": 70, "reason": "发色偏差"}
            return {"observed_emotion": "平静", "score": 80, "reason": ""}

        svc = DriftMetricsService(vlm_caller=vlm)
        chunk = tmp_path / "chunk_00.mp4"
        chunk.write_bytes(b"fake")
        ref = _png(tmp_path / "ref.png", 100)

        report = await svc.evaluate(
            [chunk],
            reference_image_paths=[ref],
            chunk_intents=["平静收尾"],
            work_dir=tmp_path / "work",
        )

        c = report.chunks[0]
        assert c.character_fidelity == 70.0  # 2 有效帧均分
        assert "发色偏差" in c.fidelity_detail
        assert c.emotion_consistency == 80.0
        assert report.seams == []  # 单块无缝

    async def test_pixel_diff_failure_keeps_action_score(
        self, monkeypatch, tmp_path
    ):
        """像素差计算抛错 → pixel_diff=None，VLM 动作连贯性仍正常采集。"""
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
            "app.services.drift_metrics_service.compute_pixel_diff",
            MagicMock(side_effect=RuntimeError("PIL 损坏")),
        )
        monkeypatch.setattr(
            DriftMetricsService, "_safe_duration", AsyncMock(return_value=14.0)
        )

        async def vlm(content):
            text = content[0]["text"]
            if "情绪基调" in text:
                return {"observed_emotion": "平静", "score": 80, "reason": ""}
            return {"score": 76, "motion_jump": False, "reason": "连贯"}

        svc = DriftMetricsService(vlm_caller=vlm)
        chunks = [tmp_path / f"chunk_{i:02d}.mp4" for i in range(2)]
        for c in chunks:
            c.write_bytes(b"fake")

        report = await svc.evaluate(chunks, work_dir=tmp_path / "work")

        seam = report.seams[0]
        assert seam.pixel_diff is None
        assert seam.action_continuity == 76.0
        assert seam.detail == "连贯"

    async def test_seam_extract_failure_records_none(self, monkeypatch, tmp_path):
        """接缝帧抽取失败 → 该缝指标全 None，不阻断整体评估。"""
        async def fake_extract_at(video: Path, t: float, out: Path) -> Path:
            return _png(out, 100)

        async def fake_extract_last_raise(video: Path, out: Path) -> Path:
            raise RuntimeError("ffmpeg 崩溃")

        monkeypatch.setattr(
            "app.services.drift_metrics_service.extract_frame_at", fake_extract_at
        )
        monkeypatch.setattr(
            "app.services.long_video_service.extract_last_frame",
            fake_extract_last_raise,
        )
        monkeypatch.setattr(
            DriftMetricsService, "_safe_duration", AsyncMock(return_value=14.0)
        )

        vlm = AsyncMock(return_value={"observed_emotion": "平静", "score": 80, "reason": ""})
        svc = DriftMetricsService(vlm_caller=vlm)
        chunks = [tmp_path / f"chunk_{i:02d}.mp4" for i in range(2)]
        for c in chunks:
            c.write_bytes(b"fake")

        report = await svc.evaluate(chunks, work_dir=tmp_path / "work")

        assert len(report.seams) == 1
        seam = report.seams[0]
        assert seam.pixel_diff is None
        assert seam.action_continuity is None
        assert seam.motion_jump is False
        assert seam.detail == ""
        # 块级指标不受接缝失败影响
        assert report.chunks[0].emotion_consistency == 80.0


# ---------------------------------------------------------------------------
# _analyze_trends 布尔漂移标注
# ---------------------------------------------------------------------------
class TestAnalyzeTrendsEdge:
    def test_drift_detected_flag_annotated(self):
        """drift_detected=True（即使分数高于阈值）→ 异常标注「漂移判定为真」。"""
        report = DriftMetricsReport(
            chunks=[
                ChunkMetrics(chunk_index=0, character_fidelity=85.0, drift_detected=True)
            ],
            seams=[],
        )
        DriftMetricsService(vlm_caller=AsyncMock())._analyze_trends(report)
        assert any("漂移判定为真" in a for a in report.anomalies)


# ---------------------------------------------------------------------------
# _safe_duration / _sample_times
# ---------------------------------------------------------------------------
class TestInternalTools:
    async def test_safe_duration_success(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.long_video_service.probe_video_duration",
            AsyncMock(return_value=12.5),
        )
        assert await DriftMetricsService._safe_duration(Path("x.mp4")) == 12.5

    async def test_safe_duration_exception_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.long_video_service.probe_video_duration",
            AsyncMock(side_effect=RuntimeError("ffprobe 缺失")),
        )
        assert await DriftMetricsService._safe_duration(Path("x.mp4")) == 0.0

    def test_sample_times_two_points(self):
        """n=2 → 首/尾两点（避开 0.1s 编码边界）。"""
        times = DriftMetricsService._sample_times(14.0, 2)
        assert times[0] == pytest.approx(0.1)
        assert times[1] == pytest.approx(13.9)


# ---------------------------------------------------------------------------
# render_markdown_report 边界
# ---------------------------------------------------------------------------
class TestRenderEdge:
    def test_emotion_slope_and_no_anomaly_branches(self):
        """情感斜率行渲染（无端点增量）+ 无异常分支文案。"""
        report = DriftMetricsReport(
            chunks=[ChunkMetrics(chunk_index=0, emotion_consistency=70.0)],
            emotion_slope=-2.5,
            suggestions=["保持现状"],
        )
        md = render_markdown_report(report, {})
        assert "情感一致性斜率" in md
        assert "-2.50/块" in md
        assert "无异常（全部指标在健康阈值内）" in md
        # 角色斜率缺省 → 不渲染该行
        assert "角色特征保持度斜率" not in md
