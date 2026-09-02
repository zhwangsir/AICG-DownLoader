"""视频 Agent 单元测试。"""

from __future__ import annotations

import json
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.video_agent import VideoAgent
from app.models.schemas import AgentResponse, VideoBatchRequest, VideoRequest
from app.config import settings



@pytest.fixture(autouse=True)
def _sfw_pin_off(monkeypatch):
    monkeypatch.setattr(
        "app.services.settings_service.settings_service.nsfw_status",
        lambda: {"nsfw_enabled": False, "has_pin": False},
    )

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


class TestSnapH3Frames:
    """M10: H3 帧数必须对齐 17k+5 网格（24fps），与官方模板公式一致。"""

    def test_five_seconds_is_124(self):
        from app.agents.video_agent import _snap_h3_frames

        assert _snap_h3_frames(5) == 124  # 训练网格经典值（官方 tooltip）

    def test_values_lie_on_grid(self):
        from app.agents.video_agent import _snap_h3_frames

        for sec in (1, 2, 3, 4, 5, 6, 10, 15):
            n = _snap_h3_frames(sec)
            assert n % 17 == 5, f"sec={sec} -> {n} 不在 17k+5 网格上"
            assert n >= 5

    def test_fifteen_seconds_is_trained_max(self):
        from app.agents.video_agent import _snap_h3_frames

        assert _snap_h3_frames(15) == 362  # 官方训练范围上限 362 帧

    def test_fractional_seconds_round_first(self):
        from app.agents.video_agent import _snap_h3_frames

        # 4.5s -> round(108)=108, 108%17=6, (5-6)%17=16 -> 124
        assert _snap_h3_frames(4.5) == 124


