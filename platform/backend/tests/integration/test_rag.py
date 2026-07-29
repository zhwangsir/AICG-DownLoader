"""RAG 路由集成测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestRAGOptimize:
    def test_success(self, client):
        payload = {
            "user_prompt": "主角低头看着手机，眉头紧锁",
            "domain": "image",
            "style_hint": "都市悬疑",
        }
        with patch(
            "app.routers.drama.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value={
                "optimized_positive": "cinematic close-up, urban suspense",
                "optimized_negative": "blurry, low quality",
                "style_notes": "都市悬疑",
                "tags": ["都市", "悬疑"],
                "original_prompt": "主角低头看着手机，眉头紧锁",
                "retrieved_count": 3,
            },
        ):
            response = client.post("/api/drama/rag/optimize", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["optimized_positive"] == "cinematic close-up, urban suspense"
        assert data["retrieved_count"] == 3

    def test_failure_returns_500(self, client):
        payload = {"user_prompt": "test"}
        with patch(
            "app.routers.drama.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("embed model not found"),
        ):
            response = client.post("/api/drama/rag/optimize", json=payload)

        assert response.status_code == 500
        assert "RAG 优化失败" in response.json()["detail"]


class TestRAGStyles:
    def test_styles_list(self, client):
        with patch(
            "app.routers.drama.rag_service.get_styles",
            return_value=[{"id": "style_001", "title": "写实电影感"}],
        ):
            response = client.get("/api/drama/rag/styles")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "style_001"

    def test_styles_failure_returns_500(self, client):
        with patch(
            "app.routers.drama.rag_service.get_styles",
            side_effect=RuntimeError("init failed"),
        ):
            response = client.get("/api/drama/rag/styles")

        assert response.status_code == 500


class TestRAGInHealth:
    def test_health_includes_rag_status(self, client):
        response = client.get("/api/drama/health")
        assert response.status_code == 200
        data = response.json()
        assert "rag" in data
        assert "enabled" in data["rag"]
        assert "embed_model" in data["rag"]
