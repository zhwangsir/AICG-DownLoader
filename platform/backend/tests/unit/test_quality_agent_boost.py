"""质检 Agent 覆盖增强测试（quality_agent.py 边界/异常/fail-open 分支）。

覆盖目标：
- QualityAgent.execute 超时降级 / 通用异常分支
- _parse_issues 容错解析（字符串项/非 dict 项/scene_id 字符串/构造异常）
- _structure_issues / _high_risk_scene_issues 空 scenes 与各风险打标分支
- _fallback_check 规则质检（错别字/敏感词/评分）
- 字幕修正函数族（_extract_subtitle_corrections / _format_srt_timestamp /
  _build_srt_from_segments / apply_subtitle_fixes 含持久化 OSError）
- VisualQualityAgent 全流程（客户端懒加载/execute 各分支/_download_video/
  _drift_check/_drift_check_single_frame/_download_reference_image/
  _extract_frames/_probe_duration）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agents.quality_agent as qa
from app.agents.quality_agent import (
    QualityAgent,
    VisualQualityAgent,
    _build_srt_from_segments,
    _extract_subtitle_corrections,
    _format_srt_timestamp,
    apply_subtitle_fixes,
)
from app.models.schemas import (
    Character,
    QualityCheckItem,
    QualityCheckRequest,
    QualityVisualRequest,
    Scene,
    SubtitleFixRequest,
    SubtitleResult,
    SubtitleSegment,
)


@pytest.fixture
def quality_agent():
    agent = QualityAgent()
    agent.llm_client = MagicMock()
    return agent


@pytest.fixture
def visual_agent():
    agent = VisualQualityAgent()
    agent.llm_client = MagicMock()
    # VisualQualityAgent 使用独立的 _vlm_client；预设 MagicMock 避免真实 AsyncOpenAI
    agent._vlm_client = MagicMock()
    return agent


def _vlm_response(payload: str | None, reasoning: str | None = None) -> MagicMock:
    """构造 VLM chat.completions 响应 mock。"""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = payload
    resp.choices[0].message.reasoning_content = reasoning
    return resp


def _mock_frame(data: bytes = b"frame") -> MagicMock:
    frame = MagicMock()
    frame.read_bytes.return_value = data
    return frame


class _FakeStreamCM:
    """httpx.AsyncClient.stream 的异步上下文管理器桩。"""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def __aenter__(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()

        async def _gen():
            for c in self._chunks:
                yield c

        resp.aiter_bytes = _gen
        return resp

    async def __aexit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# QualityAgent.execute 异常分支
# ---------------------------------------------------------------------------


class TestQualityExecuteEdge:
    async def test_timeout_falls_back_to_rule_check(self, quality_agent, mock_call_llm):
        """LLM 调用超时 → 降级为规则质检（fail-open），summary 带降级前缀。"""
        mock_call_llm.side_effect = asyncio.TimeoutError()

        request = QualityCheckRequest(project_id="p1", title="测试")
        response = await quality_agent.execute(request)

        assert response.success is True
        assert "LLM 质检超时，已降级为规则质检" in response.data["summary"]
        # 空输入规则质检无问题 → 85 分
        assert response.data["score"] == 85

    async def test_generic_exception_returns_failure(self, quality_agent, mock_call_llm):
        """LLM 调用抛出非超时异常 → success=False 且错误含「质检失败」。"""
        mock_call_llm.side_effect = RuntimeError("conn reset")

        request = QualityCheckRequest(project_id="p1", title="测试")
        response = await quality_agent.execute(request)

        assert response.success is False
        assert "质检失败" in response.error
        assert "conn reset" in response.error

    async def test_json_repair_path_succeeds(self, quality_agent, mock_call_llm):
        """LLM 输出非法 JSON（可修复）→ json_repair 兜底解析成功。"""
        mock_call_llm.return_value = '{score: 66, summary: "一般", issues: []}'

        request = QualityCheckRequest(project_id="p1", title="测试")
        response = await quality_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 66
        assert response.data["summary"] == "一般"


# ---------------------------------------------------------------------------
# _parse_issues 容错解析
# ---------------------------------------------------------------------------


class TestParseIssues:
    def test_string_item_becomes_warning(self, quality_agent):
        """LLM 返回字符串数组 → 每项包装为 logic/warning issue。"""
        items = quality_agent._parse_issues(["剧情跳跃", "台词重复"])
        assert len(items) == 2
        assert all(i.category == "logic" and i.severity == "warning" for i in items)
        assert items[0].message == "剧情跳跃"
        assert items[0].suggestion == ""

    def test_non_dict_item_skipped(self, quality_agent):
        """非 str/非 dict 项（如 int）直接跳过。"""
        items = quality_agent._parse_issues([42, 3.14, {"message": "有效", "category": "logic", "severity": "info"}])
        assert len(items) == 1
        assert items[0].message == "有效"

    def test_scene_id_string_with_digits_extracted(self, quality_agent):
        """scene_id 为含数字的字符串 → 正则提取为 int。"""
        items = quality_agent._parse_issues([
            {"category": "logic", "severity": "info", "scene_id": "场景3", "message": "m"}
        ])
        assert items[0].scene_id == 3

    def test_scene_id_string_without_digits_becomes_none(self, quality_agent):
        """scene_id 字符串无数字 → None。"""
        items = quality_agent._parse_issues([
            {"category": "logic", "severity": "info", "scene_id": "开场", "message": "m"}
        ])
        assert items[0].scene_id is None

    def test_invalid_item_construction_skipped(self, quality_agent):
        """字段类型无法通过 pydantic 校验 → 跳过该项不抛出。"""
        items = quality_agent._parse_issues([
            {"category": "logic", "severity": "info", "scene_id": [1, 2], "message": "坏项"},
            {"category": "logic", "severity": "info", "message": "好项"},
        ])
        assert len(items) == 1
        assert items[0].message == "好项"


# ---------------------------------------------------------------------------
# _structure_issues / _high_risk_scene_issues
# ---------------------------------------------------------------------------


class TestStructureIssues:
    def test_empty_scenes_returns_empty(self, quality_agent):
        request = QualityCheckRequest(project_id="p1", scenes=[])
        assert quality_agent._structure_issues(request) == []

    def test_non_empty_scenes_wraps_validator_issues(self, quality_agent):
        """极简场景触发确定性结构校验 → 包装为 structure/warning issue。"""
        request = QualityCheckRequest(
            project_id="p1",
            scenes=[Scene(scene_id=1, description="开场", dialogue="你好")],
        )
        issues = quality_agent._structure_issues(request)
        assert len(issues) >= 1
        assert all(i.category == "structure" and i.severity == "warning" for i in issues)
        assert all(i.message.startswith("剧本结构问题") for i in issues)


class TestHighRiskSceneIssues:
    def test_empty_scenes_returns_empty(self, quality_agent):
        request = QualityCheckRequest(project_id="p1", scenes=[])
        assert quality_agent._high_risk_scene_issues(request) == []

    def test_crowd_keyword_labeled(self, quality_agent):
        """人群关键词 → 多人同框高风险打标；同时覆盖角色出场统计。"""
        request = QualityCheckRequest(
            project_id="p1",
            characters=[Character(character_id="c1", name="Alice", role="主角")],
            scenes=[Scene(scene_id=1, description="Alice 穿行于人群之中，众人围观")],
        )
        issues = quality_agent._high_risk_scene_issues(request)
        crowd = [i for i in issues if "多人同框" in i.message]
        assert len(crowd) == 1
        assert crowd[0].category == "visual_risk"
        assert crowd[0].severity == "warning"
        assert crowd[0].scene_id == 1
        assert "人工复核" in crowd[0].suggestion

    def test_extreme_view_labeled(self, quality_agent):
        """大特写+手持 → 极端视角高风险打标。"""
        request = QualityCheckRequest(
            project_id="p1",
            scenes=[Scene(
                scene_id=2,
                shot_type="大特写",
                camera_movement="handheld",
                description="主角面部特写",
            )],
        )
        issues = quality_agent._high_risk_scene_issues(request)
        extreme = [i for i in issues if "极端视角" in i.message]
        assert len(extreme) == 1
        assert extreme[0].scene_id == 2

    def test_extreme_keyword_in_description_labeled(self, quality_agent):
        """描述含仰拍/俯拍等关键词 → 极端视角打标（无大特写+手持也命中）。"""
        request = QualityCheckRequest(
            project_id="p1",
            scenes=[Scene(scene_id=3, description="仰拍主角站在楼顶")],
        )
        issues = quality_agent._high_risk_scene_issues(request)
        assert any("极端视角" in i.message for i in issues)

    def test_minor_character_cross_episode_recall(self, quality_agent):
        """出场 ≤3 镜但跨 ≥2 集的小配角 → 跨集召回 info 打标。"""
        request = QualityCheckRequest(
            project_id="p1",
            characters=[Character(character_id="c1", name="Bob", role="配角")],
            scenes=[
                Scene(scene_id=1, episode=1, description="Bob 走进店里"),
                Scene(scene_id=2, episode=2, description="Bob 再次出现"),
            ],
        )
        issues = quality_agent._high_risk_scene_issues(request)
        recall = [i for i in issues if "小配角跨集召回" in i.message]
        assert len(recall) == 1
        assert recall[0].severity == "info"
        assert "Bob" in recall[0].message
        assert "服装细节卡" in recall[0].suggestion

    def test_many_characters_same_scene_labeled(self, quality_agent):
        """同镜提及 >5 名角色 → 多人同框打标。"""
        chars = [Character(character_id=f"c{i}", name=f"角色{i}") for i in range(6)]
        desc = "、".join(c.name for c in chars) + " 齐聚一堂"
        request = QualityCheckRequest(
            project_id="p1",
            characters=chars,
            scenes=[Scene(scene_id=1, description=desc)],
        )
        issues = quality_agent._high_risk_scene_issues(request)
        assert any("多人同框" in i.message for i in issues)


# ---------------------------------------------------------------------------
# _fallback_check 规则质检
# ---------------------------------------------------------------------------


class TestFallbackCheck:
    def test_subtitle_typo_detected(self, quality_agent):
        """字幕含「他她」等易错模式 → subtitle/warning issue。"""
        request = QualityCheckRequest(
            project_id="p1",
            title="t",
            subtitles=[SubtitleResult(
                scene_id=1,
                segments=[SubtitleSegment(start=0.0, end=1.0, text="他她不分地使用")],
            )],
        )
        result = quality_agent._fallback_check(request)
        typo = [i for i in result.issues if i.category == "subtitle"]
        assert len(typo) == 1
        assert typo[0].severity == "warning"
        assert typo[0].scene_id == 1
        assert "错别字" in typo[0].message
        # 有问题 → 70 分 + 计数 summary
        assert result.score == 70
        assert f"发现 {len(result.issues)} 个问题" == result.summary

    def test_sensitive_word_detected(self, quality_agent):
        """场景描述/台词含敏感词 → sensitive/warning issue。"""
        request = QualityCheckRequest(
            project_id="p1",
            title="t",
            scenes=[Scene(scene_id=2, description="涉及暴力冲突的画面", dialogue="")],
        )
        result = quality_agent._fallback_check(request)
        sensitive = [i for i in result.issues if i.category == "sensitive"]
        assert len(sensitive) == 1
        assert sensitive[0].scene_id == 2
        assert "暴力" in sensitive[0].message

    def test_clean_request_scores_85(self, quality_agent):
        """无字幕无场景 → 无任何问题 → 85 分 + 默认 summary。"""
        request = QualityCheckRequest(project_id="p9", title="干净剧本")
        result = quality_agent._fallback_check(request)
        assert result.score == 85
        assert result.summary == "基于规则的降级质检完成"
        assert result.issues == []
        assert result.project_id == "p9"


# ---------------------------------------------------------------------------
# 字幕修正函数族
# ---------------------------------------------------------------------------


class TestExtractCorrectionsBoost:
    def test_quote_only_match_stripped_to_empty_skipped(self):
        """箭头两侧仅引号字符 → strip 后为空 → 跳过（防噪声）。"""
        issues = [
            QualityCheckItem(
                category="subtitle",
                severity="warning",
                message="「」→「」",
                suggestion="",
            )
        ]
        assert _extract_subtitle_corrections(issues) == []

    def test_identical_pair_skipped(self):
        """wrong == right 的修正对无意义 → 跳过。"""
        issues = [
            QualityCheckItem(
                category="subtitle",
                severity="warning",
                message="林深→林深",
                suggestion="",
            )
        ]
        assert _extract_subtitle_corrections(issues) == []

    def test_quote_and_arrow_patterns_extracted(self):
        """'X'应为'Y' 与 X→Y 两种模式均可提取。"""
        issues = [
            QualityCheckItem(
                category="subtitle",
                severity="warning",
                message="'林声'应为'林深'，另外 这辈->这杯",
                suggestion="",
            )
        ]
        pairs = dict(_extract_subtitle_corrections(issues))
        assert pairs["林声"] == "林深"
        assert pairs["这辈"] == "这杯"

    def test_non_subtitle_category_ignored(self):
        """非 subtitle 类别的 issue 直接跳过，不参与字幕修正对提取。"""
        issues = [
            QualityCheckItem(
                category="consistency",
                severity="warning",
                message="角色名'林深'应为'林申'",
                suggestion="",
            )
        ]
        assert _extract_subtitle_corrections(issues) == []

    def test_suggestion_text_also_scanned_and_deduplicated(self):
        """suggestion 同样参与提取，且与 message 中的修正对去重。"""
        issues = [
            QualityCheckItem(
                category="subtitle",
                severity="warning",
                message="'林声'应为'林深'",
                suggestion="请修正：'林声'应为'林深'，'定上'应为'盯上'",
            )
        ]
        corrections = _extract_subtitle_corrections(issues)
        assert corrections.count(("林声", "林深")) == 1
        assert ("定上", "盯上") in corrections


class TestFormatSrtTimestamp:
    def test_negative_seconds_clamped_to_zero(self):
        assert _format_srt_timestamp(-5.0) == "00:00:00,000"

    def test_hours_minutes_millis(self):
        assert _format_srt_timestamp(3661.5) == "01:01:01,500"


class TestBuildSrtBoost:
    def test_numbering_and_timestamps(self):
        segments = [
            SubtitleSegment(start=0.0, end=1.5, text="你好"),
            SubtitleSegment(start=1.5, end=3.0, text="世界"),
        ]
        srt = _build_srt_from_segments(segments)
        assert "1\n00:00:00,000 --> 00:00:01,500\n你好" in srt
        assert "2\n00:00:01,500 --> 00:00:03,000\n世界" in srt

    def test_empty_text_segments_skipped_and_renumbered(self):
        segments = [
            SubtitleSegment(start=0.0, end=1.0, text="   "),
            SubtitleSegment(start=1.0, end=2.0, text="有效"),
        ]
        srt = _build_srt_from_segments(segments)
        assert srt.startswith("1\n00:00:01,000 --> 00:00:02,000\n有效")


class TestApplySubtitleFixesBoost:
    def _request(self, persist: bool) -> SubtitleFixRequest:
        return SubtitleFixRequest(
            subtitles=[SubtitleResult(
                scene_id=1,
                segments=[
                    SubtitleSegment(start=0.0, end=1.0, text="林声来了"),
                    SubtitleSegment(start=1.0, end=2.0, text="无关文本"),
                ],
            )],
            issues=[QualityCheckItem(
                category="subtitle",
                severity="critical",
                message="'林声'应为'林深'",
                suggestion="",
            )],
            persist=persist,
        )

    def test_fix_flow_replaces_and_counts(self):
        """修正对替换命中段，未命中段不变；fixed_count/details 正确。"""
        result = apply_subtitle_fixes(self._request(persist=False))
        assert result.fixed_count == 1
        assert result.corrections == [{"wrong": "林声", "right": "林深"}]
        fixed = result.fixed_subtitles[0]
        assert fixed.segments[0].text == "林深来了"
        assert fixed.segments[1].text == "无关文本"
        assert "林深" in fixed.srt_content
        assert len(result.details) == 1
        assert result.details[0].original_text == "林声来了"
        assert result.details[0].applied == [{"wrong": "林声", "right": "林深"}]
        assert result.persisted_files == []

    def test_persist_writes_files(self, tmp_path, monkeypatch):
        """persist=True → SRT 回写到 output/subtitle/（重定向到 tmp 目录）。"""
        fake_file = tmp_path / "app" / "agents" / "quality_agent.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()
        monkeypatch.setattr(qa, "__file__", str(fake_file))

        result = apply_subtitle_fixes(self._request(persist=True))
        assert len(result.persisted_files) == 1
        written = Path(result.persisted_files[0])
        assert written.exists()
        assert written.parent == tmp_path / "output" / "subtitle"
        assert "林深" in written.read_text(encoding="utf-8")

    def test_persist_oserror_logged_and_skipped(self, tmp_path, monkeypatch):
        """回写 OSError → 记日志跳过，persisted_files 为空且不抛出。"""
        fake_file = tmp_path / "app" / "agents" / "quality_agent.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()
        monkeypatch.setattr(qa, "__file__", str(fake_file))

        def _raise(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _raise)
        result = apply_subtitle_fixes(self._request(persist=True))
        assert result.persisted_files == []
        # 修正逻辑仍正常完成
        assert result.fixed_count == 1


# ---------------------------------------------------------------------------
# VisualQualityAgent._get_vlm_client
# ---------------------------------------------------------------------------


class TestGetVlmClient:
    def test_lazy_init_and_reuse(self):
        """首次调用创建 AsyncOpenAI 客户端，之后复用同一实例。"""
        from openai import AsyncOpenAI

        agent = VisualQualityAgent()
        assert agent._vlm_client is None
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm/v1"):
            client = agent._get_vlm_client()
            assert isinstance(client, AsyncOpenAI)
            assert agent._get_vlm_client() is client


# ---------------------------------------------------------------------------
# VisualQualityAgent.execute 分支
# ---------------------------------------------------------------------------


class TestVisualExecuteBoost:
    async def test_fallback_when_model_not_configured(self, visual_agent):
        """visual_model_url 为空 → 降级提示结果，不调用 VLM。"""
        with patch("app.agents.quality_agent.settings.visual_model_url", ""):
            request = QualityVisualRequest(
                project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4"
            )
            response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 0
        assert "未部署" in response.data["summary"]
        assert response.data["issues"][0]["category"] == "system"

    def _patch_io(self, visual_agent, frames):
        dl = patch.object(visual_agent, "_download_video", new_callable=AsyncMock)
        ex = patch.object(
            visual_agent, "_extract_frames", new_callable=AsyncMock, return_value=frames
        )
        return dl, ex

    async def test_success_parses_issues_and_filters_non_dict(self, visual_agent):
        """主流程成功；issues 中非 dict 项被过滤。"""
        payload = json.dumps({
            "score": 88,
            "summary": "画面连贯",
            "issues": [
                {"category": "anomaly", "severity": "warning", "timestamp": 1.5,
                 "message": "画面模糊", "suggestion": "重抽"},
                "非 dict 噪声",
            ],
        }, ensure_ascii=False)
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            return_value=_vlm_response(payload)
        )
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4", max_frames=1
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 88
        assert len(response.data["issues"]) == 1
        assert response.data["issues"][0]["category"] == "anomaly"
        assert response.data["issues"][0]["timestamp"] == 1.5
        assert response.data["drift_detected"] is False

    async def test_empty_content_falls_back_to_reasoning(self, visual_agent):
        """content 为空 → 回退 reasoning_content 作为解析源。"""
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            return_value=_vlm_response("", '{"score": 77, "summary": "rk", "issues": []}')
        )
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4"
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 77
        assert response.data["summary"] == "rk"

    async def test_markdown_code_block_stripped(self, visual_agent):
        """VLM 输出包裹 ```json 代码块 → 剥离后解析成功。"""
        payload = '```json\n{"score": 81, "summary": "ok", "issues": []}\n```'
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            return_value=_vlm_response(payload)
        )
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4"
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 81

    async def test_json_repair_path(self, visual_agent):
        """非法但可修复的 JSON → json_repair 兜底。"""
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            return_value=_vlm_response('{score: 69, summary: "修复", issues: []}')
        )
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4"
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 69

    async def test_non_dict_result_returns_parse_failure(self, visual_agent):
        """VLM 输出解析后非 JSON 对象 → success=False 且报 JSON 解析失败。"""
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            return_value=_vlm_response('"just a string"')
        )
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4"
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                response = await visual_agent.execute(request)

        assert response.success is False
        assert "JSON 解析失败" in response.error

    async def test_generic_exception_returns_failure(self, visual_agent):
        """视频下载抛异常 → success=False 且报「视觉质检失败」。"""
        dl = patch.object(
            visual_agent, "_download_video",
            new_callable=AsyncMock, side_effect=RuntimeError("网络中断"),
        )
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4"
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl:
                response = await visual_agent.execute(request)

        assert response.success is False
        assert "视觉质检失败" in response.error
        assert "网络中断" in response.error

    async def test_drift_detected_appends_critical_and_revises_summary(self, visual_agent):
        """有参考图且检出漂移 → 追加 critical issue + 修订 summary。"""
        main_resp = _vlm_response('{"score": 85, "summary": "画质OK", "issues": []}')
        drift_resp = _vlm_response('{"drift_detected": true, "details": "发色不同"}')
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = _mock_frame(b"ref")
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                with patch.object(
                    visual_agent, "_download_reference_image",
                    new_callable=AsyncMock, return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["drift_detected"] is True
        critical = [i for i in response.data["issues"] if i["severity"] == "critical"]
        assert len(critical) == 1
        assert "发色不同" in critical[0]["message"]
        assert "检测到角色漂移（发色不同）" in response.data["summary"]

    async def test_drift_detected_without_detail_uses_fallback_text(self, visual_agent):
        """漂移无细节 → message/summary 使用兜底文案。"""
        main_resp = _vlm_response('{"score": 85, "summary": "ok", "issues": []}')
        drift_resp = _vlm_response('{"drift_detected": true, "details": ""}')
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = _mock_frame(b"ref")
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                with patch.object(
                    visual_agent, "_download_reference_image",
                    new_callable=AsyncMock, return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.data["drift_detected"] is True
        critical = [i for i in response.data["issues"] if i["severity"] == "critical"]
        assert "视频角色与参考图不一致" in critical[0]["message"]
        assert response.data["summary"] == "检测到角色漂移。"

    async def test_drift_not_detected_keeps_main_summary(self, visual_agent):
        """漂移判定 false → 不追加 critical，summary 不变。"""
        main_resp = _vlm_response('{"score": 85, "summary": "ok", "issues": []}')
        drift_resp = _vlm_response('{"drift_detected": false, "details": ""}')
        visual_agent._vlm_client.chat.completions.create = AsyncMock(
            side_effect=[main_resp, drift_resp]
        )
        ref_path = _mock_frame(b"ref")
        dl, ex = self._patch_io(visual_agent, [(1.0, _mock_frame())])
        request = QualityVisualRequest(
            project_id="p1", title="v", scene_id=1, video_url="http://x/v.mp4",
            reference_image_urls=["http://x/ref.png"],
        )
        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with dl, ex:
                with patch.object(
                    visual_agent, "_download_reference_image",
                    new_callable=AsyncMock, return_value=ref_path,
                ):
                    response = await visual_agent.execute(request)

        assert response.data["drift_detected"] is False
        assert response.data["summary"] == "ok"
        assert all(i["severity"] != "critical" for i in response.data["issues"])


# ---------------------------------------------------------------------------
# _download_video
# ---------------------------------------------------------------------------


class TestDownloadVideo:
    async def test_local_static_reuse(self, visual_agent, tmp_path, monkeypatch):
        """localhost /static/video/xxx 且本地存在 → 直接复用 output/video 文件。"""
        fake_file = tmp_path / "app" / "agents" / "quality_agent.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()
        monkeypatch.setattr(qa, "__file__", str(fake_file))

        video_dir = tmp_path / "output" / "video"
        video_dir.mkdir(parents=True)
        target = video_dir / "clip.mp4"
        target.write_bytes(b"video")

        result = await visual_agent._download_video(
            "http://localhost:8100/static/video/clip.mp4"
        )
        assert result == target

    async def test_remote_streaming_download(self, visual_agent):
        """远程 URL → 流式下载写入临时文件。"""
        visual_agent.http = MagicMock()
        visual_agent.http.stream = MagicMock(
            return_value=_FakeStreamCM([b"chunk1", b"chunk2"])
        )
        dest = await visual_agent._download_video("http://remote.example.com/a/b.mp4")
        try:
            assert dest.suffix == ".mp4"
            assert dest.read_bytes() == b"chunk1chunk2"
        finally:
            dest.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# _drift_check / _drift_check_single_frame
# ---------------------------------------------------------------------------


class TestDriftCheck:
    async def test_aggregates_and_deduplicates_details(self, visual_agent):
        """多帧漂移细节聚合去重，任帧漂移即整体漂移。"""
        frames = [(float(i), MagicMock()) for i in range(4)]
        with patch.object(
            visual_agent, "_drift_check_single_frame", new_callable=AsyncMock
        ) as mock_single:
            mock_single.side_effect = [
                (True, "发色不同"),
                (True, "发色不同"),
                (False, ""),
                (True, "服装变化"),
            ]
            detected, detail = await visual_agent._drift_check([MagicMock()], frames)

        assert detected is True
        assert detail == "发色不同；服装变化"

    async def test_no_drift_returns_false(self, visual_agent):
        frames = [(0.0, MagicMock())]
        with patch.object(
            visual_agent, "_drift_check_single_frame",
            new_callable=AsyncMock, return_value=(False, ""),
        ):
            detected, detail = await visual_agent._drift_check([MagicMock()], frames)
        assert detected is False
        assert detail == ""

    async def test_exception_falls_back_to_no_drift(self, visual_agent):
        """漂移检测整体异常 → 兜底 (False, "")，不阻断主检查。"""
        frames = [(0.0, MagicMock())]
        with patch.object(
            visual_agent, "_drift_check_single_frame",
            new_callable=AsyncMock, side_effect=RuntimeError("vlm boom"),
        ):
            detected, detail = await visual_agent._drift_check([MagicMock()], frames)
        assert detected is False
        assert detail == ""


class TestDriftCheckSingleFrame:
    async def _run(self, visual_agent, resp_or_exc):
        ref = _mock_frame(b"ref")
        frame = _mock_frame(b"frame")
        if isinstance(resp_or_exc, Exception):
            visual_agent._vlm_client.chat.completions.create = AsyncMock(
                side_effect=resp_or_exc
            )
        else:
            visual_agent._vlm_client.chat.completions.create = AsyncMock(
                return_value=resp_or_exc
            )
        return await visual_agent._drift_check_single_frame([ref], frame)

    async def test_normal_drift_true(self, visual_agent):
        resp = _vlm_response('{"drift_detected": true, "details": "发色不同"}')
        detected, detail = await self._run(visual_agent, resp)
        assert detected is True
        assert detail == "发色不同"

    async def test_empty_content_uses_reasoning(self, visual_agent):
        resp = _vlm_response("", '{"drift_detected": false, "details": ""}')
        detected, detail = await self._run(visual_agent, resp)
        assert detected is False
        assert detail == ""

    async def test_markdown_block_stripped(self, visual_agent):
        resp = _vlm_response('```json\n{"drift_detected": true, "details": "服装变化"}\n```')
        detected, detail = await self._run(visual_agent, resp)
        assert detected is True
        assert detail == "服装变化"

    async def test_json_repair_path(self, visual_agent):
        resp = _vlm_response('{drift_detected: true, details: "妆容不同"}')
        detected, detail = await self._run(visual_agent, resp)
        assert detected is True
        assert detail == "妆容不同"

    async def test_non_dict_result_returns_no_drift(self, visual_agent):
        resp = _vlm_response('"无法解析的文本"')
        detected, detail = await self._run(visual_agent, resp)
        assert detected is False
        assert detail == ""

    async def test_character_absent_exempts_drift(self, visual_agent):
        """character_present=false → 无论 drift_detected 为何均豁免。"""
        resp = _vlm_response(
            '{"drift_detected": true, "character_present": false, "details": "POV 空镜"}'
        )
        detected, detail = await self._run(visual_agent, resp)
        assert detected is False
        assert detail == ""

    async def test_exception_falls_back(self, visual_agent):
        detected, detail = await self._run(visual_agent, RuntimeError("vlm down"))
        assert detected is False
        assert detail == ""


# ---------------------------------------------------------------------------
# _download_reference_image
# ---------------------------------------------------------------------------


class TestDownloadReferenceImageBoost:
    async def test_empty_url_returns_none(self, visual_agent):
        assert await visual_agent._download_reference_image("") is None
        assert await visual_agent._download_reference_image("   ") is None

    async def test_local_static_reuse(self, visual_agent, tmp_path, monkeypatch):
        """localhost /static/character/xxx.png 本地存在 → 直接复用。"""
        fake_file = tmp_path / "app" / "agents" / "quality_agent.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()
        monkeypatch.setattr(qa, "__file__", str(fake_file))

        char_dir = tmp_path / "output" / "character"
        char_dir.mkdir(parents=True)
        target = char_dir / "front.png"
        target.write_bytes(b"img")

        result = await visual_agent._download_reference_image(
            "http://localhost:8100/static/character/front.png"
        )
        assert result == target

    async def test_remote_streaming_download(self, visual_agent):
        """远程 URL → 流式下载，返回临时文件路径。"""
        visual_agent.http = MagicMock()
        visual_agent.http.stream = MagicMock(return_value=_FakeStreamCM([b"img-bytes"]))
        dest = await visual_agent._download_reference_image("http://remote/ref.jpg")
        try:
            assert dest is not None
            assert dest.suffix == ".jpg"
            assert dest.read_bytes() == b"img-bytes"
        finally:
            if dest is not None:
                dest.unlink(missing_ok=True)

    async def test_failure_returns_none(self, visual_agent):
        """下载异常 → 返回 None（调用方跳过，不阻断主检查）。"""
        failing_http = MagicMock()
        failing_http.stream = MagicMock(side_effect=RuntimeError("conn refused"))
        visual_agent.http = failing_http
        assert await visual_agent._download_reference_image("http://remote/ref.png") is None


# ---------------------------------------------------------------------------
# _extract_frames / _probe_duration
# ---------------------------------------------------------------------------


class TestExtractFrames:
    def _proc_mock(self):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    async def test_extracts_frames_evenly(self, visual_agent):
        """ffmpeg 成功产出帧文件 → 返回均匀分布的 (时间戳, 路径)。"""

        async def fake_exec(*args, **kwargs):
            Path(args[-1]).write_bytes(b"jpg")
            return self._proc_mock()

        with patch.object(
            visual_agent, "_probe_duration", new_callable=AsyncMock, return_value=9.0
        ):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
                frames = await visual_agent._extract_frames(Path("/tmp/v.mp4"), 2)

        assert len(frames) == 2
        assert frames[0][0] == pytest.approx(3.0)
        assert frames[1][0] == pytest.approx(6.0)
        assert all(p.exists() for _, p in frames)
        # 清理临时帧目录
        import shutil
        shutil.rmtree(frames[0][1].parent, ignore_errors=True)

    async def test_non_positive_duration_defaults_to_one(self, visual_agent):
        """ffprobe 时长 ≤0 → 按 1.0s 兜底计算抽帧步长。"""
        created: list[str] = []

        async def fake_exec(*args, **kwargs):
            created.append(args[-1])
            Path(args[-1]).write_bytes(b"jpg")
            return self._proc_mock()

        with patch.object(
            visual_agent, "_probe_duration", new_callable=AsyncMock, return_value=0.0
        ):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
                frames = await visual_agent._extract_frames(Path("/tmp/v.mp4"), 1)

        assert len(frames) == 1
        assert frames[0][0] == pytest.approx(0.5)
        import shutil
        shutil.rmtree(frames[0][1].parent, ignore_errors=True)

    async def test_no_frames_raises(self, visual_agent):
        """ffmpeg 未产出任何帧 → RuntimeError。"""

        async def fake_exec(*args, **kwargs):
            return self._proc_mock()  # 不创建帧文件

        with patch.object(
            visual_agent, "_probe_duration", new_callable=AsyncMock, return_value=5.0
        ):
            with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
                with pytest.raises(RuntimeError, match="未能从视频中抽取任何帧"):
                    await visual_agent._extract_frames(Path("/tmp/v.mp4"), 2)


class TestProbeDurationBoost:
    async def test_parses_duration(self, visual_agent):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"12.5\n", b""))
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            assert await visual_agent._probe_duration(Path("/tmp/v.mp4")) == 12.5

    async def test_invalid_output_returns_zero(self, visual_agent):
        """ffprobe 输出非数字 → 0.0。"""
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"N/A\n", b""))
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            assert await visual_agent._probe_duration(Path("/tmp/v.mp4")) == 0.0
