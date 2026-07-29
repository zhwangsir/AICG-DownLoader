"""剧本 Agent 单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.script_agent import ScriptAgent
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
