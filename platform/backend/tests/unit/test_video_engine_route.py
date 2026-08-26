"""视频引擎路由判定单元测试（route_video_engine 纯函数 + execute LTX 分发链）。

路由契约：
- engine 显式指定（h3/ltx/comfyui）→ 直达（ltx 未启用时降级 h3）
- engine=None/'auto' → 按镜头类型路由：
  对白（<d> 标签）/ 参考图 / 末帧锚定 → H3（角色一致性优先）
  时长超 H3 训练上限 / 纯运动空镜描述 → LTX-2.5（ltx_enabled 为前提）
- settings.video_backend='comfyui' 时钉死旧行为（向后兼容既有用例）
回退链：ltx → h3 → comfyui；h3 → comfyui。
"""

from __future__ import annotations

import pytest

from app.agents.video_agent import VideoAgent, route_video_engine
from app.config import settings
from app.models.schemas import VideoRequest


@pytest.fixture
def agent():
    return VideoAgent()


def _req(**kw) -> VideoRequest:
    defaults = dict(
        scene_id=1,
        image_url="http://x/i.png",
        prompt="cinematic",
        duration_seconds=3,
    )
    defaults.update(kw)
    return VideoRequest(**defaults)


class TestExplicitEngine:
    """engine 显式指定 → 直达，不被镜头类型规则覆盖。"""

    def test_explicit_h3_wins_over_long_duration(self, monkeypatch):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        monkeypatch.setattr(settings, "video_backend", "h3")
        assert route_video_engine(_req(engine="h3", duration_seconds=30), settings) == "h3"

    def test_explicit_ltx(self, monkeypatch):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        assert route_video_engine(_req(engine="ltx"), settings) == "ltx"

    def test_explicit_ltx_disabled_falls_back_h3(self, monkeypatch):
        # conftest 默认 ltx_enabled=False
        monkeypatch.setattr(settings, "video_backend", "h3")
        assert route_video_engine(_req(engine="ltx"), settings) == "h3"

    def test_explicit_comfyui(self, monkeypatch):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        monkeypatch.setattr(settings, "video_backend", "h3")
        assert route_video_engine(_req(engine="comfyui"), settings) == "comfyui"


class TestAutoRouting:
    """engine=None/'auto' 且 video_backend='h3'/'auto' → 按镜头类型路由。"""

    @pytest.fixture(autouse=True)
    def _enable_routing(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", True)

    def test_dialogue_routes_h3(self):
        req = _req(prompt="a man sits. <d>[zh] 你终于来了</d> (S1)", duration_seconds=30)
        assert route_video_engine(req, settings) == "h3"

    def test_reference_images_route_h3(self):
        req = _req(reference_images=["http://x/char.png"], duration_seconds=30)
        assert route_video_engine(req, settings) == "h3"

    def test_reference_videos_route_h3(self):
        req = _req(reference_videos=["http://x/ref.mp4"], duration_seconds=30)
        assert route_video_engine(req, settings) == "h3"

    def test_last_frame_routes_h3(self):
        req = _req(last_frame_url="http://x/end.png", duration_seconds=30)
        assert route_video_engine(req, settings) == "h3"

    def test_long_duration_routes_ltx(self):
        assert route_video_engine(_req(duration_seconds=20), settings) == "ltx"

    def test_long_duration_ltx_disabled_routes_h3(self, monkeypatch):
        monkeypatch.setattr(settings, "ltx_enabled", False)
        assert route_video_engine(_req(duration_seconds=20), settings) == "h3"

    def test_pure_motion_routes_ltx(self):
        req = _req(prompt="aerial drone shot over the city, camera pans across the skyline")
        assert route_video_engine(req, settings) == "ltx"

    def test_plain_short_prompt_routes_h3(self):
        assert route_video_engine(_req(prompt="a girl smiles at the camera"), settings) == "h3"

    def test_auto_keyword_behaves_like_none(self):
        assert route_video_engine(_req(engine="auto", duration_seconds=20), settings) == "ltx"


class TestLegacyBackendPin:
    """video_backend='comfyui'（conftest 默认）→ 钉死旧 ComfyUI 路径。"""

    def test_comfyui_backend_pinned(self):
        assert route_video_engine(_req(duration_seconds=30), settings) == "comfyui"


class TestExecuteLtxBranch:
    """execute 的 LTX 分发与回退链（ltx → h3 → comfyui）。"""

    @staticmethod
    def _ltx_outputs():
        return {"70": {"videos": [{"filename": "ltx.mp4", "subfolder": "", "type": "output"}]}}

    async def test_ltx_engine_goes_to_ltx_instance(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        mock_get_comfyui_result.return_value = self._ltx_outputs()

        resp = await agent.execute(_req(engine="ltx", duration_seconds=3))

        assert resp.success is True
        assert "ltx.mp4" in resp.data["video_url"]
        assert resp.data["duration_seconds"] == 73 // 25
        # 直连 LTX 专用实例（conftest 占位 :9006），有 image_url → I2V 工作流
        assert mock_call_comfyui.call_args[0][0] == settings.ltx_comfyui_url
        workflow = mock_call_comfyui.call_args[0][1]
        assert workflow["81"]["class_type"] == "LTXVImgToVideo"

    async def test_ltx_failure_falls_back_to_h3(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        # LTX 上传即失败；H3 回退上传成功
        mock_upload_image.side_effect = [RuntimeError("ltx down"), "img.png"]
        mock_get_comfyui_result.return_value = {
            "60": {"videos": [{"filename": "h3.mp4", "subfolder": "", "type": "output"}]}
        }

        resp = await agent.execute(_req(engine="ltx"))

        assert resp.success is True
        assert "h3.mp4" in resp.data["video_url"]
        # 第二次上传走 H3 专用实例
        assert mock_upload_image.call_args_list[1].args[0] == settings.h3_comfyui_url

    async def test_ltx_and_h3_fail_fall_back_to_comfyui(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        mock_upload_image.side_effect = [
            RuntimeError("ltx down"),
            RuntimeError("h3 down"),
            "img.png",
        ]
        mock_get_comfyui_result.return_value = {
            "8": {"videos": [{"filename": "wan.mp4", "subfolder": "", "type": "output"}]}
        }

        resp = await agent.execute(_req(engine="ltx"))

        assert resp.success is True
        assert "wan.mp4" in resp.data["video_url"]
        assert mock_upload_image.await_count == 3

    async def test_all_engines_fail_reports_chain_errors(
        self, agent, monkeypatch, mock_upload_image
    ):
        monkeypatch.setattr(settings, "ltx_enabled", True)
        mock_upload_image.side_effect = RuntimeError("always down")

        resp = await agent.execute(_req(engine="ltx"))

        assert resp.success is False
        # 回退链各引擎错误均入列（LTX 服务层会包装一层错误前缀）
        assert "ltx=" in resp.error
        assert "h3=always down" in resp.error
        assert "comfyui=always down" in resp.error
        assert resp.error.count("always down") == 3

    async def test_auto_long_duration_routes_to_ltx_instance(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "ltx_enabled", True)
        mock_get_comfyui_result.return_value = self._ltx_outputs()

        resp = await agent.execute(_req(duration_seconds=20))

        assert resp.success is True
        assert mock_call_comfyui.call_args[0][0] == settings.ltx_comfyui_url