class TestVideoAgentH3:
    """M10: MiniMax H3 后端为主 + ComfyUI 回退测试。

    conftest._patch_settings 默认将 video_backend 设为 'comfyui'，
    本测试类通过 monkeypatch 局部覆盖为 'h3' 以测试 H3 路径。
    """

    async def test_h3_success_official_workflow(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """video_backend='h3' → 走 H3 专用实例，工作流对齐官方 i2v 模板。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {
                "videos": [
                    {"filename": "video_scene_7.mp4", "subfolder": "", "type": "output"}
                ]
            }
        }

        request = VideoRequest(
            scene_id=7,
            image_url="http://x/sb.png",
            prompt="cinematic vertical shot",
            duration_seconds=5,
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["scene_id"] == 7
        assert "video_scene_7.mp4" in resp.data["video_url"]
        assert resp.data["duration_seconds"] == 124 // 24

        # 必须直连 H3 专用实例（conftest 占位 http://localhost:9005），而非 LB
        call_url, workflow = mock_call_comfyui.call_args[0]
        assert call_url == "http://localhost:9005"

        # 官方模板关键节点结构
        assert workflow["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert workflow["20"]["inputs"]["prompt"] == "cinematic vertical shot"
        assert workflow["20"]["inputs"]["length"] == 124
        assert workflow["20"]["inputs"]["width"] == settings.h3_width
        assert workflow["20"]["inputs"]["height"] == settings.h3_height
        assert workflow["2"]["inputs"]["type"] == "minimax"
        assert workflow["41"]["class_type"] == "VAEDecodeAudio"  # 原生音频解码
        assert workflow["50"]["class_type"] == "CreateVideo"
        assert workflow["60"]["class_type"] == "SaveVideo"
        # 蒸馏单条件模型：不得出现负面提示词编码节点
        class_types = {n["class_type"] for n in workflow.values()}
        assert "CLIPTextEncode" not in class_types

    async def test_h3_ignores_lb_worker_url(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """H3 模型只部署在专用实例：即使传入 LB worker 也必须直连 h3_comfyui_url。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }

        request = VideoRequest(scene_id=8, image_url="http://x/sb.png", prompt="p")
        resp = await agent.execute(request, worker_url="http://localhost:9003")

        assert resp.success is True
        assert mock_call_comfyui.call_args[0][0] == "http://localhost:9005"
        # 上传也必须走 H3 实例
        assert mock_upload_image.call_args[0][0] == "http://localhost:9005"

    async def test_h3_failure_fallback_to_comfyui_success(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """H3 抛异常 → 自动回退到 ComfyUI/Wan 2.2 并成功。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        # 有角色参考时 H3 先 r2v 再 fl2va，两次上传均失败后才回退 Wan
        mock_upload_image.side_effect = [
            RuntimeError("h3 OOM"),
            RuntimeError("h3 OOM"),
            "img.png",
        ]
        mock_get_comfyui_result.return_value = {
            "8": {"videos": [{"filename": "fb.mp4", "subfolder": "", "type": "output"}]}
        }

        # P2：无角色空镜不再回退 Wan；本用例带参考图以覆盖「有角色 → Wan 回退」契约
        request = VideoRequest(
            scene_id=9,
            image_url="http://x/sb.png",
            prompt="p",
            reference_images=["http://x/char.png"],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert "fb.mp4" in resp.data["video_url"]
        # 回退路径走 LB worker（r2v 失败 + fl2va 失败 + Wan 成功）
        assert mock_upload_image.await_count == 3

    async def test_h3_and_comfyui_both_fail(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
    ):
        """H3 与 ComfyUI 均失败 → 返回失败，error 包含两侧错误。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_upload_image.side_effect = RuntimeError("always down")

        request = VideoRequest(
            scene_id=10,
            image_url="http://x/sb.png",
            prompt="p",
            reference_images=["http://x/char.png"],
        )
        resp = await agent.execute(request)

        assert resp.success is False
        assert "h3=always down" in resp.error
        assert "comfyui=always down" in resp.error


class TestVideoAgentH3R2V:
    """M10+: MiniMax H3 ref2va 角色一致性路径测试。

    VideoRequest.reference_images 非空 → MiniMaxH3ReferenceToVideo（r2v），
    分镜关键帧作为第 1 张参考图（构图参考），角色三视图参考图随后；
    为空 → 维持 fl2va（MiniMaxH3ImageToVideo）首帧图生视频路径。
    conftest._patch_settings 默认 video_backend='comfyui'，本类局部覆盖为 'h3'。
    """

    @staticmethod
    def _video_outputs():
        return {
            "60": {
                "videos": [
                    {"filename": "video_scene_7.mp4", "subfolder": "", "type": "output"}
                ]
            }
        }

    async def test_r2v_triggered_by_reference_images(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """reference_images 非空 → r2v 节点 + ref2va 模型 + audio_vae 连接 + 无 first_frame。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=7,
            image_url="http://x/sb.png",
            prompt="cinematic vertical shot",
            duration_seconds=5,
            reference_images=["http://x/char_front.png", "http://x/char_side.png"],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["scene_id"] == 7
        assert "video_scene_7.mp4" in resp.data["video_url"]
        assert resp.data["duration_seconds"] == 124 // 24

        # 必须直连 H3 专用实例（conftest 占位 http://localhost:9005），而非 LB
        call_url, workflow = mock_call_comfyui.call_args[0]
        assert call_url == "http://localhost:9005"

        # r2v 节点结构：无 first_frame、audio_vae 必须接节点 4
        node20 = workflow["20"]
        assert node20["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert "first_frame" not in node20["inputs"]
        assert node20["inputs"]["audio_vae"] == ["4", 0]
        assert node20["inputs"]["ref_image_size"] == settings.h3_ref_image_size
        assert node20["inputs"]["length"] == 124
        assert node20["inputs"]["width"] == settings.h3_width
        assert node20["inputs"]["height"] == settings.h3_height
        # ref2va 模型（区别于 fl2va 的 h3_unet_name）
        assert workflow["1"]["inputs"]["unet_name"] == settings.h3_ref_unet_name
        # prompt 保留原分镜 prompt，并附加参考图用途引导（官方模板实践）
        assert "cinematic vertical shot" in node20["inputs"]["prompt"]
        assert "reference" in node20["inputs"]["prompt"].lower()

        # 参考图接线（COMFY_AUTOGROW_V3 嵌套 dict）：分镜关键帧=ref_image_0，角色参考图按序 ref_image_1/2
        ref_group = node20["inputs"]["ref_images"]
        assert ref_group["ref_image_0"] == ["10", 0]
        assert ref_group["ref_image_1"] == ["11", 0]
        assert ref_group["ref_image_2"] == ["12", 0]
        load_image_ids = sorted(
            nid for nid, n in workflow.items() if n["class_type"] == "LoadImage"
        )
        assert load_image_ids == ["10", "11", "12"]

        # 上传 3 次（关键帧 + 2 参考图），全部走 H3 实例且保持顺序
        assert mock_upload_image.await_count == 3
        uploaded = [c.args for c in mock_upload_image.call_args_list]
        assert uploaded == [
            ("http://localhost:9005", "http://x/sb.png"),
            ("http://localhost:9005", "http://x/char_front.png"),
            ("http://localhost:9005", "http://x/char_side.png"),
        ]

        # 与 fl2va 共用同一音视频解码/合成尾部
        assert workflow["41"]["class_type"] == "VAEDecodeAudio"
        assert workflow["50"]["class_type"] == "CreateVideo"
        assert workflow["60"]["class_type"] == "SaveVideo"

    async def test_empty_reference_images_keeps_fl2va(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """reference_images 为空 → 维持 fl2va i2v 路径（向后兼容）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=1,
            image_url="http://x/sb.png",
            prompt="p",
            reference_images=[],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert workflow["20"]["inputs"]["first_frame"] == ["10", 0]
        assert workflow["1"]["inputs"]["unet_name"] == settings.h3_unet_name
        # fl2va 只上传关键帧 1 次
        assert mock_upload_image.await_count == 1

    async def test_r2v_truncates_to_max_images(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """参考图超过节点上限 → 截断：ref_image 输入总数 = h3_ref_max_images（含关键帧）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()

        refs = [f"http://x/ref_{i}.png" for i in range(12)]
        request = VideoRequest(
            scene_id=2,
            image_url="http://x/sb.png",
            prompt="p",
            reference_images=refs,
        )
        resp = await agent.execute(request)

        assert resp.success is True
        workflow = mock_call_comfyui.call_args[0][1]
        node20_inputs = workflow["20"]["inputs"]
        # AUTOGROW 组为嵌套 dict：ref_images = {ref_image_N: link}
        ref_group = node20_inputs["ref_images"]
        # 1 关键帧 + 8 角色参考图 = 节点上限 9（ref_image_0..ref_image_8）
        assert len(ref_group) == 9
        assert ref_group["ref_image_8"] == ["18", 0]
        assert "ref_image_9" not in ref_group
        # 上传同步截断为 9 次
        assert mock_upload_image.await_count == 9

    async def test_r2v_dedupes_reference_images_preserving_order(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """重复/空 URL 去重且保持原顺序。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = self._video_outputs()

        refs = [
            "http://x/a.png",
            "http://x/b.png",
            "http://x/a.png",
            "",
            "http://x/b.png",
            "http://x/c.png",
        ]
        request = VideoRequest(
            scene_id=3,
            image_url="http://x/sb.png",
            prompt="p",
            reference_images=refs,
        )
        resp = await agent.execute(request)

        assert resp.success is True
        uploaded_urls = [c.args[1] for c in mock_upload_image.call_args_list]
        assert uploaded_urls == [
            "http://x/sb.png",
            "http://x/a.png",
            "http://x/b.png",
            "http://x/c.png",
        ]

    async def test_r2v_failure_falls_back_to_fl2va(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """r2v 提交失败 → 同实例回退 fl2va i2v 并成功。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_call_comfyui.side_effect = [
            RuntimeError("r2v OOM"),
            {"prompt_id": "p-fl2va"},
        ]
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=4,
            image_url="http://x/sb.png",
            prompt="p",
            reference_images=["http://x/a.png"],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert "video_scene_7.mp4" in resp.data["video_url"]
        # 第一次提交 r2v，失败后第二次提交 fl2va
        assert mock_call_comfyui.await_count == 2
        wf_r2v = mock_call_comfyui.call_args_list[0].args[1]
        wf_fl2va = mock_call_comfyui.call_args_list[1].args[1]
        assert wf_r2v["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert wf_fl2va["20"]["class_type"] == "MiniMaxH3ImageToVideo"
        # 两次都直连 H3 专用实例
        assert mock_call_comfyui.call_args_list[1].args[0] == "http://localhost:9005"

    async def test_r2v_and_fl2va_fail_falls_back_to_comfyui(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """r2v 与 fl2va 均失败 → 回退 ComfyUI/Wan 2.2 并成功。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_call_comfyui.side_effect = [
            RuntimeError("r2v boom"),
            RuntimeError("fl2va boom"),
            {"prompt_id": "p-wan"},
        ]
        mock_get_comfyui_result.return_value = {
            "8": {"videos": [{"filename": "fb.mp4", "subfolder": "", "type": "output"}]}
        }

        request = VideoRequest(
            scene_id=5,
            image_url="http://x/sb.png",
            prompt="p",
            reference_images=["http://x/a.png"],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert "fb.mp4" in resp.data["video_url"]
        assert mock_call_comfyui.await_count == 3


# ---------------------------------------------------------------------------
# M18.4: H3 画风漂移治理（约束 + 检测 + 纠偏 三层机制）
# ---------------------------------------------------------------------------


class TestH3StyleAnchorClause:
    """M18.4 约束层（纯函数）：H3 prompt 画风冲突清洗 + 风格锚定尾幂等注入。

    背景：M18.1 帧级核验发现 H3 输出（半写实厚涂）与参考图（卡通平涂）系统性
    脱节——orchestrator 虽追加了 style tail，但剧本 LLM 场景 prompt 残留的
    hyperrealistic / cinematic realism 等冲突词在 H3 侧无任何清洗，风格尾被
    正文冲突信号抵消。约束层在 video Agent 内对 fl2va / r2v / 多镜三条 H3
    路径统一做冲突清洗 + 幂等锚定。
    """

    def test_sanitizes_conflicts_and_appends_tail(self):
        """目标国漫 → 写实冲突词清洗 + 国漫风格尾注入。"""
        from app.agents.video_agent import apply_h3_style_anchor

        out = apply_h3_style_anchor(
            "a girl in a convenience store, hyperrealistic texture, cinematic realism",
            "国漫",
        )
        assert "hyperrealistic" not in out
        assert "cinematic realism" not in out
        assert "convenience store" in out
        assert "Chinese anime guoman style" in out

    def test_idempotent_when_tail_already_present(self):
        """orchestrator 已追加风格尾（M15.1）→ 不得二次追加。"""
        from app.agents.video_agent import apply_h3_style_anchor

        out = apply_h3_style_anchor("a girl, Chinese anime guoman style", "国漫")
        assert out.count("Chinese anime guoman style") == 1

    def test_realistic_style_strips_anime_terms(self):
        """目标写实 → 动漫冲突词清洗 + 写实风格尾注入。"""
        from app.agents.video_agent import apply_h3_style_anchor

        out = apply_h3_style_anchor("a young man, anime style, soft light", "写实电影感")
        assert "anime style" not in out
        assert "soft light" in out
        assert "cinematic realistic" in out

    def test_disabled_passthrough(self, monkeypatch):
        """h3_style_anchor_enabled=False → 原样透传（回滚路径与现状一致）。"""
        monkeypatch.setattr(settings, "h3_style_anchor_enabled", False)
        from app.agents.video_agent import apply_h3_style_anchor

        prompt = "hyperrealistic texture"
        assert apply_h3_style_anchor(prompt, "国漫") == prompt

    def test_empty_style_passthrough(self):
        """style 为空（直连 API 未传画风）→ 跳过锚定，向后兼容。"""
        from app.agents.video_agent import apply_h3_style_anchor

        prompt = "hyperrealistic texture"
        assert apply_h3_style_anchor(prompt, "") == prompt

    def test_strengthen_clause_prepends_style(self):
        """纠偏层：漂移重生成时前置强化画风子句，原 prompt 保留在后。"""
        from app.agents.video_agent import strengthen_h3_style_clause

        out = strengthen_h3_style_clause("a girl in rain", "国漫")
        assert out.startswith("Rendered strictly in Chinese anime guoman style.")
        assert "a girl in rain" in out


class TestVideoAgentH3StyleAnchorWiring:
    """M18.4 约束层（接线）：fl2va / r2v / 多镜三条 H3 prompt 路径统一锚定。"""

    async def test_fl2va_prompt_anchored(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """fl2va：冲突词清洗 + 风格尾进入工作流 prompt。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }

        request = VideoRequest(
            scene_id=21,
            image_url="http://x/sb.png",
            prompt="a girl in a convenience store, hyperrealistic texture",
            style="国漫",
        )
        resp = await agent.execute(request)

        assert resp.success is True
        prompt = mock_call_comfyui.call_args[0][1]["20"]["inputs"]["prompt"]
        assert "hyperrealistic" not in prompt
        assert "convenience store" in prompt
        assert "Chinese anime guoman style" in prompt

    async def test_r2v_prompt_anchored_before_guide(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """r2v：锚定作用于场景 prompt 本体，风格尾位于参考图引导语之前。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
        }

        request = VideoRequest(
            scene_id=22,
            image_url="http://x/sb.png",
            prompt="a girl, cinematic realism",
            style="国漫",
            reference_images=["http://x/char_front.png"],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        prompt = mock_call_comfyui.call_args[0][1]["20"]["inputs"]["prompt"]
        assert "cinematic realism" not in prompt
        assert "Chinese anime guoman style" in prompt
        assert prompt.index("Chinese anime guoman style") < prompt.index("Reference images:")

    def test_build_multishot_prompt_applies_anchor(self):
        """多镜：各镜 prompt 逐镜清洗 + 锚定（同组共享画风）。"""
        from app.agents.video_agent import build_multishot_prompt

        reqs = [
            VideoRequest(
                scene_id=1,
                image_url="http://x/1.png",
                prompt="a girl, hyperrealistic texture",
                style="国漫",
            ),
            VideoRequest(
                scene_id=2,
                image_url="http://x/2.png",
                prompt="rainy street",
                style="国漫",
            ),
        ]
        prompt = build_multishot_prompt(reqs)
        assert "hyperrealistic" not in prompt
        assert "Chinese anime guoman style" in prompt

    def test_build_multishot_prompt_empty_style_unchanged(self):
        """多镜：全部请求无 style → prompt 原样（向后兼容，不清洗不追加）。"""
        from app.agents.video_agent import build_multishot_prompt

        reqs = [
            VideoRequest(scene_id=1, image_url="http://x/1.png", prompt="alpha, hyperrealistic"),
            VideoRequest(scene_id=2, image_url="http://x/2.png", prompt="beta"),
        ]
        prompt = build_multishot_prompt(reqs)
        assert "hyperrealistic" in prompt


def _make_vlm_result(content: str):
    """构造 VLM chat.completions.create 返回值（与角色质检测试同构）。"""
    result = MagicMock()
    result.choices = [MagicMock()]
    result.choices[0].message.content = content
    return result


class TestVideoAgentH3StyleQC:
    """M18.4 检测+纠偏层：H3 产出 VLM 画风漂移检测，漂移换 seed 强化锚定重生成。

    检测：抽取视频中点帧送 VLM 比对目标画风；纠偏：漂移时前置强化画风子句并
    换 seed 重提交（最多 h3_style_qc_max_retries 次），重试耗尽放行最后结果
    （纠偏不阻断生产）；VLM 未配置/异常/坏 JSON 一律 fail-open 放行。
    覆盖单镜 fl2va/r2v 路径；多镜组视频重生成成本高（10-20 分钟/组），
    组级漂移由约束层治理，不做组级 VLM QC。
    conftest._patch_settings 默认 h3_style_qc_enabled=False，本类局部开启。
    """

    QC_PASS = json.dumps({"pass": True, "reason": ""})
    QC_FAIL = json.dumps({"pass": False, "reason": "画面为半写实厚涂而非目标卡通平涂"})

    def _enable_qc(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_style_qc_enabled", True)
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")

    def _attach_vlm(self, agent, contents: list[str]):
        vlm = MagicMock()
        vlm.chat.completions.create = AsyncMock(
            side_effect=[_make_vlm_result(c) for c in contents]
        )
        agent._vlm_client = vlm
        return vlm

    @staticmethod
    def _mock_frame_extract(agent):
        agent._extract_h3_middle_frame = AsyncMock(return_value=b"fake-png-bytes")

    @staticmethod
    def _video_outputs():
        return {
            "60": {
                "videos": [
                    {"filename": "video_scene_7.mp4", "subfolder": "", "type": "output"}
                ]
            }
        }

    async def test_qc_pass_no_retry(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """画风合格 → 单次提交，VLM 调 1 次且请求携带目标画风。"""
        self._enable_qc(monkeypatch)
        self._mock_frame_extract(agent)
        vlm = self._attach_vlm(agent, [self.QC_PASS])
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=31, image_url="http://x/sb.png", prompt="a girl", style="国漫"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 1
        assert vlm.chat.completions.create.await_count == 1
        vlm_text = vlm.chat.completions.create.call_args.kwargs["messages"][0]["content"][0]["text"]
        assert "国漫" in vlm_text
        assert "Chinese anime guoman style" in vlm_text

    async def test_qc_fail_then_pass_retries_once(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """首次漂移 → 强化画风子句 + 换 seed 重提交一次，复检合格采用新结果。"""
        self._enable_qc(monkeypatch)
        self._mock_frame_extract(agent)
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_PASS])
        mock_get_comfyui_result.return_value = self._video_outputs()
        seeds = iter([111, 222])
        monkeypatch.setattr(random, "randint", lambda a, b: next(seeds))

        request = VideoRequest(
            scene_id=32, image_url="http://x/sb.png", prompt="a girl", style="国漫"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 2
        wf1 = mock_call_comfyui.call_args_list[0].args[1]
        wf2 = mock_call_comfyui.call_args_list[1].args[1]
        # 纠偏重提交：换 seed + 前置强化画风子句
        assert wf1["30"]["inputs"]["noise_seed"] == 111
        assert wf2["30"]["inputs"]["noise_seed"] == 222
        assert wf2["20"]["inputs"]["prompt"].startswith(
            "Rendered strictly in Chinese anime guoman style."
        )

    async def test_qc_retry_exhausted_accepts_last_result(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """重试后仍漂移 → 放行最后结果（纠偏不阻断生产），仅日志记录。"""
        self._enable_qc(monkeypatch)
        self._mock_frame_extract(agent)
        vlm = self._attach_vlm(agent, [self.QC_FAIL, self.QC_FAIL])
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=33, image_url="http://x/sb.png", prompt="a girl", style="国漫"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 2
        assert vlm.chat.completions.create.await_count == 2
        assert "video_scene_7.mp4" in resp.data["video_url"]

    async def test_r2v_path_also_style_qc(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """r2v 路径同样过画风 QC：漂移重提交仍走 r2v 工作流且参考图挂接保留。"""
        self._enable_qc(monkeypatch)
        self._mock_frame_extract(agent)
        self._attach_vlm(agent, [self.QC_FAIL, self.QC_PASS])
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=34,
            image_url="http://x/sb.png",
            prompt="a girl",
            style="国漫",
            reference_images=["http://x/char_front.png"],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 2
        wf2 = mock_call_comfyui.call_args_list[1].args[1]
        assert wf2["20"]["class_type"] == "MiniMaxH3ReferenceToVideo"
        assert wf2["20"]["inputs"]["ref_images"]["ref_image_1"] == ["11", 0]
        assert wf2["20"]["inputs"]["prompt"].startswith(
            "Rendered strictly in Chinese anime guoman style."
        )

    async def test_qc_fail_open_on_vlm_exception(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """VLM 调用异常 → fail-open 放行，不重生成。"""
        self._enable_qc(monkeypatch)
        self._mock_frame_extract(agent)
        vlm = MagicMock()
        vlm.chat.completions.create = AsyncMock(side_effect=RuntimeError("vlm down"))
        agent._vlm_client = vlm
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=35, image_url="http://x/sb.png", prompt="a girl", style="国漫"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 1

    async def test_qc_bad_json_fail_open(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """VLM 返回坏 JSON → fail-open 放行，不重生成。"""
        self._enable_qc(monkeypatch)
        self._mock_frame_extract(agent)
        self._attach_vlm(agent, ["not-json{{{"])
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=36, image_url="http://x/sb.png", prompt="a girl", style="国漫"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 1

    async def test_qc_disabled_skips_vlm(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """h3_style_qc_enabled=False → 不发起任何 VLM 调用（回滚路径与现状一致）。"""
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_style_qc_enabled", False)
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        self._mock_frame_extract(agent)
        vlm = self._attach_vlm(agent, [])
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(
            scene_id=37, image_url="http://x/sb.png", prompt="a girl", style="国漫"
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 1
        assert vlm.chat.completions.create.await_count == 0
        agent._extract_h3_middle_frame.assert_not_awaited()

    async def test_qc_skipped_when_style_empty(
        self,
        agent,
        monkeypatch,
        mock_upload_image,
        mock_call_comfyui,
        mock_get_comfyui_result,
    ):
        """style 为空（直连 API 未传画风）→ 无质检基准，跳过 VLM 与帧抽取。"""
        self._enable_qc(monkeypatch)
        self._mock_frame_extract(agent)
        vlm = self._attach_vlm(agent, [])
        mock_get_comfyui_result.return_value = self._video_outputs()

        request = VideoRequest(scene_id=38, image_url="http://x/sb.png", prompt="a girl")
        resp = await agent.execute(request)

        assert resp.success is True
        assert mock_call_comfyui.await_count == 1
        assert vlm.chat.completions.create.await_count == 0
        agent._extract_h3_middle_frame.assert_not_awaited()


# ---------------------------------------------------------------------------
# M19.1: MiniMax H3 Turbo LoRA 工作流改造（可选加速，默认关闭）
# ---------------------------------------------------------------------------


class TestH3TurboWorkflowTransformation:
    """Turbo LoRA 对工作流的改造是纯本地变换，不依赖真实 ComfyUI 服务。

    默认 h3_turbo_enabled=False 时原工作流应完全不变；开启后应：
      - 插入 MiniMaxH3TurboLoRA 节点并接管 model 链路
      - 替换 KSamplerSelect 为 MiniMaxH3TurboSampler
      - BasicScheduler steps 切换为 h3_turbo_steps
    """

    @pytest.fixture
    def fresh_workflow(self):
        """返回官方 fl2va 工作流深拷贝。"""
        import json

        from app.agents.video_agent import WORKFLOW_TEMPLATE_H3

        return json.loads(json.dumps(WORKFLOW_TEMPLATE_H3))

    @pytest.fixture
    def fresh_r2v_workflow(self):
        """返回官方 r2v 工作流深拷贝。"""
        import json

        from app.agents.video_agent import WORKFLOW_TEMPLATE_H3_R2V

        return json.loads(json.dumps(WORKFLOW_TEMPLATE_H3_R2V))

    def test_turbo_disabled_keeps_native_workflow(self, monkeypatch, fresh_workflow):
        """默认关闭：采样器、steps、model 链路保持原生。"""
        monkeypatch.setattr(settings, "h3_turbo_enabled", False)
        from app.agents.video_agent import _apply_h3_turbo_to_workflow

        _apply_h3_turbo_to_workflow(fresh_workflow)

        assert "100" not in fresh_workflow
        assert "101" not in fresh_workflow
        assert fresh_workflow["31"]["class_type"] == "KSamplerSelect"
        assert fresh_workflow["32"]["inputs"]["steps"] == 20
        assert fresh_workflow["32"]["inputs"]["model"] == ["1", 0]
        assert fresh_workflow["33"]["inputs"]["model"] == ["1", 0]

    def test_turbo_enabled_transforms_fl2va(self, monkeypatch, fresh_workflow):
        """开启 Turbo：LoRA 接管 model，专用采样器替换，steps=6。"""
        monkeypatch.setattr(settings, "h3_turbo_enabled", True)
        monkeypatch.setattr(settings, "h3_turbo_steps", 6)
        monkeypatch.setattr(settings, "h3_turbo_strength", 1.0)
        monkeypatch.setattr(settings, "h3_turbo_low_vram", False)
        from app.agents.video_agent import _apply_h3_turbo_to_workflow

        _apply_h3_turbo_to_workflow(fresh_workflow)

        assert fresh_workflow["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert fresh_workflow["100"]["inputs"]["model"] == ["1", 0]
        assert fresh_workflow["100"]["inputs"]["strength"] == 1.0
        assert fresh_workflow["100"]["inputs"]["low_vram"] is False

        assert fresh_workflow["101"]["class_type"] == "MiniMaxH3TurboSampler"

        # model 链路被 LoRA 接管
        assert fresh_workflow["32"]["inputs"]["model"] == ["100", 0]
        assert fresh_workflow["33"]["inputs"]["model"] == ["100", 0]

        # 采样器替换并指向 TurboSampler
        assert fresh_workflow["34"]["inputs"]["sampler"] == ["101", 0]
        assert fresh_workflow["32"]["inputs"]["steps"] == 6

    def test_turbo_enabled_transforms_r2v(self, monkeypatch, fresh_r2v_workflow):
        """r2v 工作流同样能被 Turbo 改造。"""
        monkeypatch.setattr(settings, "h3_turbo_enabled", True)
        monkeypatch.setattr(settings, "h3_turbo_steps", 6)
        from app.agents.video_agent import _apply_h3_turbo_to_workflow

        _apply_h3_turbo_to_workflow(fresh_r2v_workflow)

        assert fresh_r2v_workflow["100"]["class_type"] == "MiniMaxH3TurboLoRA"
        assert fresh_r2v_workflow["32"]["inputs"]["steps"] == 6
        assert fresh_r2v_workflow["34"]["inputs"]["sampler"] == ["101", 0]

    def test_turbo_steps_configurable(self, monkeypatch, fresh_workflow):
        """h3_turbo_steps 可配置到 4 步。"""
        monkeypatch.setattr(settings, "h3_turbo_enabled", True)
        monkeypatch.setattr(settings, "h3_turbo_steps", 4)
        from app.agents.video_agent import _apply_h3_turbo_to_workflow

        _apply_h3_turbo_to_workflow(fresh_workflow)
        assert fresh_workflow["32"]["inputs"]["steps"] == 4

    def test_turbo_low_vram_passed(self, monkeypatch, fresh_workflow):
        """low_vram 开关透传到 LoRA 节点。"""
        monkeypatch.setattr(settings, "h3_turbo_enabled", True)
        monkeypatch.setattr(settings, "h3_turbo_low_vram", True)
        from app.agents.video_agent import _apply_h3_turbo_to_workflow

        _apply_h3_turbo_to_workflow(fresh_workflow)
        assert fresh_workflow["100"]["inputs"]["low_vram"] is True

    def test_turbo_missing_unet_loader_logs_warning(
        self, monkeypatch, fresh_workflow, caplog
    ):
        """工作流缺少 UNETLoader 节点 1 时，应记录 warning 并跳过。"""
        monkeypatch.setattr(settings, "h3_turbo_enabled", True)
        from app.agents.video_agent import _apply_h3_turbo_to_workflow

        del fresh_workflow["1"]
        with caplog.at_level("WARNING"):
            _apply_h3_turbo_to_workflow(fresh_workflow)

        assert "未找到 UNETLoader 节点 1" in caplog.text


class TestH3UnetNsfwPin:
    def test_sfw_unets_are_minimax(self):
        from app.agents.video_agent import resolve_h3_unet_names
        from app.config import settings
        fl2va, ref2va = resolve_h3_unet_names(nsfw=False)
        assert fl2va == settings.h3_unet_name
        assert ref2va == settings.h3_ref_unet_name
        assert "10Eros" not in fl2va

    def test_nsfw_pin_uses_10eros_unets(self):
        from app.agents.video_agent import resolve_h3_unet_names
        from app.config import settings
        fl2va, ref2va = resolve_h3_unet_names(nsfw=True)
        assert fl2va == settings.h3_nsfw_unet_name
        assert ref2va == settings.h3_nsfw_ref_unet_name
        assert fl2va.startswith("10Eros")
        assert ref2va.startswith("10Eros")
