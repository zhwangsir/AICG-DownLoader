"""剧本 Agent 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.script_agent import (
    ScriptAgent,
    _normalize_scene_terms,
    validate_script_scenes,
)
from app.config import settings
from app.models.schemas import ScriptRequest


@pytest.fixture
def agent():
    return ScriptAgent()


class TestScriptAgentExecute:
    async def test_success(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps(
            {
                "title": "测试剧",
                "genre": "都市悬疑",
                "characters": [
                    {
                        "character_id": "char_001",
                        "name": "林远",
                        "role": "主角",
                        "age": 26,
                        "description": "外卖员",
                        "personality": "坚毅",
                    }
                ],
                "scenes": [
                    {
                        "scene_id": 1,
                        "episode": 1,
                        "shot_type": "特写",
                        "description": "主角看手机",
                        "prompt": "cinematic close-up",
                        "negative_prompt": "blurry",
                        "dialogue": "这单地址好熟悉",
                        "emotion": "tension",
                        "duration_seconds": 5,
                        "camera_movement": "static",
                    }
                ],
            }
        )

        request = ScriptRequest(premise="测试创意", genre="都市悬疑", episodes=1, scenes_per_episode=1)
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["title"] == "测试剧"
        assert len(response.data["characters"]) == 1
        assert len(response.data["scenes"]) == 1
        assert response.data["scenes"][0]["scene_id"] == 1

    async def test_json_repair_fallback(self, agent, mock_call_llm):
        mock_call_llm.return_value = '{"title": "测试", "characters": [{"character_id":"c1","name":"n"}], "scenes": [{"description":"d"}]}'

        request = ScriptRequest(premise="x")
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["title"] == "测试"

    async def test_invalid_entry_filtering(self, agent, mock_call_llm):
        mock_call_llm.return_value = json.dumps(
            {
                "title": "测试",
                "characters": [
                    {"character_id": "c1", "name": "有效角色"},
                    {"name": "无效角色"},  # 缺少 character_id
                    "not a dict",
                ],
                "scenes": [
                    {"description": "有效场景", "scene_id": 1},
                    {"scene_id": 2},  # 缺少 description
                    "not a dict",
                ],
            }
        )

        request = ScriptRequest(premise="x")
        response = await agent.execute(request)

        assert response.success is True
        assert len(response.data["characters"]) == 1
        assert len(response.data["scenes"]) == 1

    async def test_exception_returns_error(self, agent, mock_call_llm):
        mock_call_llm.side_effect = RuntimeError("LLM 失败")

        request = ScriptRequest(premise="x")
        response = await agent.execute(request)

        assert response.success is False
        assert "LLM 失败" in response.error
        assert response.elapsed_seconds >= 0


class TestScriptAgentRAGEnhance:
    async def test_rag_enhances_scene_prompts(self, agent, mock_call_llm, monkeypatch):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        mock_call_llm.return_value = json.dumps(
            {
                "title": "RAG 测试",
                "characters": [],
                "scenes": [
                    {
                        "scene_id": 1,
                        "description": "主角看手机",
                        "prompt": "original prompt",
                        "negative_prompt": "original negative",
                    }
                ],
            }
        )

        with patch(
            "app.agents.script_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={
                "optimized_positive": "rag positive",
                "optimized_negative": "rag negative",
            },
        ):
            response = await agent.execute(ScriptRequest(premise="x"))

        assert response.success is True
        scene = response.data["scenes"][0]
        assert scene["prompt"] == "rag positive"
        assert scene["negative_prompt"] == "rag negative"

    async def test_rag_failure_keeps_original_prompt(self, agent, mock_call_llm, monkeypatch):
        monkeypatch.setattr(settings, "rag_optimize_enabled", True)
        mock_call_llm.return_value = json.dumps(
            {
                "title": "RAG 失败测试",
                "characters": [],
                "scenes": [
                    {
                        "scene_id": 1,
                        "description": "主角看手机",
                        "prompt": "original prompt",
                        "negative_prompt": "original negative",
                    }
                ],
            }
        )

        with patch(
            "app.agents.script_agent.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("RAG 失败"),
        ):
            response = await agent.execute(ScriptRequest(premise="x"))

        assert response.success is True
        scene = response.data["scenes"][0]
        assert scene["prompt"] == "original prompt"
        assert scene["negative_prompt"] == "original negative"


def _make_scene(
    scene_id: int,
    episode: int = 1,
    shot_type: str = "特写",
    duration: int = 8,
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


class TestValidateScriptScenes:
    def test_compliant_script_passes(self):
        assert validate_script_scenes(_compliant_scenes(), episodes=1, scenes_per_episode=6) == []

    def test_missing_hook_reported(self):
        scenes = _compliant_scenes()
        scenes[0]["narrative_beat"] = "transition"
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("hook" in i for i in issues)

    def test_missing_cliffhanger_reported(self):
        scenes = _compliant_scenes()
        scenes[-1]["narrative_beat"] = "transition"
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("末镜" in i for i in issues)

    def test_final_episode_allows_emotional_beat_ending(self):
        scenes = _compliant_scenes()
        scenes[-1]["narrative_beat"] = "emotional_beat"
        assert validate_script_scenes(scenes, 1, 6) == []

    def test_episode_duration_out_of_range(self):
        scenes = _compliant_scenes()
        for s in scenes:
            s["duration_seconds"] = 2  # 总时长 12s，低于下限
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("总时长" in i for i in issues)

    def test_single_scene_duration_out_of_range(self):
        scenes = _compliant_scenes()
        scenes[2]["duration_seconds"] = 20
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("单镜时长" in i for i in issues)

    def test_three_same_shot_types_in_a_row(self):
        scenes = _compliant_scenes()
        scenes[1]["shot_type"] = "特写"
        scenes[2]["shot_type"] = "特写"  # scene 0/1/2 连续三个特写
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("连续 3 镜景别相同" in i for i in issues)

    def test_invalid_shot_type_reported(self):
        scenes = _compliant_scenes()
        scenes[0]["shot_type"] = "鸟瞰"
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("景别" in i and "白名单" in i for i in issues)

    def test_invalid_camera_movement_reported(self):
        scenes = _compliant_scenes()
        scenes[0]["camera_movement"] = "orbit"
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("运镜" in i and "白名单" in i for i in issues)

    def test_long_dialogue_reported(self):
        scenes = _compliant_scenes()
        scenes[0]["dialogue"] = "字" * 41
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("台词" in i for i in issues)

    def test_scene_count_mismatch_reported(self):
        scenes = _compliant_scenes()[:-1]  # 只有 5 镜
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("分镜数" in i for i in issues)

    def test_missing_episode_reported(self):
        scenes = _compliant_scenes()  # 全部 episode=1
        issues = validate_script_scenes(scenes, episodes=2, scenes_per_episode=6)
        assert any("第2集缺少分镜" in i for i in issues)

    def test_empty_scenes_reported(self):
        assert validate_script_scenes([], 1, 6) == ["剧本没有任何分镜"]

    def test_first_scene_duration_over_3s_reported(self):
        """黄金 3 秒原则：首镜（hook）时长不得超过 3s。"""
        scenes = _compliant_scenes()
        scenes[0]["duration_seconds"] = 5
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("首镜时长" in i and "3s" in i for i in issues)

    def test_weak_beat_density_over_15s_reported(self):
        """连续 15s+ 无强节拍（全 transition）须报情绪密度问题。"""
        scenes = _compliant_scenes()
        # 保留首镜 hook(3s)，中间 3 镜改 transition(8s×3=24s)，末镜 cliffhanger
        for idx in (1, 2, 3):
            scenes[idx]["narrative_beat"] = "transition"
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("情绪刺激点" in i for i in issues)

    def test_density_within_limit_passes(self):
        """弱节拍连续时长 ≤15s 不报密度问题。"""
        scenes = _compliant_scenes()
        scenes[1]["narrative_beat"] = "transition"  # 8s，未超 15s
        scenes[2]["narrative_beat"] = "transition"  # 累计 16s > 15s → 仍会报
        issues = validate_script_scenes(scenes, 1, 6)
        assert any("情绪刺激点" in i for i in issues)
        # 只改一镜（8s ≤ 15s）则通过
        scenes[2]["narrative_beat"] = "escalation"
        issues = validate_script_scenes(scenes, 1, 6)
        assert not any("情绪刺激点" in i for i in issues)


def _multi_episode_scenes(episodes: int, reversal_eps: set[int] | None = None) -> list[dict]:
    """多集合规剧本：每集 6 镜、43s、首 hook 末 cliffhanger；reversal 默认每集都有。"""
    beats = ["hook", "escalation", "escalation", "reversal", "emotional_beat", "cliffhanger"]
    shots = ["特写", "近景", "中景", "特写", "近景", "特写"]
    durs = [3, 8, 8, 8, 8, 8]
    scenes: list[dict] = []
    sid = 1
    for ep in range(1, episodes + 1):
        for i in range(6):
            beat = beats[i]
            if beat == "reversal" and reversal_eps is not None and ep not in reversal_eps:
                beat = "escalation"
            scenes.append(
                _make_scene(sid, episode=ep, shot_type=shots[i], duration=durs[i], beat=beat)
            )
            sid += 1
    return scenes


class TestMonetizationModeValidation:
    def test_iaa_mode_skips_paywall_check(self):
        scenes = _multi_episode_scenes(8, reversal_eps=set())  # 全部无 reversal
        issues = validate_script_scenes(scenes, 8, 6, monetization_mode="iaa")
        assert not any("付费卡点" in i for i in issues)

    def test_iap_paywall_missing_reversal_reported(self):
        """IAP 模式第 8-12 集无 reversal → 报付费卡点缺失。"""
        scenes = _multi_episode_scenes(8, reversal_eps={1, 2, 3})  # 8 集只在 1-3 有 reversal
        issues = validate_script_scenes(scenes, 8, 6, monetization_mode="iap")
        assert any("付费卡点" in i for i in issues)

    def test_iap_paywall_with_reversal_passes(self):
        scenes = _multi_episode_scenes(8, reversal_eps={8})
        issues = validate_script_scenes(scenes, 8, 6, monetization_mode="iap")
        assert issues == []

    def test_iap_under_8_episodes_skips_paywall_check(self):
        scenes = _multi_episode_scenes(3, reversal_eps=set())
        issues = validate_script_scenes(scenes, 3, 6, monetization_mode="iap")
        assert not any("付费卡点" in i for i in issues)

    def test_default_mode_is_iaa(self):
        """不传 monetization_mode 保持向后兼容（默认 iaa，不检查付费卡点）。"""
        scenes = _multi_episode_scenes(8, reversal_eps=set())
        issues = validate_script_scenes(scenes, 8, 6)
        assert not any("付费卡点" in i for i in issues)


class TestNormalizeSceneTerms:
    def test_english_shot_type_mapped(self):
        scene = {"shot_type": "Close-Up", "camera_movement": "static"}
        _normalize_scene_terms(scene)
        assert scene["shot_type"] == "特写"

    def test_chinese_camera_movement_mapped(self):
        scene = {"shot_type": "中景", "camera_movement": "手持"}
        _normalize_scene_terms(scene)
        assert scene["camera_movement"] == "handheld"

    def test_valid_terms_unchanged(self):
        scene = {"shot_type": "近景", "camera_movement": "dolly"}
        _normalize_scene_terms(scene)
        assert scene["shot_type"] == "近景"
        assert scene["camera_movement"] == "dolly"


class TestRepairLoop:
    async def test_repair_fixes_noncompliant_script(self, agent, mock_call_llm):
        """首次输出不合规 → 触发返修 → 返修后合规 → call_llm 共调用 2 次。"""
        bad = _compliant_scenes()
        bad[0]["narrative_beat"] = "transition"  # 缺钩子
        good_json = json.dumps({"title": "修复后", "characters": [], "scenes": _compliant_scenes()})
        bad_json = json.dumps({"title": "原始", "characters": [], "scenes": bad})
        mock_call_llm.side_effect = [bad_json, json.dumps({"scenes": _compliant_scenes()})]

        response = await agent.execute(ScriptRequest(premise="x", episodes=1, scenes_per_episode=6))

        assert response.success is True
        assert mock_call_llm.await_count == 2
        beats = [s["narrative_beat"] for s in response.data["scenes"]]
        assert beats[0] == "hook" and beats[-1] == "cliffhanger"
        assert good_json  # 避免未使用告警（结构对照）

    async def test_repair_failure_still_returns_script(self, agent, mock_call_llm):
        """返修 LLM 异常 → 放行原始结果，接口仍成功。"""
        bad = _compliant_scenes()
        bad[0]["narrative_beat"] = "transition"
        mock_call_llm.side_effect = [
            json.dumps({"title": "原始", "characters": [], "scenes": bad}),
            RuntimeError("返修失败"),
        ]

        response = await agent.execute(ScriptRequest(premise="x", episodes=1, scenes_per_episode=6))

        assert response.success is True
        assert response.data["scenes"][0]["narrative_beat"] == "transition"

    async def test_compliant_script_skips_repair(self, agent, mock_call_llm):
        """一次通过校验 → 不触发返修，call_llm 仅 1 次。"""
        mock_call_llm.return_value = json.dumps(
            {"title": "合规", "characters": [], "scenes": _compliant_scenes()}
        )

        response = await agent.execute(ScriptRequest(premise="x", episodes=1, scenes_per_episode=6))

        assert response.success is True
        assert mock_call_llm.await_count == 1
