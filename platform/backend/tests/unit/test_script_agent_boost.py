"""剧本 Agent 覆盖率补強测试 — 针对 test_script_agent.py 未触及的分支。

覆盖目标：
- _normalize_scene_terms：未知运镜小写兜底
- validate_script_scenes：非整数时长四处容错、非法 narrative_beat
- execute：联网参考资料注入（日志 + user 消息）、非 dict LLM 输出报错、返修结构异常放行
- _parse_llm_json：空内容 / json_repair 修复 / 双重转义 / 片段截取全链路
- _rag_enhance_scenes：空 description 跳过
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.script_agent import (
    ScriptAgent,
    _normalize_scene_terms,
    validate_script_scenes,
)
from app.models.schemas import ScriptRequest


@pytest.fixture
def agent():
    return ScriptAgent()


def _make_scene(
    scene_id: int,
    episode: int = 1,
    shot_type: str = "特写",
    duration: object = 8,
    beat: str = "escalation",
    movement: str = "static",
    dialogue: str = "短台词",
) -> dict:
    return {
        "scene_id": scene_id,
        "episode": episode,
        "shot_type": shot_type,
        "description": f"场景{scene_id}描述",
        "prompt": "cinematic",
        "dialogue": dialogue,
        "duration_seconds": duration,
        "camera_movement": movement,
        "narrative_beat": beat,
    }


def _compliant_scenes() -> list[dict]:
    """6 镜合规剧本：首镜 hook、末镜 cliffhanger、总时长 43s、景别无连三。"""
    beats = ["hook", "escalation", "escalation", "reversal", "emotional_beat", "cliffhanger"]
    shots = ["特写", "近景", "中景", "特写", "近景", "特写"]
    durs = [3, 8, 8, 8, 8, 8]
    return [
        _make_scene(i + 1, shot_type=shots[i], duration=durs[i], beat=beats[i])
        for i in range(6)
    ]


class TestNormalizeUnknownMovement:
    def test_unknown_movement_lowercased(self):
        """未知运镜既不在英文白名单也不在中文映射表 → 小写兜底（第 84 行）。"""
        scene = {"shot_type": "中景", "camera_movement": "ORBIT"}
        _normalize_scene_terms(scene)
        assert scene["camera_movement"] == "orbit"

    def test_unknown_movement_not_in_zh_map_but_valid_shot(self):
        """景别合法不动、运镜走小写兜底。"""
        scene = {"shot_type": "近景", "camera_movement": "Crane"}
        _normalize_scene_terms(scene)
        assert scene["shot_type"] == "近景"
        assert scene["camera_movement"] == "crane"


class TestValidateNonIntegerDuration:
    def test_non_integer_duration_all_guards(self):
        """首镜时长为字符串：四处 try/except 全部走异常分支（124-125/139-140/162-163/182-183）。"""
        scenes = _compliant_scenes()
        scenes[0]["duration_seconds"] = "abc"
        issues = validate_script_scenes(scenes, 1, 6)
        # 总时长累加处记录非整数问题
        assert any("时长不是整数" in i for i in issues)
        # 首镜时长/单镜时长/密度累计三处静默跳过，不因异常崩溃
        assert not any("首镜时长" in i for i in issues)

    def test_none_duration_also_guarded(self):
        """duration_seconds=None 触发 TypeError 分支。"""
        scenes = _compliant_scenes()
        scenes[0]["duration_seconds"] = None
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("时长不是整数" in i for i in issues)


class TestValidateInvalidBeat:
    def test_invalid_narrative_beat_reported(self):
        """非空白名单外 narrative_beat 须报白名单问题（第 166 行）。"""
        scenes = _compliant_scenes()
        scenes[1]["narrative_beat"] = "twist"
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("twist" in i and "不在白名单" in i for i in issues)


class TestExecuteReferenceInjection:
    async def test_reference_logged_and_injected(self, agent, mock_call_llm, mock_web_search):
        """联网搜索到资料 → 记日志（第 314 行）且注入 user 消息（第 350 行）。"""
        mock_web_search.return_value = "参考资料XYZ"
        mock_call_llm.return_value = json.dumps(
            {"title": "合规", "characters": [], "scenes": _compliant_scenes()}
        )

        request = ScriptRequest(premise="外卖员的最后一单", episodes=1, scenes_per_episode=6, web_search=True)
        response = await agent.execute(request)

        assert response.success is True
        assert mock_call_llm.await_count == 1
        user_msg = mock_call_llm.call_args_list[0].kwargs["messages"][1]["content"]
        assert "参考资料（联网搜索，供借鉴剧情节奏和角色设计手法）" in user_msg
        assert "参考资料XYZ" in user_msg
        # 搜索查询词包含题材与创意前缀
        query = mock_web_search.call_args.args[0]
        assert "都市悬疑" in query and "外卖员的最后一单" in query


class TestExecuteNonDictOutput:
    async def test_list_payload_returns_error(self, agent, mock_call_llm):
        """LLM 返回合法 JSON 但非对象 → 报错并走异常出口（第 367-368 行）。"""
        mock_call_llm.return_value = "[1, 2, 3]"

        response = await agent.execute(ScriptRequest(premise="x"))

        assert response.success is False
        assert "格式异常" in response.error
        assert response.elapsed_seconds >= 0


class TestRepairAbnormalStructure:
    async def test_repair_dict_without_scenes_returns_none(self, agent, mock_call_llm):
        """返修返回的 dict 缺 scenes 列表 → 放弃返修放行原始结果（第 483-484 行）。"""
        bad = _compliant_scenes()
        bad[0]["narrative_beat"] = "transition"  # 触发校验问题进入返修
        mock_call_llm.side_effect = [
            json.dumps({"title": "原始", "characters": [], "scenes": bad}),
            json.dumps({"not_scenes": True}),
        ]

        response = await agent.execute(ScriptRequest(premise="x", episodes=1, scenes_per_episode=6))

        assert response.success is True
        assert mock_call_llm.await_count == 2
        # 返修被放弃，原始不合规分镜原样放行
        assert response.data["scenes"][0]["narrative_beat"] == "transition"

    async def test_repair_list_payload_returns_none(self, agent, mock_call_llm):
        """直接调用 _repair_scenes：返修返回 list（非 dict）→ None。"""
        mock_call_llm.return_value = "[1, 2, 3]"
        result = await agent._repair_scenes([], ["问题"], ScriptRequest(premise="x"))
        assert result is None


class TestRagEnhanceSkipsBlankDescription:
    async def test_blank_description_skipped(self, agent):
        """description 为空白 → continue 跳过 RAG（第 560 行），其余场景正常优化。"""
        scenes = [
            {"scene_id": 1, "description": "   ", "prompt": "p1"},
            {"scene_id": 2, "description": "主角看手机", "prompt": "p2"},
        ]
        with patch(
            "app.agents.script_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={"optimized_positive": "优化后正向"},
        ) as mock_opt:
            await agent._rag_enhance_scenes(scenes, "都市悬疑")

        assert mock_opt.await_count == 1
        assert mock_opt.call_args.kwargs["user_prompt"] == "主角看手机"
        assert mock_opt.call_args.kwargs["style_hint"] == "都市悬疑"
        assert scenes[0]["prompt"] == "p1"
        assert scenes[1]["prompt"] == "优化后正向"


class TestParseLlmJson:
    """_parse_llm_json 多层容错解析（第 500/507-553 行）。"""

    def test_empty_content_returns_none(self):
        assert ScriptAgent._parse_llm_json("") is None
        assert ScriptAgent._parse_llm_json("   \n\t ") is None

    def test_json_repair_repairs_broken_json(self):
        """标准解析失败 → json_repair 修复成功（511-512）。"""
        result = ScriptAgent._parse_llm_json('{"title": "未闭合')
        assert isinstance(result, dict)
        assert result["title"] == "未闭合"

    def test_json_repair_raises_returns_none(self):
        """json_repair 自身抛异常 → parsed=None 直接返回（513-514, 553）。"""
        with patch(
            "app.agents.script_agent.json_repair.loads",
            side_effect=RuntimeError("repair boom"),
        ):
            assert ScriptAgent._parse_llm_json("{broken") is None

    def test_double_escaped_via_second_json_loads(self):
        """json_repair 返回双重转义字符串 → 二次 json.loads 命中（518-521）。"""
        with patch(
            "app.agents.script_agent.json_repair.loads",
            return_value='{"a": 1}',
        ):
            assert ScriptAgent._parse_llm_json("garbage") == {"a": 1}

    def test_double_escaped_via_second_repair(self):
        """二次 json.loads 仍失败 → 二次 json_repair 命中（524-526）。"""
        with patch(
            "app.agents.script_agent.json_repair.loads",
            side_effect=["plain text", {"b": 2}],
        ):
            assert ScriptAgent._parse_llm_json("garbage") == {"b": 2}

    def test_snippet_extraction_success(self):
        """二次修复也失败 → 从文本截取 {...} 片段解析成功（527-546）。"""
        with patch(
            "app.agents.script_agent.json_repair.loads",
            side_effect=['前言 {"c": 3} 后记', RuntimeError("second fails")],
        ):
            assert ScriptAgent._parse_llm_json("garbage") == {"c": 3}

    def test_snippet_repair_fallback(self):
        """截取的片段标准解析失败 → 片段级 json_repair 兜底成功（547-549）。"""
        with patch(
            "app.agents.script_agent.json_repair.loads",
            side_effect=["前缀 {bad, json} 后缀", RuntimeError("e1"), {"d": 4}],
        ):
            assert ScriptAgent._parse_llm_json("garbage") == {"d": 4}

    def test_snippet_all_fail_returns_string(self):
        """片段解析与片段修复全失败 → 返回原始字符串（550-551, 553）。"""
        raw = "前缀 {bad, json} 后缀"
        with patch(
            "app.agents.script_agent.json_repair.loads",
            side_effect=[raw, RuntimeError("e1"), RuntimeError("e2")],
        ):
            assert ScriptAgent._parse_llm_json("garbage") == raw

    def test_no_braces_returns_string(self):
        """文本中无 {} / [] 边界 → 跳过截取，返回原字符串（543 条件不满足）。"""
        with patch(
            "app.agents.script_agent.json_repair.loads",
            side_effect=["纯文本无括号", RuntimeError("e1")],
        ):
            assert ScriptAgent._parse_llm_json("garbage") == "纯文本无括号"
