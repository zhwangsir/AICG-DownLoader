"""质检 Agent 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.quality_agent import QualityAgent
from app.models.schemas import Character, QualityCheckRequest, Scene, SubtitleResult


@pytest.fixture
def quality_agent():
    agent = QualityAgent()
    agent.llm_client = MagicMock()
    return agent


class TestQualityAgent:
    async def test_success(self, quality_agent):
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].delta.content = (
            '{"score": 85, "summary": "良好", "issues": [{"category": "logic", "severity": "warning", "scene_id": 1, "message": "剧情跳跃", "suggestion": "补充过渡"}]}'
        )
        fake_resp.choices[0].delta.reasoning_content = None

        mock = MagicMock()
        mock.__aiter__.return_value = iter([fake_resp])
        quality_agent.llm_client.chat.completions.create = AsyncMock(return_value=mock)

        request = QualityCheckRequest(
            project_id="p1",
            title="测试短剧",
            characters=[Character(character_id="c1", name="Alice", role="主角")],
            scenes=[Scene(scene_id=1, description="开场", dialogue="你好")],
        )
        response = await quality_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 85
        # LLM 返回的 logic 问题必须存在（结构校验/高风险打标可能追加额外 issue）
        logic_issues = [i for i in response.data["issues"] if i["category"] == "logic"]
        assert len(logic_issues) == 1
        assert logic_issues[0]["message"] == "剧情跳跃"
        # 极简场景缺少 narrative_beat，确定性结构校验应报末镜节拍问题
        assert any(i["category"] == "structure" for i in response.data["issues"])

    async def test_json_decode_error(self, quality_agent):
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].delta.content = "not json"
        fake_resp.choices[0].delta.reasoning_content = None

        mock = MagicMock()
        mock.__aiter__.return_value = iter([fake_resp])
        quality_agent.llm_client.chat.completions.create = AsyncMock(return_value=mock)

        request = QualityCheckRequest(project_id="p1", title="测试")
        response = await quality_agent.execute(request)

        assert response.success is False
        assert "JSON 解析失败" in response.error

    async def test_serialization_empty(self, quality_agent):
        request = QualityCheckRequest(project_id="p1", title="测试")
        assert quality_agent._serialize_characters(request.characters) == "无"
        assert quality_agent._serialize_scenes(request.scenes) == "无"
        assert quality_agent._serialize_subtitles(request.subtitles) == "无"

    async def test_serialization_subtitles(self, quality_agent):
        subtitle = SubtitleResult(
            scene_id=1,
            srt_content="",
            segments=[{"start": 0, "end": 1, "text": "你好"}],
            language="zh",
        )
        result = quality_agent._serialize_subtitles([subtitle])
        assert "你好" in result


class TestMultishotDriftLabel:
    """M12.3：多镜联合生成组自动标注跨镜角色漂移风险（H3 独有失败模式）。"""

    @staticmethod
    def _scene(scene_id: int, episode: int = 1, duration: int = 4) -> Scene:
        return Scene(
            scene_id=scene_id,
            episode=episode,
            duration_seconds=duration,
            description=f"场景{scene_id}",
        )

    def test_adjacent_group_labeled(self, quality_agent, monkeypatch):
        """同集相邻 3 场景（12s ≤ 14s 上限）将并入一个多镜组 → 逐场景标注漂移风险。"""
        monkeypatch.setattr("app.agents.quality_agent.settings.video_backend", "h3")
        scenes = [self._scene(1), self._scene(2), self._scene(3)]
        issues = quality_agent._multishot_group_issues(scenes)
        assert len(issues) == 3
        assert all(i.category == "visual_risk" for i in issues)
        assert all("多镜" in i.message for i in issues)
        assert {i.scene_id for i in issues} == {1, 2, 3}

    def test_cross_episode_not_labeled(self, quality_agent, monkeypatch):
        """跨集场景不并组 → 无多镜标注。"""
        monkeypatch.setattr("app.agents.quality_agent.settings.video_backend", "h3")
        scenes = [self._scene(1, episode=1), self._scene(2, episode=2)]
        assert quality_agent._multishot_group_issues(scenes) == []

    def test_single_scene_not_labeled(self, quality_agent, monkeypatch):
        """单场景不成组 → 无标注。"""
        monkeypatch.setattr("app.agents.quality_agent.settings.video_backend", "h3")
        assert quality_agent._multishot_group_issues([self._scene(1)]) == []

    def test_non_h3_backend_not_labeled(self, quality_agent):
        """非 H3 后端（conftest 默认 comfyui）→ 不标注（多镜是 H3 独有路径）。"""
        scenes = [self._scene(1), self._scene(2), self._scene(3)]
        assert quality_agent._multishot_group_issues(scenes) == []

    def test_multishot_disabled_not_labeled(self, quality_agent, monkeypatch):
        """h3_multishot_enabled=False → 不标注。"""
        monkeypatch.setattr("app.agents.quality_agent.settings.video_backend", "h3")
        monkeypatch.setattr("app.agents.quality_agent.settings.h3_multishot_enabled", False)
        scenes = [self._scene(1), self._scene(2)]
        assert quality_agent._multishot_group_issues(scenes) == []
