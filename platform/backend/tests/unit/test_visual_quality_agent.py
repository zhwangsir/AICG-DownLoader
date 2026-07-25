"""视觉质检 Agent 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.quality_agent import VisualQualityAgent
from app.models.schemas import QualityVisualRequest


@pytest.fixture
def visual_agent():
    agent = VisualQualityAgent()
    agent.llm_client = MagicMock()
    # VisualQualityAgent 使用独立的 _vlm_client, 而非继承自 BaseAgent 的 llm_client
    # 预设 MagicMock 避免 _get_vlm_client 触发真实 AsyncOpenAI 创建
    agent._vlm_client = MagicMock()
    return agent


class TestVisualQualityAgent:
    async def test_fallback_when_model_not_configured(self, visual_agent):
        with patch("app.agents.quality_agent.settings.visual_model_url", ""):
            request = QualityVisualRequest(
                project_id="p1",
                title="测试视频",
                scene_id=1,
                video_url="http://x/v.mp4",
            )
            response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 0
        assert "未部署" in response.data["summary"]
        assert response.data["issues"][0]["category"] == "system"

    async def test_success_with_frames(self, visual_agent):
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = (
            '{"score": 88, "summary": "画面连贯", "issues": []}'
        )
        fake_resp.choices[0].message.reasoning_content = None

        visual_agent._vlm_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
            max_frames=2,
        )

        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with patch.object(visual_agent, "_download_video", new_callable=AsyncMock) as mock_dl:
                mock_dl.return_value = MagicMock()
                with patch.object(
                    visual_agent, "_extract_frames", new_callable=AsyncMock
                ) as mock_extract:
                    frame_path = MagicMock()
                    frame_path.read_bytes.return_value = b"fake_image"
                    mock_extract.return_value = [(1.0, frame_path), (2.0, frame_path)]
                    response = await visual_agent.execute(request)

        assert response.success is True
        assert response.data["score"] == 88
        assert response.data["summary"] == "画面连贯"

    async def test_json_decode_error(self, visual_agent):
        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = "not json"
        fake_resp.choices[0].message.reasoning_content = None

        visual_agent._vlm_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        request = QualityVisualRequest(
            project_id="p1",
            title="测试视频",
            scene_id=1,
            video_url="http://x/v.mp4",
        )

        with patch("app.agents.quality_agent.settings.visual_model_url", "http://vlm"):
            with patch.object(visual_agent, "_download_video", new_callable=AsyncMock) as mock_dl:
                mock_dl.return_value = MagicMock()
                with patch.object(
                    visual_agent, "_extract_frames", new_callable=AsyncMock
                ) as mock_extract:
                    frame_path = MagicMock()
                    frame_path.read_bytes.return_value = b"fake_image"
                    mock_extract.return_value = [(1.0, frame_path)]
                    response = await visual_agent.execute(request)

        assert response.success is False
        assert "JSON 解析失败" in response.error

    async def test_probe_duration(self, visual_agent):
        proc_mock = MagicMock()
        proc_mock.communicate = AsyncMock(return_value=(b"12.5\n", b""))
        proc_mock.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc_mock
        ):
            duration = await visual_agent._probe_duration(MagicMock())

        assert duration == 12.5
