"""视频 Agent 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.video_agent import VideoAgent
from app.models.schemas import AgentResponse, VideoBatchRequest, VideoRequest
from app.config import settings


@pytest.fixture
def agent():
    return VideoAgent()


class TestVideoAgentExecute:
    async def test_success_videos_output(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_get_comfyui_result.return_value = {
            "8": {
                "videos": [
                    {"filename": "video_1.mp4", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = VideoRequest(
            scene_id=1,
            image_url="http://x/sb.png",
            prompt="cinematic",
            duration_seconds=3,
        )
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["scene_id"] == 1
        assert "video_1.mp4" in response.data["video_url"]

    async def test_success_gifs_output(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_get_comfyui_result.return_value = {
            "8": {
                "gifs": [
                    {"filename": "video_1.mp4", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = VideoRequest(
            scene_id=1,
            image_url="http://x/sb.png",
            prompt="cinematic",
            duration_seconds=3,
        )
        response = await agent.execute(request)

        assert response.success is True
        assert "video_1.mp4" in response.data["video_url"]

    async def test_success_images_output(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_get_comfyui_result.return_value = {
            "8": {
                "images": [
                    {"filename": "video_1.png", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = VideoRequest(
            scene_id=1,
            image_url="http://x/sb.png",
            prompt="cinematic",
            duration_seconds=3,
        )
        response = await agent.execute(request)

        assert response.success is True
        assert "video_1.png" in response.data["video_url"]

    async def test_no_output_returns_error(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        mock_get_comfyui_result.return_value = {"8": {}}

        request = VideoRequest(
            scene_id=1,
            image_url="http://x/sb.png",
            prompt="cinematic",
            duration_seconds=3,
        )
        response = await agent.execute(request)

        assert response.success is False

    async def test_exception_returns_error(self, agent, mock_upload_image):
        mock_upload_image.side_effect = RuntimeError("上传失败")

        request = VideoRequest(scene_id=1, image_url="http://x/sb.png")
        response = await agent.execute(request)

        assert response.success is False
        assert "上传失败" in response.error


class TestVideoAgentBatchExecute:
    """P3.1: 批量并行生成 + 故障转移测试。"""

    def _make_request(self, n: int = 2) -> VideoBatchRequest:
        return VideoBatchRequest(
            items=[
                VideoRequest(scene_id=i, image_url="http://x/sb.png", prompt="p")
                for i in range(1, n + 1)
            ]
        )

    def _ok_resp(self, scene_id: int) -> AgentResponse:
        return AgentResponse(
            success=True,
            data={
                "scene_id": scene_id,
                "video_url": f"http://x/v{scene_id}.mp4",
                "duration_seconds": 3,
            },
            elapsed_seconds=0.01,
        )

    def _fail_resp(self, err: str = "boom") -> AgentResponse:
        return AgentResponse(success=False, error=err, elapsed_seconds=0.01)

    async def test_batch_success(
        self, agent, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        """两个场景全部成功，并发上报进度。"""
        mock_get_comfyui_result.return_value = {
            "8": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }
        with patch.object(
            agent, "get_available_video_workers", new_callable=AsyncMock
        ) as mw:
            mw.return_value = ["http://w-a", "http://w-b"]
            progresses: list[tuple[int, str]] = []
            resp = await agent.batch_execute(
                self._make_request(2),
                progress_callback=lambda p, m: progresses.append((p, m)),
            )

        assert resp.success is True
        assert len(resp.data["results"]) == 2
        assert resp.data["failed_scenes"] == []
        # 进度被多次上报，最终至少出现过 100 或批完成提示
        assert any(p == 100 for p, _ in progresses) or any(
            "批量完成" in m for _, m in progresses
        )

    async def test_batch_failover_succeeds(self, agent):
        """首次失败，故障转移到备用 worker 后成功。"""
        # execute 第一次返回失败，第二次返回成功
        with patch.object(
            agent, "get_available_video_workers", new_callable=AsyncMock
        ) as mw, patch.object(
            agent, "_pick_alternate_worker", new_callable=AsyncMock
        ) as mp, patch.object(
            agent, "execute", new_callable=AsyncMock
        ) as me:
            mw.return_value = ["http://w-a"]
            mp.return_value = "http://w-b"
            me.side_effect = [self._fail_resp(), self._ok_resp(1)]
            resp = await agent.batch_execute(self._make_request(1))

        assert resp.success is True
        assert len(resp.data["results"]) == 1
        assert resp.data["failed_scenes"] == []
        # 故障转移被调用过
        mp.assert_awaited_once_with("http://w-a")
        # execute 被调用两次（首次 + 重试）
        assert me.await_count == 2

    async def test_batch_failover_still_fails(self, agent):
        """首次失败 + 故障转移后仍失败 → 进入 failed_scenes。"""
        with patch.object(
            agent, "get_available_video_workers", new_callable=AsyncMock
        ) as mw, patch.object(
            agent, "_pick_alternate_worker", new_callable=AsyncMock
        ) as mp, patch.object(
            agent, "execute", new_callable=AsyncMock
        ) as me:
            mw.return_value = ["http://w-a"]
            mp.return_value = "http://w-b"
            me.side_effect = [self._fail_resp("err1"), self._fail_resp("err2")]
            resp = await agent.batch_execute(self._make_request(1))

        assert resp.success is True  # 批量整体仍返回 success，failed_scenes 记录失败
        assert resp.data["results"] == []
        assert resp.data["failed_scenes"] == [1]

    async def test_batch_no_alternate_worker(self, agent):
        """_pick_alternate_worker 返回 None（video_a == video_b）→ 不重试直接记失败。"""
        with patch.object(
            agent, "get_available_video_workers", new_callable=AsyncMock
        ) as mw, patch.object(
            agent, "_pick_alternate_worker", new_callable=AsyncMock
        ) as mp, patch.object(
            agent, "execute", new_callable=AsyncMock
        ) as me:
            mw.return_value = ["http://w-a"]
            mp.return_value = None
            me.return_value = self._fail_resp()
            resp = await agent.batch_execute(self._make_request(1))

        assert resp.data["failed_scenes"] == [1]
        # 没有备用 worker → execute 只被调用一次
        assert me.await_count == 1

    async def test_batch_concurrency_respected(self, agent):
        """video_max_concurrency=1 时，并发被信号量限制为串行。"""
        import asyncio

        order: list[str] = []
        active = 0
        peak = 0

        async def fake_execute(item, progress_callback=None, worker_url=None):
            nonlocal active, peak
            order.append(f"start {item.scene_id}")
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return self._ok_resp(item.scene_id)

        with patch.object(
            agent, "get_available_video_workers", new_callable=AsyncMock
        ) as mw, patch.object(
            settings, "video_max_concurrency", 1
        ), patch.object(
            agent, "execute", new=fake_execute
        ):
            mw.return_value = ["http://w-a"] * 3
            resp = await agent.batch_execute(self._make_request(3))

        assert resp.success is True
        # 并发度被限制为 1，峰值不超过 1
        assert peak == 1


class TestPickAlternateWorker:
    """P3.1: 故障转移 worker 选择逻辑。"""

    async def test_returns_different_worker(self, agent):
        """排除已失败 URL，从剩余候选中按负载选一个。"""
        with patch.object(
            agent, "_get_worker_loads", new_callable=AsyncMock
        ) as ml:
            ml.return_value = {"http://localhost:9004": 8000.0}
            result = await agent._pick_alternate_worker("http://localhost:9003")
        assert result == "http://localhost:9004"

    async def test_returns_none_when_all_same(self, agent):
        """video_a == video_b（同一 LB URL）→ alternates 为空，返回 None。"""
        # 临时把两个 worker 改成相同值
        with patch.object(settings, "comfyui_video_a", "http://lb"), patch.object(
            settings, "comfyui_video_b", "http://lb"
        ):
            result = await agent._pick_alternate_worker("http://lb")
        assert result is None

    async def test_fallback_when_loads_unavailable(self, agent):
        """_get_worker_loads 返回空 → 回退到第一个备用候选。"""
        with patch.object(
            agent, "_get_worker_loads", new_callable=AsyncMock
        ) as ml:
            ml.return_value = {}
            result = await agent._pick_alternate_worker("http://localhost:9003")
        # video_a=9003 失败 → 返回 video_b=9004
        assert result == "http://localhost:9004"


class TestVideoAgentXDiT:
    """P4.1: xDiT 后端为主 + ComfyUI 回退测试。

    conftest._patch_settings 默认将 video_backend 设为 'comfyui'，
    本测试类通过 monkeypatch 局部覆盖为 'xdit' 以测试 xDiT 路径。
    """

    def _attach_mock_xdit(self, agent, return_value=None, side_effect=None):
        """将 mock XDiTService 注入 agent._xdit，绕过懒加载 property。"""
        mock_svc = MagicMock()
        mock_svc.generate_video = AsyncMock()
        if side_effect is not None:
            mock_svc.generate_video.side_effect = side_effect
        else:
            mock_svc.generate_video.return_value = return_value or {
                "video_url": "http://xdit/out.mp4",
                "duration_seconds": 4,
                "task_id": "task-x",
            }
        agent._xdit = mock_svc
        return mock_svc

    async def test_xdit_success(self, agent, monkeypatch):
        """video_backend='xdit' → 直接走 XDiT 并返回成功。"""
        monkeypatch.setattr(settings, "video_backend", "xdit")
        self._attach_mock_xdit(agent)

        request = VideoRequest(
            scene_id=1, image_url="http://x/img.png", prompt="cinematic"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["scene_id"] == 1
        assert resp.data["video_url"] == "http://xdit/out.mp4"
        assert resp.data["duration_seconds"] == 4

    async def test_xdit_failure_fallback_to_comfyui_success(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """xDiT 抛异常 → 自动回退到 ComfyUI/Wan 2.2 并成功。"""
        monkeypatch.setattr(settings, "video_backend", "xdit")
        # xDiT 失败
        self._attach_mock_xdit(
            agent, side_effect=RuntimeError("xDiT OOM")
        )
        # ComfyUI 路径返回视频
        mock_get_comfyui_result.return_value = {
            "8": {"videos": [{"filename": "fb.mp4", "subfolder": "", "type": "output"}]}
        }

        request = VideoRequest(
            scene_id=2, image_url="http://x/img.png", prompt="cinematic"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert "fb.mp4" in resp.data["video_url"]
        # 验证回退日志被记录（通过 logger.warning 调用）
        # xdit_service.generate_video 被调用过一次
        assert agent._xdit.generate_video.await_count == 1

    async def test_xdit_and_comfyui_both_fail(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """xDiT 与 ComfyUI 均失败 → 返回失败，error 包含两侧错误。"""
        monkeypatch.setattr(settings, "video_backend", "xdit")
        self._attach_mock_xdit(agent, side_effect=RuntimeError("xdit boom"))
        # ComfyUI 也失败
        mock_get_comfyui_result.side_effect = TimeoutError("comfyui timeout")

        request = VideoRequest(
            scene_id=3, image_url="http://x/img.png", prompt="cinematic"
        )
        resp = await agent.execute(request)

        assert resp.success is False
        assert "xdit boom" in resp.error
        assert "comfyui timeout" in resp.error

    async def test_comfyui_backend_skips_xdit(
        self,
        agent,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """video_backend='comfyui'（conftest 默认）→ 不调用 xDiT，直接走 ComfyUI。"""
        # 即使注入 mock xdit，也不应被调用
        mock_svc = MagicMock()
        mock_svc.generate_video = AsyncMock()
        agent._xdit = mock_svc

        mock_get_comfyui_result.return_value = {
            "8": {"videos": [{"filename": "cf.mp4", "subfolder": "", "type": "output"}]}
        }
        request = VideoRequest(
            scene_id=4, image_url="http://x/img.png", prompt="cinematic"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert "cf.mp4" in resp.data["video_url"]
        # xDiT 未被调用
        mock_svc.generate_video.assert_not_awaited()

    async def test_xdit_progress_callback_propagated(self, agent, monkeypatch):
        """xDiT 路径下，progress_callback 透传到 XDiTService.generate_video。"""
        monkeypatch.setattr(settings, "video_backend", "xdit")

        captured: list[tuple[int, str]] = []

        def fake_generate(*, image_url, prompt, negative_prompt, scene_id,
                          duration_seconds, progress_callback):
            # 模拟 xDiT 内部上报进度
            if progress_callback:
                progress_callback(5, "uploading")
                progress_callback(50, "denoising")
                progress_callback(100, "done")
            captured.extend([(5, "uploading"), (50, "denoising"), (100, "done")])
            return {"video_url": "http://x/v.mp4", "duration_seconds": 4,
                    "task_id": "t"}

        mock_svc = MagicMock()
        mock_svc.generate_video = AsyncMock(side_effect=fake_generate)
        agent._xdit = mock_svc

        progresses: list[tuple[int, str]] = []
        request = VideoRequest(
            scene_id=5, image_url="http://x/img.png", prompt="cinematic"
        )
        resp = await agent.execute(
            request, progress_callback=lambda p, m: progresses.append((p, m))
        )

        assert resp.success is True
        # VideoAgent.execute 入口先上报 5（xDiT/HunyuanVideo），随后透传 xDiT 内部进度
        assert progresses[0] == (5, "xDiT/HunyuanVideo 1.5 多卡并行推理")
        assert (50, "denoising") in progresses
        assert progresses[-1] == (100, "done")
