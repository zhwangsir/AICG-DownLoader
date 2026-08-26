"""M25.1: POST /video/rerun-shot 单镜头锚点重拍 API 测试。

覆盖：快照恢复重建 VideoRequest、seed 锁定/覆盖、override_prompt、
成功回写快照、失败隔离（其余镜头不受影响）、404 分支。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import AgentResponse
from app.services.pipeline_orchestrator import PipelineOrchestrator


def _write_snapshot(tmp_path: Path, project_id: str = "pipeline-test") -> Path:
    """按 conftest 重定向后的根目录造一份两镜头快照。"""
    payload = {
        "project_id": project_id,
        "saved_at": 1786720000.0,
        "shots": [
            {
                "scene_id": 1,
                "image_url": "http://x/kf1.png",
                "prompt": "镜头1 prompt",
                "negative_prompt": "blurry",
                "duration_seconds": 5,
                "reference_images": ["http://x/front.png"],
                "episode": 1,
                "narrative_beat": "hook",
                "last_frame_url": "",
                "reference_videos": [],
                "reference_audios": [],
                "style": "写实电影感",
                "engine": "h3",
                "seed": 42,
                "lock_params": {"engine": "h3", "steps": 8},
                "status": "success",
                "video_url": "http://x/v1.mp4",
            },
            {
                "scene_id": 2,
                "image_url": "http://x/kf2.png",
                "prompt": "镜头2 prompt",
                "negative_prompt": "blurry",
                "duration_seconds": 5,
                "reference_images": [],
                "episode": 1,
                "narrative_beat": "",
                "last_frame_url": "",
                "reference_videos": [],
                "reference_audios": [],
                "style": "写实电影感",
                "engine": "ltx",
                "seed": None,
                "lock_params": None,
                "status": "success",
                "video_url": "http://x/v2.mp4",
            },
        ],
    }
    path = tmp_path / "pipeline" / project_id / "shot_params.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def client():
    return TestClient(app)


class TestRerunShot404:
    def test_snapshot_missing(self, client, tmp_path):
        resp = client.post(
            "/api/drama/video/rerun-shot",
            json={"project_id": "pipeline-nope", "scene_id": 1},
        )
        assert resp.status_code == 404
        assert "快照不存在" in resp.json()["detail"]

    def test_scene_missing(self, client, tmp_path):
        _write_snapshot(tmp_path)
        resp = client.post(
            "/api/drama/video/rerun-shot",
            json={"project_id": "pipeline-test", "scene_id": 99},
        )
        assert resp.status_code == 404
        assert "无镜头" in resp.json()["detail"]

    def test_seed_out_of_range_422(self, client, tmp_path):
        _write_snapshot(tmp_path)
        resp = client.post(
            "/api/drama/video/rerun-shot",
            json={"project_id": "pipeline-test", "scene_id": 1, "seed": -1},
        )
        assert resp.status_code == 422


class TestRerunShotExecution:
    def test_rebuilds_request_from_snapshot_with_locked_seed(self, client, tmp_path):
        _write_snapshot(tmp_path)
        captured: dict = {}

        async def fake_execute(req, progress_callback=None, worker_url=None):
            captured["req"] = req
            return AgentResponse(
                success=True,
                data={"scene_id": 1, "video_url": "http://x/v1_rerun.mp4", "duration_seconds": 5},
                elapsed_seconds=1.0,
            )

        with patch("app.routers.drama.video_agent.execute", new=AsyncMock(side_effect=fake_execute)):
            resp = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 1},
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        req = captured["req"]
        # 快照字段完整重建
        assert req.scene_id == 1
        assert req.image_url == "http://x/kf1.png"
        assert req.prompt == "镜头1 prompt"
        assert req.engine == "h3"
        assert req.seed == 42  # 快照锁定 seed 沿用
        assert req.lock_params == {"engine": "h3", "steps": 8}
        assert req.reference_images == ["http://x/front.png"]
        assert req.style == "写实电影感"
        # 成功回写：该镜头 video_url 更新
        snapshot = PipelineOrchestrator.load_shot_params("pipeline-test")
        shot1 = next(s for s in snapshot["shots"] if s["scene_id"] == 1)
        assert shot1["video_url"] == "http://x/v1_rerun.mp4"
        assert shot1["status"] == "success"
        assert "rerun_at" in shot1
        # 其余镜头不受影响
        shot2 = next(s for s in snapshot["shots"] if s["scene_id"] == 2)
        assert shot2["video_url"] == "http://x/v2.mp4"
        assert "rerun_at" not in shot2

    def test_seed_override(self, client, tmp_path):
        _write_snapshot(tmp_path)
        captured: dict = {}

        async def fake_execute(req, progress_callback=None, worker_url=None):
            captured["req"] = req
            return AgentResponse(success=True, data={"scene_id": 1, "video_url": "http://x/v.mp4"}, elapsed_seconds=1.0)

        with patch("app.routers.drama.video_agent.execute", new=AsyncMock(side_effect=fake_execute)):
            resp = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 1, "seed": 777},
            )
        assert resp.status_code == 200
        assert captured["req"].seed == 777  # 覆盖快照 seed=42

    def test_override_prompt(self, client, tmp_path):
        _write_snapshot(tmp_path)
        captured: dict = {}

        async def fake_execute(req, progress_callback=None, worker_url=None):
            captured["req"] = req
            return AgentResponse(success=True, data={"scene_id": 1, "video_url": "http://x/v.mp4"}, elapsed_seconds=1.0)

        with patch("app.routers.drama.video_agent.execute", new=AsyncMock(side_effect=fake_execute)):
            resp = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 1, "override_prompt": "新 prompt"},
            )
        assert resp.status_code == 200
        assert captured["req"].prompt == "新 prompt"

    def test_failure_isolation(self, client, tmp_path):
        """重拍失败：快照保持原产物 URL，其余镜头不动。"""
        path = _write_snapshot(tmp_path)

        async def fake_fail(req, progress_callback=None, worker_url=None):
            return AgentResponse(success=False, data=None, error="H3 离线", elapsed_seconds=1.0)

        with patch("app.routers.drama.video_agent.execute", new=AsyncMock(side_effect=fake_fail)):
            resp = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 1},
            )
        assert resp.status_code == 200  # 业务失败走 success=false，不是 5xx
        assert resp.json()["success"] is False
        snapshot = PipelineOrchestrator.load_shot_params("pipeline-test")
        shot1 = next(s for s in snapshot["shots"] if s["scene_id"] == 1)
        assert shot1["video_url"] == "http://x/v1.mp4"  # 原产物保留
        assert "rerun_at" not in shot1

    def test_snapshot_without_seed_reruns_with_none(self, client, tmp_path):
        """快照 seed=None 且请求未覆盖 → req.seed 保持 None（后端随机）。"""
        _write_snapshot(tmp_path)
        captured: dict = {}

        async def fake_execute(req, progress_callback=None, worker_url=None):
            captured["req"] = req
            return AgentResponse(success=True, data={"scene_id": 2, "video_url": "http://x/v2b.mp4"}, elapsed_seconds=1.0)

        with patch("app.routers.drama.video_agent.execute", new=AsyncMock(side_effect=fake_execute)):
            resp = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 2},
            )
        assert resp.status_code == 200
        assert captured["req"].seed is None
        assert captured["req"].engine == "ltx"

    def test_reseed_forces_none_despite_snapshot_seed(self, client, tmp_path):
        """reseed=True：快照 seed=42 也被强制置 None（换 seed 重拍）。"""
        _write_snapshot(tmp_path)
        captured: dict = {}

        async def fake_execute(req, progress_callback=None, worker_url=None):
            captured["req"] = req
            return AgentResponse(success=True, data={"scene_id": 1, "video_url": "http://x/v.mp4"}, elapsed_seconds=1.0)

        with patch("app.routers.drama.video_agent.execute", new=AsyncMock(side_effect=fake_execute)):
            resp = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 1, "reseed": True},
            )
        assert resp.status_code == 200
        assert captured["req"].seed is None  # 快照 42 被忽略
