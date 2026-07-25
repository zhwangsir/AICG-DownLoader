"""进度 SSE 路由集成测试。"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.progress import progress_tracker
from app.main import app


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestProgressStream:
    async def test_stream_unknown_task(self, async_client: AsyncClient):
        response = await async_client.get("/api/progress/not-exist/stream")
        assert response.status_code == 404

    async def test_get_unknown_task(self, async_client: AsyncClient):
        response = await async_client.get("/api/progress/not-exist")
        assert response.status_code == 404

    async def test_get_task(self, async_client: AsyncClient):
        task_id = progress_tracker.create("video", "test")
        response = await async_client.get(f"/api/progress/{task_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == task_id
        assert data["status"] == "pending"

    async def test_stream_receives_update(self, async_client: AsyncClient):
        task_id = progress_tracker.create("video", "test")

        async def push_updates():
            await asyncio.sleep(0.05)
            progress_tracker.update(task_id, status="running", percent=50, message="half")
            await asyncio.sleep(0.05)
            progress_tracker.update(task_id, status="completed", percent=100, message="done")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(push_updates())
            response = await async_client.get(f"/api/progress/{task_id}/stream")
            assert response.status_code == 200
            body = ""
            async for chunk in response.aiter_text():
                body += chunk
                if "completed" in body:
                    break

        assert "video" in body
        assert "half" in body or "done" in body
