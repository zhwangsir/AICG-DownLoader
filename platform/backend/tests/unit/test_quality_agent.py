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
        assert len(response.data["issues"]) == 1
        assert response.data["issues"][0]["category"] == "logic"

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
