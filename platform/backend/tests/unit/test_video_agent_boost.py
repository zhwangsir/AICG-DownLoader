"""video_agent 覆盖率补充测试（boost）。

针对既有套件未覆盖的分支：
- 路由/纯函数边界：_is_pure_motion_prompt 空串与台词短路、video_backend='ltx' 直达、
  strengthen_h3_style_clause 无风格名透传
- 子进程封装：_run_ffmpeg 成功/失败、_extract_h3_middle_frame 时长解析与非法值回退
- VLM 客户端懒加载单例、_download_to_file 落盘
- execute/多镜/fl2va/r2v/LTX 各路径的 progress_callback 上报
- prompt_id 缺失三条路径（ComfyUI 单镜 / H3 单镜 / H3 多镜组）
- 逐场景兜底防御性捕获、batch_execute 任务异常计入 failed_scenes
- 画风 QC 漂移重生成上报与重生成失败放行

conftest._patch_settings 默认 video_backend='comfyui'、ltx_enabled=False，
H3/LTX 用例局部 monkeypatch 覆盖。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import AsyncOpenAI

from app.agents.video_agent import (
    VideoAgent,
    _is_pure_motion_prompt,
    _run_ffmpeg,
    route_video_engine,
    strengthen_h3_style_clause,
)
from app.config import settings
from app.models.schemas import AgentResponse, VideoBatchRequest, VideoRequest


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


def _video_outputs(filename: str = "v.mp4") -> dict:
    return {"60": {"videos": [{"filename": filename, "subfolder": "", "type": "output"}]}}


def _make_vlm_result(content: str):
    """构造 VLM chat.completions.create 返回值（与 test_video_agent 同构）。"""
    result = MagicMock()
    result.choices = [MagicMock()]
    result.choices[0].message.content = content
    return result


class _FakeProc:
    """asyncio.create_subprocess_exec 假进程（communicate + returncode）。"""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


class TestPureMotionPromptBoundary:
    """_is_pure_motion_prompt 短路分支：空串/含台词 → False。"""

    def test_empty_prompt_is_not_pure_motion(self):
        assert _is_pure_motion_prompt("") is False

    def test_dialogue_prompt_is_not_pure_motion(self):
        # 命中运动关键词但含 <d> 台词标签 → 仍归 H3（角色一致性优先）
        assert _is_pure_motion_prompt("aerial drone shot. <d>[zh] 你来了</d>") is False


class TestLtxBackendDefault:
    """video_backend='ltx' 时按 ltx_enabled 直达或降级。"""

    def test_backend_ltx_enabled_routes_ltx(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "ltx")
        monkeypatch.setattr(settings, "ltx_enabled", True)
        assert route_video_engine(_req(engine=None), settings) == "ltx"

    def test_backend_ltx_disabled_falls_back_h3(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "ltx")
        monkeypatch.setattr(settings, "ltx_enabled", False)
        assert route_video_engine(_req(engine=None), settings) == "h3"


class TestStrengthenClauseNoStyleName:
    """纠偏层：anchor 无英文风格名时原样透传（防御分支）。"""

    def test_empty_style_name_passthrough(self, monkeypatch):
        from app.services.style_anchor import StyleAnchor

        anchor = StyleAnchor(
            key="k", title="未知画风", keywords_en="",
            style_name_en="", negative_en="", is_realistic=False,
        )
        monkeypatch.setattr(
            "app.agents.video_agent.resolve_style_anchor", lambda style: anchor
        )
        prompt = "a girl in rain"
        assert strengthen_h3_style_clause(prompt, "未知画风") == prompt


class TestRunFfmpeg:
    """_run_ffmpeg：成功静默返回；非零退出码抛 RuntimeError 并携带 stderr 尾部。"""

    async def test_success_returns_none(self):
        with patch(
            "app.agents.video_agent.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_FakeProc(0)),
        ) as m_exec:
            result = await _run_ffmpeg(["ffmpeg", "-version"])
        assert result is None
        m_exec.assert_awaited_once()
        assert m_exec.call_args.args[:2] == ("ffmpeg", "-version")

    async def test_nonzero_returncode_raises_with_stderr(self):
        proc = _FakeProc(returncode=1, stderr=b"x" * 600 + b"boom tail")
        with patch(
            "app.agents.video_agent.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            with pytest.raises(RuntimeError, match="FFmpeg failed") as exc_info:
                await _run_ffmpeg(["ffmpeg", "bad-args"])
        # stderr 尾部截断到末 500 字符
        assert "boom tail" in str(exc_info.value)


class TestVlmClientLazyLoad:
    """_get_vlm_client：首次调用创建 AsyncOpenAI，之后复用同一实例。"""

    def test_lazy_singleton(self, agent):
        assert agent._vlm_client is None
        client = agent._get_vlm_client()
        assert isinstance(client, AsyncOpenAI)
        assert str(client.base_url).rstrip("/") == settings.visual_model_url.rstrip("/")
        assert agent._get_vlm_client() is client


class TestExecuteProgressCallback:
    """execute 的 H3 分发：_report 经 progress_callback 上报（引擎提示 + fl2va 各阶段）。"""

    async def test_h3_execute_reports_progress(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_get_comfyui_result.return_value = _video_outputs()

        events: list[tuple[int, str]] = []
        resp = await agent.execute(
            _req(scene_id=5), progress_callback=lambda p, m: events.append((p, m))
        )

        assert resp.success is True
        # execute 分发层引擎提示（_report(5, ...)）
        assert (5, "MiniMax H3 音视频联合生成") in events
        # fl2va 内部各阶段上报（_execute_via_h3_fl2va._report）
        assert any("上传分镜图片" in m for _, m in events)
        assert any("构建 MiniMax H3 fl2va 工作流" in m for _, m in events)
        assert events[-1] == (100, "H3 视频生成完成（含原生音频）")


class TestExecuteViaLtxBranches:
    """_execute_via_ltx：FLF2V 双锚定 / T2V 纯文本两个未覆盖分支。"""

    @staticmethod
    def _ok_service() -> MagicMock:
        service = MagicMock()
        ok = AgentResponse(
            success=True,
            data={"scene_id": 1, "video_url": "http://x/ltx.mp4", "duration_seconds": 2},
        )
        service.generate_flf2v = AsyncMock(return_value=ok)
        service.generate_i2v = AsyncMock(return_value=ok)
        service.generate_t2v = AsyncMock(return_value=ok)
        return service

    async def test_flf2v_branch_with_progress(self, agent, monkeypatch):
        """last_frame_url 非空 → FLF2V 首尾帧双锚定；进度回调上报 LTX 提示。"""
        monkeypatch.setattr(settings, "ltx_enabled", True)
        service = self._ok_service()
        events: list[tuple[int, str]] = []
        with patch("app.agents.video_agent.LTX25VideoService", return_value=service):
            resp = await agent.execute(
                _req(
                    engine="ltx",
                    image_url="http://x/first.png",
                    last_frame_url="http://x/last.png",
                    prompt="p",
                ),
                progress_callback=lambda p, m: events.append((p, m)),
            )

        assert resp.success is True
        service.generate_flf2v.assert_awaited_once()
        args = service.generate_flf2v.call_args
        assert args.args[:3] == ("http://x/first.png", "http://x/last.png", "p")
        assert args.kwargs["scene_id"] == 1
        assert args.kwargs["num_frames"] % 8 == 1  # LTX 帧网格
        service.generate_i2v.assert_not_awaited()
        service.generate_t2v.assert_not_awaited()
        # _execute_via_ltx 入口进度上报
        assert (5, "LTX-2.5 音视频联合生成（distilled 8+3 步）") in events

    async def test_t2v_branch(self, agent, monkeypatch):
        """无首帧且无末帧 → T2V 纯文本驱动。"""
        monkeypatch.setattr(settings, "ltx_enabled", True)
        service = self._ok_service()
        with patch("app.agents.video_agent.LTX25VideoService", return_value=service):
            resp = await agent.execute(
                _req(engine="ltx", image_url="", prompt="aerial drone shot"),
            )

        assert resp.success is True
        service.generate_t2v.assert_awaited_once()
        assert service.generate_t2v.call_args.args[0] == "aerial drone shot"
        service.generate_i2v.assert_not_awaited()
        service.generate_flf2v.assert_not_awaited()


class TestExecuteMultiShotProgress:
    """execute_multi_shot：多镜组失败整组回退时的进度上报。"""

    async def test_group_failure_falls_back_with_progress(self, agent, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        ok = AgentResponse(
            success=True,
            data={"scene_id": 0, "video_url": "http://x/v.mp4", "duration_seconds": 3},
        )
        events: list[tuple[int, str]] = []
        with (
            patch.object(
                agent, "_execute_multishot_group",
                new_callable=AsyncMock, side_effect=RuntimeError("group boom"),
            ),
            patch.object(agent, "execute", new_callable=AsyncMock, return_value=ok) as m_exec,
        ):
            results = await agent.execute_multi_shot(
                [_req(scene_id=1), _req(scene_id=2)],
                progress_callback=lambda p, m: events.append((p, m)),
            )

        assert len(results) == 2 and all(r.success for r in results)
        assert m_exec.await_count == 2
        # 多镜启动与失败回退两条上报均经 progress_callback
        assert any("多镜叙事联合生成" in m for _, m in events)
        assert any(p == 50 and "多镜失败，回退逐场景生成" in m for p, m in events)


class TestExecuteScenesIndividuallyDefense:
    """_execute_scenes_individually：execute 异常时的防御性捕获（正常路径 execute 自兜底）。"""

    async def test_execute_exception_wrapped_into_failed_response(self, agent):
        with patch.object(
            agent, "execute", new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ):
            results = await agent._execute_scenes_individually(
                [_req(scene_id=1), _req(scene_id=2)], None, time.time()
            )

        assert len(results) == 2
        assert all(r.success is False for r in results)
        assert all("视频生成失败: boom" in r.error for r in results)


class TestMultishotMissingPromptId:
    """_execute_multishot_group：H3 ComfyUI 未返回 prompt_id → 抛错，整组回退逐场景。"""

    async def test_missing_prompt_id_falls_back_to_individual(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_call_comfyui.return_value = {"error": "workflow rejected"}  # 无 prompt_id
        ok = AgentResponse(
            success=True,
            data={"scene_id": 0, "video_url": "http://x/v.mp4", "duration_seconds": 3},
        )
        with patch.object(agent, "execute", new_callable=AsyncMock, return_value=ok) as m_exec:
            results = await agent.execute_multi_shot([_req(scene_id=1), _req(scene_id=2)])

        # 组级提交 1 次（无 prompt_id 抛 RuntimeError），回退后逐场景各 1 次
        assert mock_call_comfyui.await_count == 1
        assert m_exec.await_count == 2
        assert len(results) == 2 and all(r.success for r in results)


class TestDownloadToFile:
    """_download_to_file：HTTP 响应字节落盘并返回目标路径。"""

    async def test_writes_response_bytes(self, agent, tmp_path, mock_httpx_get):
        dest = tmp_path / "group.mp4"
        result = await agent._download_to_file("http://x/group.mp4", dest)

        assert result == dest
        assert dest.read_bytes() == b"fake-image-bytes"
        mock_httpx_get.assert_awaited_once_with("http://x/group.mp4")


class TestStyleQcProgressAndFailure:
    """M18.4 纠偏层：漂移重生成的进度上报；重生成失败时直接放行失败响应。"""

    QC_PASS = json.dumps({"pass": True, "reason": ""})
    QC_FAIL = json.dumps({"pass": False, "reason": "半写实厚涂而非卡通平涂"})

    def _enable_qc(self, monkeypatch):
        monkeypatch.setattr(settings, "video_backend", "h3")
        monkeypatch.setattr(settings, "h3_style_qc_enabled", True)
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")

    async def test_drift_reports_progress_callback(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        """首次漂移 → progress_callback 收到 96% 纠偏上报，重提交后复检合格。"""
        self._enable_qc(monkeypatch)
        agent._extract_h3_middle_frame = AsyncMock(return_value=b"fake-png-bytes")
        vlm = MagicMock()
        vlm.chat.completions.create = AsyncMock(
            side_effect=[_make_vlm_result(self.QC_FAIL), _make_vlm_result(self.QC_PASS)]
        )
        agent._vlm_client = vlm
        mock_get_comfyui_result.return_value = _video_outputs()

        events: list[tuple[int, str]] = []
        resp = await agent.execute(
            _req(scene_id=7, prompt="a girl", style="国漫"),
            progress_callback=lambda p, m: events.append((p, m)),
        )

        assert resp.success is True
        assert mock_call_comfyui.await_count == 2
        drift_events = [(p, m) for p, m in events if p == 96]
        assert len(drift_events) == 1
        assert "画风漂移" in drift_events[0][1]
        assert "半写实厚涂" in drift_events[0][1]
        assert "1/1" in drift_events[0][1]

    async def test_regenerate_failure_returns_failed_response(self, agent, monkeypatch):
        """漂移后重生成失败 → 直接返回失败响应（不再继续重试）。"""
        monkeypatch.setattr(settings, "h3_style_qc_enabled", True)
        monkeypatch.setattr(settings, "visual_model_url", "http://vlm:9000/v1")
        ok = AgentResponse(
            success=True,
            data={"scene_id": 8, "video_url": "http://x/v.mp4", "duration_seconds": 5},
        )
        bad = AgentResponse(success=False, error="h3 OOM")
        agent._execute_via_h3 = AsyncMock(side_effect=[ok, bad])
        agent._h3_style_qc_check = AsyncMock(return_value=(False, "drifted"))

        resp = await agent._execute_h3_with_style_qc(
            _req(scene_id=8, prompt="a girl", style="国漫")
        )

        assert resp.success is False
        assert resp.error == "h3 OOM"
        assert agent._execute_via_h3.await_count == 2
        # 重生成失败后不再做第二次 QC
        assert agent._h3_style_qc_check.await_count == 1


class TestExtractH3MiddleFrame:
    """_extract_h3_middle_frame：ffprobe 时长解析 → ffmpeg 抽中点帧 → PNG 字节。"""

    @staticmethod
    def _fake_exec_factory(probe_stdout: bytes, written: dict):
        async def fake_exec(*cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return _FakeProc(0, stdout=probe_stdout)
            # ffmpeg 抽帧：末位参数为输出帧路径，模拟写出 PNG
            frame_path = Path(cmd[-1])
            frame_path.write_bytes(b"fake-png")
            written["ss"] = cmd[cmd.index("-ss") + 1]
            return _FakeProc(0)

        return fake_exec

    async def test_valid_duration_extracts_midpoint(self, agent):
        agent._download_to_file = AsyncMock(side_effect=lambda url, dest: dest)
        written: dict = {}
        with patch(
            "app.agents.video_agent.asyncio.create_subprocess_exec",
            new=self._fake_exec_factory(b"4.000\n", written),
        ):
            data = await agent._extract_h3_middle_frame("http://x/v.mp4")

        assert data == b"fake-png"
        assert written["ss"] == "2.000"  # 4s 视频的中点

    async def test_invalid_duration_defaults_to_zero(self, agent):
        """ffprobe 输出非数值 → duration=0.0 → 抽第 0 秒帧（不抛异常）。"""
        agent._download_to_file = AsyncMock(side_effect=lambda url, dest: dest)
        written: dict = {}
        with patch(
            "app.agents.video_agent.asyncio.create_subprocess_exec",
            new=self._fake_exec_factory(b"N/A", written),
        ):
            data = await agent._extract_h3_middle_frame("http://x/v.mp4")

        assert data == b"fake-png"
        assert written["ss"] == "0.000"


class TestR2vFallbackProgress:
    """_execute_via_h3：ref2va 失败回退 fl2va 时的进度上报（含 r2v 自身的阶段上报）。"""

    async def test_r2v_failure_reports_progress(
        self, agent, monkeypatch, mock_upload_image, mock_call_comfyui, mock_get_comfyui_result
    ):
        monkeypatch.setattr(settings, "video_backend", "h3")
        mock_call_comfyui.side_effect = [
            RuntimeError("r2v OOM"),
            {"prompt_id": "p-fl2va"},
        ]
        mock_get_comfyui_result.return_value = _video_outputs()

        events: list[tuple[int, str]] = []
        resp = await agent.execute(
            _req(scene_id=6, reference_images=["http://x/a.png"]),
            progress_callback=lambda p, m: events.append((p, m)),
        )

        assert resp.success is True
        assert mock_call_comfyui.await_count == 2
        # r2v 路径内部阶段上报（_execute_via_h3_r2v._report）
        assert any("上传关键帧与 1 张角色参考图" in m for _, m in events)
        # ref2va → fl2va 回退上报
        fallback = [(p, m) for p, m in events if p == 20 and "ref2va 失败" in m]
        assert len(fallback) == 1
        assert "r2v OOM" in fallback[0][1]


class TestSubmitH3WorkflowMissingPromptId:
    """_submit_h3_workflow：H3 ComfyUI 应答缺 prompt_id → RuntimeError。"""

    async def test_missing_prompt_id_raises(self, agent):
        with patch.object(agent, "call_comfyui", new_callable=AsyncMock) as m_call:
            m_call.return_value = {"error": "bad workflow"}
            reports: list[tuple[int, str]] = []
            with pytest.raises(RuntimeError, match="H3 ComfyUI 未返回 prompt_id"):
                await agent._submit_h3_workflow(
                    "http://w", {"1": {}}, 7, 124, time.time(),
                    lambda p, m: reports.append((p, m)),
                )
        # 提交前已上报 25% 进度
        assert reports == [(25, "提交 H3 视频生成任务")]


class TestComfyuiMissingPromptId:
    """_execute_via_comfyui：ComfyUI 应答缺 prompt_id → execute 返回失败响应。"""

    async def test_missing_prompt_id_returns_error(
        self, agent, mock_upload_image, mock_call_comfyui
    ):
        mock_call_comfyui.return_value = {"error": "bad workflow"}

        resp = await agent.execute(_req(scene_id=1))

        assert resp.success is False
        assert "ComfyUI 未返回 prompt_id" in resp.error


class TestBatchExecuteTaskException:
    """batch_execute：单场景任务抛异常（非 AgentResponse 失败）→ 计入 failed_scenes。"""

    async def test_task_exception_recorded_as_failed(self, agent):
        with (
            patch.object(
                agent, "get_available_video_workers", new_callable=AsyncMock
            ) as m_workers,
            patch.object(
                agent, "execute", new_callable=AsyncMock, side_effect=RuntimeError("worker exploded")
            ),
        ):
            m_workers.return_value = ["http://w-a", "http://w-b"]
            resp = await agent.batch_execute(
                VideoBatchRequest(items=[_req(scene_id=1), _req(scene_id=2)])
            )

        assert resp.success is True  # 批量整体仍 success，异常场景入 failed_scenes
        assert resp.data["results"] == []
        assert sorted(resp.data["failed_scenes"]) == [1, 2]
