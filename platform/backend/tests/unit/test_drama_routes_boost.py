"""drama 路由补盲测试 —— 覆盖 integration/test_drama.py 未触及的端点与分支。

覆盖目标：
- /gateway/* 四个端点（capabilities/health/refresh/metrics）
- /agent/assist（成功/未闭合 think 回退/代码块剥离/异常）
- /models/registry（成功/异常）
- /character/preview
- /character-library CRUD（含 404 分支）
- /assets/resolve-mentions 500 分支
- /storyboard/generate_batch、/video/generate_batch
- /quality/apply_subtitle_fix（成功/异常）
- /pipeline/run、/pipeline/status、/pipeline/cancel
- /{agent}/generate_async 非 video 分支 + 成功/失败进度回写
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.progress import progress_tracker
from app.main import app
from app.models.schemas import AgentResponse


@pytest.fixture
def client():
    """返回已配置好的 TestClient。"""
    return TestClient(app)


def _make_llm_client(content: str | Exception) -> MagicMock:
    """构造 get_shared_llm_client 返回值：chat.completions.create 异步返回固定内容。"""
    client_mock = MagicMock()
    if isinstance(content, Exception):
        client_mock.chat.completions.create = AsyncMock(side_effect=content)
    else:
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        client_mock.chat.completions.create = AsyncMock(return_value=resp)
    return client_mock


class TestGatewayRoutes:
    def test_capabilities(self, client):
        report = {"llm": {"endpoint": "http://x", "healthy": True}}
        with patch(
            "app.routers.drama.model_gateway.capabilities_report",
            return_value=report,
        ):
            response = client.get("/api/drama/gateway/capabilities")
        assert response.status_code == 200
        assert response.json()["capabilities"] == report

    def test_health(self, client):
        report = {"llm": {"healthy": True, "latency_ms": 1}}
        with patch(
            "app.routers.drama.model_gateway.health_report",
            new_callable=AsyncMock,
            return_value=report,
        ):
            response = client.get("/api/drama/gateway/health")
        assert response.status_code == 200
        assert response.json() == report

    def test_health_refresh(self, client):
        report = {"llm": {"healthy": True}}
        with (
            patch("app.routers.drama.model_gateway.invalidate_health_cache") as mock_invalidate,
            patch(
                "app.routers.drama.model_gateway.health_report",
                new_callable=AsyncMock,
                return_value=report,
            ),
        ):
            response = client.post("/api/drama/gateway/health/refresh")
        assert response.status_code == 200
        assert response.json() == report
        mock_invalidate.assert_called_once()

    def test_metrics(self, client):
        report = {"llm": {"calls": 3, "errors": 0}}
        with patch(
            "app.routers.drama.model_gateway.metrics_report",
            return_value=report,
        ):
            response = client.get("/api/drama/gateway/metrics")
        assert response.status_code == 200
        assert response.json() == report


class TestAgentAssist:
    payload = {
        "text": "他走进雨里。",
        "context": "script",
        "action": "polish",
        "extra_instruction": "更有画面感",
    }

    def test_success(self, client):
        llm_client = _make_llm_client("他独自走进瓢泼大雨。")
        with patch("app.agents.base.get_shared_llm_client", return_value=llm_client):
            response = client.post("/api/drama/agent/assist", json=self.payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["text"] == "他独自走进瓢泼大雨。"
        assert data["data"]["action"] == "polish"
        assert data["data"]["context"] == "script"
        llm_client.chat.completions.create.assert_awaited_once()

    def test_unclosed_think_falls_back_to_original(self, client):
        """思维链未闭合（无 </think>）时无有效输出，回退原文。"""
        llm_client = _make_llm_client("<think>还没想完就被截断")
        with patch("app.agents.base.get_shared_llm_client", return_value=llm_client):
            response = client.post("/api/drama/agent/assist", json=self.payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["text"] == self.payload["text"]

    def test_code_block_stripped(self, client):
        """LLM 输出包裹 Markdown 代码块时剥离围栏。"""
        llm_client = _make_llm_client("```\n处理后的文本\n```")
        with patch("app.agents.base.get_shared_llm_client", return_value=llm_client):
            response = client.post("/api/drama/agent/assist", json=self.payload)
        assert response.status_code == 200
        assert response.json()["data"]["text"] == "处理后的文本"

    def test_llm_error_returns_failure(self, client):
        llm_client = _make_llm_client(RuntimeError("LLM 不可达"))
        with patch("app.agents.base.get_shared_llm_client", return_value=llm_client):
            response = client.post("/api/drama/agent/assist", json=self.payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "智能体辅助失败" in data["error"]
        assert "LLM 不可达" in data["error"]


class TestModelsRegistry:
    def test_success(self, client):
        registry = {"loras": [{"filename": "a.safetensors", "downloaded": True}]}
        with patch(
            "app.services.model_registry_service.model_registry_service.get_registry",
            return_value=registry,
        ):
            response = client.get("/api/drama/models/registry")
        assert response.status_code == 200
        assert response.json() == registry

    def test_error_returns_500(self, client):
        with patch(
            "app.services.model_registry_service.model_registry_service.get_registry",
            side_effect=RuntimeError("manifest 损坏"),
        ):
            response = client.get("/api/drama/models/registry")
        assert response.status_code == 500
        assert "模型注册表失败" in response.json()["detail"]


class TestCharacterPreview:
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
        preview_data = {"character_id": "char_001", "prompts": {"front": "p1"}}
        with patch(
            "app.routers.drama.character_agent.preview",
            new_callable=AsyncMock,
            return_value=AgentResponse(success=True, data=preview_data, elapsed_seconds=0.5),
        ):
            response = client.post("/api/drama/character/preview", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["character_id"] == "char_001"


class TestCharacterLibrary:
    def _asset(self, character_id: str = "char_001") -> MagicMock:
        asset = MagicMock()
        asset.model_dump.return_value = {"character_id": character_id, "name": "林远"}
        return asset

    def test_list(self, client):
        with patch(
            "app.routers.drama.character_library.list",
            return_value=[self._asset()],
        ):
            response = client.get("/api/drama/character-library/list")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["total"] == 1
        assert data["data"][0]["character_id"] == "char_001"

    def test_get_found(self, client):
        with patch(
            "app.routers.drama.character_library.get",
            return_value=self._asset(),
        ):
            response = client.get("/api/drama/character-library/char_001")
        assert response.status_code == 200
        assert response.json()["data"]["character_id"] == "char_001"

    def test_get_not_found(self, client):
        with patch("app.routers.drama.character_library.get", return_value=None):
            response = client.get("/api/drama/character-library/missing")
        assert response.status_code == 404
        assert "角色资产不存在" in response.json()["detail"]

    def test_update_found(self, client):
        with patch(
            "app.routers.drama.character_library.update",
            return_value=self._asset(),
        ) as mock_update:
            response = client.put(
                "/api/drama/character-library/char_001",
                json={"appearance_lock": "黄色外卖服，寸头"},
            )
        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_update.assert_called_once_with("char_001", appearance_lock="黄色外卖服，寸头")

    def test_update_not_found(self, client):
        with patch("app.routers.drama.character_library.update", return_value=None):
            response = client.put(
                "/api/drama/character-library/missing",
                json={"locked": False},
            )
        assert response.status_code == 404

    def test_delete_ok(self, client):
        with patch("app.routers.drama.character_library.delete", return_value=True):
            response = client.delete("/api/drama/character-library/char_001")
        assert response.status_code == 200
        assert response.json()["data"]["deleted"] == "char_001"

    def test_delete_not_found(self, client):
        with patch("app.routers.drama.character_library.delete", return_value=False):
            response = client.delete("/api/drama/character-library/missing")
        assert response.status_code == 404


class TestResolveMentions:
    def test_success(self, client):
        resolved = {"mentions": [{"mention": "林远", "matched": True}], "expanded_text": "林远"}
        with patch(
            "app.services.mention_service.resolve_mentions",
            return_value=resolved,
        ):
            response = client.post("/api/drama/assets/resolve-mentions", json={"text": "@林远"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == resolved

    def test_value_error_returns_400(self, client):
        with patch(
            "app.services.mention_service.resolve_mentions",
            side_effect=ValueError("提及数量超限"),
        ):
            response = client.post("/api/drama/assets/resolve-mentions", json={"text": "@林远"})
        assert response.status_code == 400
        assert "提及数量超限" in response.json()["detail"]

    def test_internal_error_returns_500(self, client):
        with patch(
            "app.services.mention_service.resolve_mentions",
            side_effect=RuntimeError("资产库读取失败"),
        ):
            response = client.post("/api/drama/assets/resolve-mentions", json={"text": "@林远"})
        assert response.status_code == 500
        assert "@提及解析失败" in response.json()["detail"]


class TestFailureModes:
    def test_list(self, client):
        mode = MagicMock()
        mode.model_dump.return_value = {"code": "FM-001", "layer": "video"}
        with (
            patch("app.routers.drama.failure_registry.list_active", return_value=[mode]),
            patch("app.routers.drama.failure_registry.hits", return_value={"FM-001": 2}),
        ):
            response = client.get("/api/drama/verification/failure-modes?layer=video&gate_only=true")
        assert response.status_code == 200
        data = response.json()
        assert data["modes"] == [{"code": "FM-001", "layer": "video"}]
        assert data["hits"] == {"FM-001": 2}

    def test_hit_ok(self, client):
        with (
            patch("app.routers.drama.failure_registry.get", return_value=MagicMock()),
            patch("app.routers.drama.failure_registry.bump_hit", return_value=3),
        ):
            response = client.post("/api/drama/verification/failure-modes/FM-001/hit")
        assert response.status_code == 200
        assert response.json() == {"code": "FM-001", "hit_count": 3}

    def test_hit_not_found(self, client):
        with patch("app.routers.drama.failure_registry.get", return_value=None):
            response = client.post("/api/drama/verification/failure-modes/FM-X/hit")
        assert response.status_code == 404
        assert "未注册的失败模式" in response.json()["detail"]

    def test_upsert_ok(self, client):
        mode = MagicMock()
        mode.model_dump.return_value = {"code": "FM-002", "layer": "subtitle"}
        with patch("app.routers.drama.failure_registry.upsert", return_value=mode):
            response = client.put(
                "/api/drama/verification/failure-modes/FM-002",
                json={"layer": "subtitle"},
            )
        assert response.status_code == 200
        assert response.json()["code"] == "FM-002"

    def test_upsert_invalid_field_returns_422(self, client):
        with patch(
            "app.routers.drama.failure_registry.upsert",
            side_effect=ValueError("非白名单字段: foo"),
        ):
            response = client.put(
                "/api/drama/verification/failure-modes/FM-002",
                json={"foo": "bar"},
            )
        assert response.status_code == 422
        assert "非白名单字段" in response.json()["detail"]


class TestRagRoutes:
    def test_optimize_success(self, client):
        result = {
            "optimized_positive": "cinematic shot",
            "optimized_negative": "blurry",
            "style_notes": "写实",
            "tags": ["video"],
            "lora_recommendations": [],
            "original_prompt": "雨夜街头",
            "retrieved_count": 2,
            "fallback": False,
        }
        with patch(
            "app.routers.drama.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            return_value=result,
        ):
            response = client.post(
                "/api/drama/rag/optimize",
                json={"user_prompt": "雨夜街头", "domain": "video"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["optimized_positive"] == "cinematic shot"
        assert data["retrieved_count"] == 2

    def test_optimize_error_returns_500(self, client):
        with patch(
            "app.routers.drama.rag_service.optimize_prompt",
            new_callable=AsyncMock,
            side_effect=RuntimeError("检索服务不可用"),
        ):
            response = client.post("/api/drama/rag/optimize", json={"user_prompt": "雨夜街头"})
        assert response.status_code == 500
        assert "RAG 优化失败" in response.json()["detail"]

    def test_styles_success(self, client):
        styles = [{"key": "realistic", "name": "写实电影感"}]
        with patch("app.routers.drama.rag_service.get_styles", return_value=styles):
            response = client.get("/api/drama/rag/styles")
        assert response.status_code == 200
        assert response.json() == styles

    def test_styles_error_returns_500(self, client):
        with patch(
            "app.routers.drama.rag_service.get_styles",
            side_effect=RuntimeError("风格库损坏"),
        ):
            response = client.get("/api/drama/rag/styles")
        assert response.status_code == 500
        assert "RAG 风格列表失败" in response.json()["detail"]


class TestRerunVideoShot:
    """POST /video/rerun-shot 单镜头锚点重拍（路由层，快照/执行全部 mock）。"""

    snapshot = {
        "project_id": "pipeline-test",
        "shots": [
            {
                "scene_id": 1,
                "image_url": "http://x/kf1.png",
                "prompt": "镜头1 prompt",
                "duration_seconds": 5,
                "engine": "h3",
                "seed": 42,
                "status": "success",
                "video_url": "http://x/v1.mp4",
            }
        ],
    }

    def test_snapshot_missing_returns_404(self, client):
        with patch(
            "app.routers.drama.PipelineOrchestrator.load_shot_params",
            return_value=None,
        ):
            response = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "nope", "scene_id": 1},
            )
        assert response.status_code == 404
        assert "镜头参数快照不存在" in response.json()["detail"]

    def test_shot_missing_returns_404(self, client):
        with patch(
            "app.routers.drama.PipelineOrchestrator.load_shot_params",
            return_value=self.snapshot,
        ):
            response = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 99},
            )
        assert response.status_code == 404
        assert "快照中无镜头" in response.json()["detail"]

    def test_success_with_seed_and_override_prompt(self, client):
        with (
            patch(
                "app.routers.drama.PipelineOrchestrator.load_shot_params",
                return_value=self.snapshot,
            ),
            patch(
                "app.routers.drama.video_agent.execute",
                new_callable=AsyncMock,
                return_value=AgentResponse(
                    success=True, data={"video_url": "http://x/v1-new.mp4"}, elapsed_seconds=2.0
                ),
            ) as mock_execute,
            patch(
                "app.routers.drama.PipelineOrchestrator.update_shot_result",
                return_value=True,
            ) as mock_update,
        ):
            response = client.post(
                "/api/drama/video/rerun-shot",
                json={
                    "project_id": "pipeline-test",
                    "scene_id": 1,
                    "seed": 99,
                    "override_prompt": "  新提示词  ",
                },
            )
        assert response.status_code == 200
        assert response.json()["success"] is True
        # 快照落盘字段被剔除、seed/prompt 被覆盖
        req = mock_execute.await_args.args[0]
        assert req.seed == 99
        assert req.prompt == "新提示词"
        mock_update.assert_called_once_with(
            "pipeline-test", 1, video_url="http://x/v1-new.mp4", status="success", seed_used=99
        )

    def test_reseed_forces_random_and_failure_skips_writeback(self, client):
        with (
            patch(
                "app.routers.drama.PipelineOrchestrator.load_shot_params",
                return_value=self.snapshot,
            ),
            patch(
                "app.routers.drama.video_agent.execute",
                new_callable=AsyncMock,
                return_value=AgentResponse(success=False, error="生成超时", elapsed_seconds=1.0),
            ) as mock_execute,
            patch(
                "app.routers.drama.PipelineOrchestrator.update_shot_result",
                return_value=True,
            ) as mock_update,
        ):
            response = client.post(
                "/api/drama/video/rerun-shot",
                json={"project_id": "pipeline-test", "scene_id": 1, "reseed": True},
            )
        assert response.status_code == 200
        assert response.json()["success"] is False
        # reseed=True 时快照 seed 被置 None
        assert mock_execute.await_args.args[0].seed is None
        # 失败隔离：不回写快照
        mock_update.assert_not_called()


class TestStoryboardBatch:
    def test_success(self, client):
        payload = {
            "scenes": [
                {"scene_id": 1, "description": "开场", "prompt": "close-up"},
                {"scene_id": 2, "description": "追逐", "prompt": "wide shot"},
            ],
            "characters": [{"character_id": "char_001", "name": "林远"}],
        }
        with patch(
            "app.routers.drama.storyboard_agent.batch_execute",
            new_callable=AsyncMock,
            return_value=AgentResponse(
                success=True,
                data={"results": [{"scene_id": 1}, {"scene_id": 2}], "failed_scenes": []},
                elapsed_seconds=2.0,
            ),
        ):
            response = client.post("/api/drama/storyboard/generate_batch", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["results"]) == 2


class TestVideoBatch:
    def test_success(self, client):
        payload = {
            "items": [
                {
                    "scene_id": 1,
                    "image_url": "http://x/s1.png",
                    "prompt": "a man",
                    "duration_seconds": 3,
                },
                {
                    "scene_id": 2,
                    "image_url": "http://x/s2.png",
                    "prompt": "a street",
                    "duration_seconds": 3,
                },
            ]
        }
        with patch(
            "app.routers.drama.video_agent.batch_execute",
            new_callable=AsyncMock,
            return_value=AgentResponse(
                success=True,
                data={"results": [{"scene_id": 1, "video_url": "http://x/v1.mp4"}]},
                elapsed_seconds=3.0,
            ),
        ):
            response = client.post("/api/drama/video/generate_batch", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True


class TestApplySubtitleFix:
    payload = {
        "subtitles": [{"scene_id": 1, "srt_content": "1\n00:00:00,000 --> 00:00:01,000\n你好"}],
        "issues": [
            {"category": "subtitle", "severity": "warning", "scene_id": 1, "message": "错别字"}
        ],
        "persist": False,
    }

    def test_success(self, client):
        result = MagicMock()
        result.model_dump.return_value = {"fixed_count": 1, "corrections": []}
        with patch("app.routers.drama.apply_subtitle_fixes", return_value=result):
            response = client.post("/api/drama/quality/apply_subtitle_fix", json=self.payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["fixed_count"] == 1

    def test_error_returns_failure(self, client):
        with patch(
            "app.routers.drama.apply_subtitle_fixes",
            side_effect=RuntimeError("SRT 文件只读"),
        ):
            response = client.post("/api/drama/quality/apply_subtitle_fix", json=self.payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "字幕回写修正失败" in data["error"]


class TestPipelineRoutes:
    def test_run(self, client):
        with patch(
            "app.routers.drama.pipeline_orchestrator.start",
            return_value="task-abc",
        ):
            response = client.post(
                "/api/drama/pipeline/run",
                json={"premise": "外卖员收到最后一单", "episodes": 1},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-abc"
        assert data["agent"] == "pipeline"
        assert data["status"] == "pending"
        assert data["poll_url"].endswith("/api/progress/task-abc")
        assert data["stream_url"].endswith("/api/progress/task-abc/stream")

    def test_status_found(self, client):
        record = SimpleNamespace(
            task_id="task-abc",
            status="running",
            percent=40,
            message="视频生成中",
            error=None,
            result=None,
            updated_at=1234567890.0,
        )
        with patch("app.routers.drama.progress_tracker.get", return_value=record):
            response = client.get("/api/drama/pipeline/status/task-abc")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["task_id"] == "task-abc"
        assert data["status"] == "running"
        assert data["percent"] == 40

    def test_status_not_found(self, client):
        with patch("app.routers.drama.progress_tracker.get", return_value=None):
            response = client.get("/api/drama/pipeline/status/missing")
        assert response.status_code == 404
        assert "任务不存在或已过期" in response.json()["detail"]

    def test_cancel_ok(self, client):
        with patch("app.routers.drama.pipeline_orchestrator.cancel", return_value=True):
            response = client.post("/api/drama/pipeline/cancel/task-abc")
        assert response.status_code == 200
        assert response.json()["data"]["cancel_requested"] is True

    def test_cancel_not_found(self, client):
        with patch("app.routers.drama.pipeline_orchestrator.cancel", return_value=False):
            response = client.post("/api/drama/pipeline/cancel/missing")
        assert response.status_code == 404
        assert "任务不存在或已结束" in response.json()["detail"]


class TestAsyncAgentTaskBranches:
    """_run_agent_task 的非 video 分支与成功/失败进度回写。"""

    payload = {
        "premise": "一个外卖员收到最后一单",
        "genre": "都市悬疑",
        "episodes": 1,
        "scenes_per_episode": 3,
    }

    def test_non_video_agent_success(self, client):
        with patch(
            "app.routers.drama.script_agent.execute",
            new_callable=AsyncMock,
            return_value=AgentResponse(
                success=True, data={"title": "最后的订单"}, elapsed_seconds=1.0
            ),
        ):
            response = client.post("/api/drama/script/generate_async", json=self.payload)
        assert response.status_code == 200
        task_id = response.json()["task_id"]
        # TestClient 会在响应返回前跑完 BackgroundTasks
        record = progress_tracker.get(task_id)
        assert record is not None
        assert record.status == "completed"
        assert record.percent == 100
        assert record.result == {"title": "最后的订单"}

    def test_non_video_agent_failure(self, client):
        with patch(
            "app.routers.drama.script_agent.execute",
            new_callable=AsyncMock,
            return_value=AgentResponse(success=False, error="剧本生成失败", elapsed_seconds=1.0),
        ):
            response = client.post("/api/drama/script/generate_async", json=self.payload)
        assert response.status_code == 200
        record = progress_tracker.get(response.json()["task_id"])
        assert record is not None
        assert record.status == "failed"
        assert record.error == "剧本生成失败"
