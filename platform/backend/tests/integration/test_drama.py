"""drama 路由集成测试 —— 覆盖 /api/drama/* 端点。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """返回已配置好的 TestClient。"""
    return TestClient(app)


class TestHealth:
    def test_health(self, client):
        response = client.get("/api/drama/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "agents" in data
        assert "script_agent" in data["agents"]


class TestScriptGenerate:
    def test_success(self, client):
        payload = {
            "premise": "一个外卖员收到最后一单，发现地址是自己家",
            "genre": "都市悬疑",
            "episodes": 1,
            "scenes_per_episode": 5,
        }
        with patch(
            "app.routers.drama.script_agent.execute",
            new_callable=AsyncMock,
            return_value={"success": True, "data": {"title": "最后的订单"}, "elapsed_seconds": 1.0},
        ):
            response = client.post("/api/drama/script/generate", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["title"] == "最后的订单"


class TestCharacterGenerate:
    def test_success(self, client):
        payload = {
            "character": {
                "character_id": "char_001",
                "name": "林远",
                "role": "主角",
                "age": 26,
                "description": "年轻外卖员",
            },
            "style": "写实电影感",
        }
        with patch(
            "app.routers.drama.character_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {"character_id": "char_001", "image_urls": ["http://x/a.png"]},
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/character/generate", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestStoryboardGenerate:
    def test_success(self, client):
        payload = {
            "scene": {
                "scene_id": 1,
                "description": "主角低头看手机",
                "prompt": "close-up phone",
            },
            "characters": [{"character_id": "char_001", "name": "林远"}],
        }
        with patch(
            "app.routers.drama.storyboard_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {"scene_id": 1, "image_url": "http://x/sb.png"},
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/storyboard/generate", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestVideoGenerate:
    def test_success(self, client):
        payload = {
            "scene_id": 1,
            "image_url": "http://x/sb.png",
            "prompt": "a man looking at phone",
            "duration_seconds": 3,
        }
        with patch(
            "app.routers.drama.video_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {"scene_id": 1, "video_url": "http://x/v.mp4"},
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/video/generate", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestVoiceGenerate:
    def test_success(self, client):
        payload = {
            "scene_id": 1,
            "dialogues": [
                {"text": "你好", "character_name": "林远", "character_role": "主角"}
            ],
        }
        with patch(
            "app.routers.drama.voice_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {"scene_id": 1, "audio_urls": [{"audio_url": "http://x/a.mp3"}]},
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/voice/generate", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestSubtitleGenerate:
    def test_success(self, client):
        payload = {
            "scene_id": 1,
            "audio_url": "http://x/a.mp3",
            "language": "zh",
        }
        with patch(
            "app.routers.drama.subtitle_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {"scene_id": 1, "srt_content": "1\n00:00:00,000 --> 00:00:01,000\n测试"},
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/subtitle/generate", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "测试" in data["data"]["srt_content"]


class TestEditCompose:
    def test_success(self, client):
        payload = {
            "project_id": "route-test",
            "title": "路由测试",
            "segments": [
                {
                    "scene_id": 1,
                    "video_url": "http://x/v.mp4",
                    "audio_url": "http://x/a.mp3",
                    "subtitle_url": "http://x/s.srt",
                }
            ],
        }
        with patch(
            "app.routers.drama.edit_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {"final_video_url": "http://x/final.mp4"},
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/edit/compose", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestAsyncGenerate:
    def test_video_async_success(self, client):
        payload = {
            "scene_id": 1,
            "image_url": "http://x/sb.png",
            "prompt": "a man looking at phone",
            "duration_seconds": 3,
        }

        async def fake_execute(request, progress_callback=None):
            if progress_callback:
                progress_callback(50, "generating")
            return {
                "success": True,
                "data": {"scene_id": 1, "video_url": "http://x/v.mp4"},
                "elapsed_seconds": 1.0,
            }

        with patch(
            "app.routers.drama.video_agent.execute",
            new=fake_execute,
        ):
            response = client.post("/api/drama/video/generate_async", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert "task_id" in data
        assert data["agent"] == "video"
        assert data["stream_url"].endswith("/stream")

    def test_async_unknown_agent(self, client):
        response = client.post("/api/drama/unknown/generate_async", json={})
        assert response.status_code == 404

    def test_async_invalid_payload(self, client):
        response = client.post("/api/drama/video/generate_async", json={"scene_id": "not-a-number"})
        assert response.status_code == 422


class TestQualityCheck:
    def test_success(self, client):
        payload = {
            "project_id": "q-test",
            "title": "测试短剧",
            "characters": [{"character_id": "c1", "name": "Alice", "role": "主角"}],
            "scenes": [{"scene_id": 1, "description": "开场", "dialogue": "你好"}],
            "subtitles": [],
        }
        with patch(
            "app.routers.drama.quality_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {"project_id": "q-test", "title": "测试短剧", "score": 90, "issues": []},
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/quality/check", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["score"] == 90


class TestVisualQualityCheck:
    def test_success(self, client):
        payload = {
            "project_id": "vq-test",
            "title": "视觉质检测试",
            "scene_id": 1,
            "video_url": "http://x/v.mp4",
            "max_frames": 2,
        }
        with patch(
            "app.routers.drama.visual_quality_agent.execute",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "data": {
                    "project_id": "vq-test",
                    "title": "视觉质检测试",
                    "scene_id": 1,
                    "score": 85,
                    "issues": [],
                },
                "elapsed_seconds": 1.0,
            },
        ):
            response = client.post("/api/drama/quality/visual", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["score"] == 85

    def test_missing_video_url(self, client):
        response = client.post("/api/drama/quality/visual", json={"scene_id": 1})
        assert response.status_code == 422


class TestValidationErrors:
    def test_script_missing_premise(self, client):
        response = client.post("/api/drama/script/generate", json={})
        assert response.status_code == 422

    def test_video_missing_image_url(self, client):
        response = client.post("/api/drama/video/generate", json={"scene_id": 1})
        assert response.status_code == 422
