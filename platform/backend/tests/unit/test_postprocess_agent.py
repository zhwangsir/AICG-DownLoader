"""PostprocessAgent 单元测试 — P4.4 编排 + best-effort 覆盖。

覆盖：
- postprocess_enabled=False 总开关关闭：跳过，返回原视频
- _resolve_steps: 单步开关组合 / override 去重保序
- 主路径成功：单步成功链路 + 进度回调
- best-effort：单步失败不阻断后续步骤
- AUDIO_DENOISE 缺 audio_url 抛错
- FINAL_ENCODE 调用 _final_encode
- _save_denoised_audio 保存字节并返回 URL
- _run_step 各步骤分支派发
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.postprocess_agent import PostprocessAgent, _parse_resolution
from app.config import settings
from app.models.schemas import (
    PostprocessRequest,
    PostprocessResult,
    PostprocessStep,
    PostprocessStepResult,
)
from app.services.postprocess_service import PostprocessServiceError


@pytest.fixture
def agent():
    return PostprocessAgent()


def _attach_mock_postprocess(agent):
    """注入 mock PostprocessService。"""
    mock_svc = MagicMock()
    mock_svc.run_super_resolution = AsyncMock(return_value="http://4k/out.mp4")
    mock_svc.run_frame_interpolation = AsyncMock(
        return_value="http://60fps/out.mp4"
    )
    mock_svc.run_inpainting = AsyncMock(return_value="http://clean/out.mp4")
    agent._postprocess = mock_svc
    return mock_svc


def _attach_mock_deepfilternet(agent, return_bytes=b"denoised"):
    mock_svc = MagicMock()
    mock_svc.denoise = AsyncMock(return_value=return_bytes)
    agent._deepfilternet = mock_svc
    return mock_svc


# ============================================================================
# 总开关 + 步骤解析
# ============================================================================


class TestPostprocessDisabled:
    async def test_disabled_skips_all_steps(self, agent):
        """postprocess_enabled=False（conftest 默认）→ 跳过，返回原视频。"""
        request = PostprocessRequest(
            scene_id=1,
            video_url="http://mock/v.mp4",
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["final_video_url"] == "http://mock/v.mp4"
        assert resp.data["steps"] == []
        assert resp.data["success"] is True

    async def test_disabled_does_not_call_service(self, agent):
        mock_svc = MagicMock()
        mock_svc.run_super_resolution = AsyncMock()
        agent._postprocess = mock_svc

        request = PostprocessRequest(
            scene_id=2,
            video_url="http://mock/v.mp4",
        )
        await agent.execute(request)

        mock_svc.run_super_resolution.assert_not_awaited()


class TestResolveSteps:
    """_resolve_steps 单步开关与 override 组合。"""

    def test_all_enabled(self, agent, monkeypatch):
        """所有单步开关开启 → 返回 5 个步骤（按固定顺序）。"""
        monkeypatch.setattr(settings, "postprocess_super_resolution_enabled", True)
        monkeypatch.setattr(
            settings, "postprocess_frame_interpolation_enabled", True
        )
        monkeypatch.setattr(settings, "postprocess_inpainting_enabled", True)
        monkeypatch.setattr(settings, "postprocess_audio_denoise_enabled", True)
        monkeypatch.setattr(settings, "postprocess_final_encode_enabled", True)

        steps = agent._resolve_steps([])
        assert steps == [
            PostprocessStep.SUPER_RESOLUTION,
            PostprocessStep.FRAME_INTERPOLATION,
            PostprocessStep.INPAINTING,
            PostprocessStep.AUDIO_DENOISE,
            PostprocessStep.FINAL_ENCODE,
        ]

    def test_partial_enabled(self, agent, monkeypatch):
        """仅超分 + 编码启用 → 仅返回这两步。"""
        monkeypatch.setattr(settings, "postprocess_super_resolution_enabled", True)
        monkeypatch.setattr(
            settings, "postprocess_frame_interpolation_enabled", False
        )
        monkeypatch.setattr(settings, "postprocess_inpainting_enabled", False)
        monkeypatch.setattr(settings, "postprocess_audio_denoise_enabled", False)
        monkeypatch.setattr(settings, "postprocess_final_encode_enabled", True)

        steps = agent._resolve_steps([])
        assert steps == [
            PostprocessStep.SUPER_RESOLUTION,
            PostprocessStep.FINAL_ENCODE,
        ]

    def test_override_takes_precedence(self, agent, monkeypatch):
        """request.steps 非空时忽略 settings 单步开关，仅执行指定步骤。"""
        # 即使所有单步关闭，override 仍生效
        monkeypatch.setattr(settings, "postprocess_super_resolution_enabled", False)
        monkeypatch.setattr(
            settings, "postprocess_frame_interpolation_enabled", False
        )

        steps = agent._resolve_steps([PostprocessStep.FRAME_INTERPOLATION])
        assert steps == [PostprocessStep.FRAME_INTERPOLATION]

    def test_override_dedup_preserve_order(self, agent):
        """override 中重复步骤去重，保持首次出现顺序。"""
        steps = agent._resolve_steps([
            PostprocessStep.FINAL_ENCODE,
            PostprocessStep.SUPER_RESOLUTION,
            PostprocessStep.FINAL_ENCODE,  # 重复
            PostprocessStep.FRAME_INTERPOLATION,
        ])
        assert steps == [
            PostprocessStep.FINAL_ENCODE,
            PostprocessStep.SUPER_RESOLUTION,
            PostprocessStep.FRAME_INTERPOLATION,
        ]

    def test_empty_override_and_all_disabled(self, agent, monkeypatch):
        """override 为空 + 所有单步关闭 → 返回空列表。"""
        for attr in (
            "postprocess_super_resolution_enabled",
            "postprocess_frame_interpolation_enabled",
            "postprocess_inpainting_enabled",
            "postprocess_audio_denoise_enabled",
            "postprocess_final_encode_enabled",
        ):
            monkeypatch.setattr(settings, attr, False)

        steps = agent._resolve_steps([])
        assert steps == []


class TestNoEnabledStepsSkips:
    async def test_no_steps_skips(self, agent, monkeypatch):
        """总开关开启但单步全关 → 跳过，返回原视频。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        for attr in (
            "postprocess_super_resolution_enabled",
            "postprocess_frame_interpolation_enabled",
            "postprocess_inpainting_enabled",
            "postprocess_audio_denoise_enabled",
            "postprocess_final_encode_enabled",
        ):
            monkeypatch.setattr(settings, attr, False)

        request = PostprocessRequest(
            scene_id=3,
            video_url="http://mock/v.mp4",
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["final_video_url"] == "http://mock/v.mp4"
        assert resp.data["steps"] == []


# ============================================================================
# 主路径成功 + best-effort
# ============================================================================


class TestPostprocessMainPath:
    async def test_super_resolution_success(self, agent, monkeypatch):
        """单步 super_resolution → 调用 PostprocessService.run_super_resolution。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        mock_svc = _attach_mock_postprocess(agent)

        request = PostprocessRequest(
            scene_id=10,
            video_url="http://mock/v.mp4",
            steps=[PostprocessStep.SUPER_RESOLUTION],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["final_video_url"] == "http://4k/out.mp4"
        assert resp.data["original_video_url"] == "http://mock/v.mp4"
        assert len(resp.data["steps"]) == 1
        assert resp.data["steps"][0]["step"] == "super_resolution"
        assert resp.data["steps"][0]["success"] is True
        assert resp.data["steps"][0]["output_url"] == "http://4k/out.mp4"
        mock_svc.run_super_resolution.assert_awaited_once()

    async def test_chained_steps_success(self, agent, monkeypatch):
        """多个步骤顺序执行：SR → RIFE，current_url 链式传递。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        mock_svc = _attach_mock_postprocess(agent)

        # 验证 current_url 链式传递：RIFE 应接收 SR 的输出 URL
        received_urls: list[str] = []

        async def fake_rife(video_url, **kwargs):
            received_urls.append(video_url)
            return "http://60fps/out.mp4"

        mock_svc.run_frame_interpolation = AsyncMock(side_effect=fake_rife)

        request = PostprocessRequest(
            scene_id=11,
            video_url="http://mock/v.mp4",
            steps=[
                PostprocessStep.SUPER_RESOLUTION,
                PostprocessStep.FRAME_INTERPOLATION,
            ],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["final_video_url"] == "http://60fps/out.mp4"
        # RIFE 接收的是 SR 输出，而非原视频
        assert received_urls == ["http://4k/out.mp4"]

    async def test_single_step_failure_does_not_block(self, agent, monkeypatch):
        """SR 失败 → 记录失败但 RIFE 继续执行，current_url 保持原视频。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        mock_svc = _attach_mock_postprocess(agent)
        # SR 失败
        mock_svc.run_super_resolution.side_effect = PostprocessServiceError("SR OOM")

        received_urls: list[str] = []

        async def fake_rife(video_url, **kwargs):
            received_urls.append(video_url)
            return "http://60fps/out.mp4"

        mock_svc.run_frame_interpolation = AsyncMock(side_effect=fake_rife)

        request = PostprocessRequest(
            scene_id=12,
            video_url="http://mock/v.mp4",
            steps=[
                PostprocessStep.SUPER_RESOLUTION,
                PostprocessStep.FRAME_INTERPOLATION,
            ],
        )
        resp = await agent.execute(request)

        # best-effort：整体仍视为成功
        assert resp.success is True
        # 最终 URL 为 RIFE 的输出
        assert resp.data["final_video_url"] == "http://60fps/out.mp4"
        # 两个步骤都被记录
        assert len(resp.data["steps"]) == 2
        assert resp.data["steps"][0]["success"] is False
        assert resp.data["steps"][0]["output_url"] == ""
        assert "SR OOM" in resp.data["steps"][0]["message"]
        assert resp.data["steps"][1]["success"] is True
        # RIFE 接收原视频（SR 失败后 current_url 不变）
        assert received_urls == ["http://mock/v.mp4"]

    async def test_all_steps_fail_returns_success(self, agent, monkeypatch):
        """所有步骤都失败 → success=True（best-effort），final_video_url 为原视频。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        mock_svc = _attach_mock_postprocess(agent)
        mock_svc.run_super_resolution.side_effect = PostprocessServiceError("fail1")
        mock_svc.run_frame_interpolation.side_effect = PostprocessServiceError("fail2")

        request = PostprocessRequest(
            scene_id=13,
            video_url="http://mock/v.mp4",
            steps=[
                PostprocessStep.SUPER_RESOLUTION,
                PostprocessStep.FRAME_INTERPOLATION,
            ],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["final_video_url"] == "http://mock/v.mp4"
        assert resp.data["success"] is False  # PostprocessResult.success=False
        assert len(resp.data["steps"]) == 2
        assert all(not s["success"] for s in resp.data["steps"])


# ============================================================================
# AUDIO_DENOISE 步骤
# ============================================================================


class TestAudioDenoiseStep:
    async def test_denoise_success(self, agent, monkeypatch):
        """AUDIO_DENOISE 步骤调用 DeepFilterNetService.denoise 并保存字节。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        _attach_mock_postprocess(agent)
        _attach_mock_deepfilternet(agent, return_bytes=b"clean-audio")

        request = PostprocessRequest(
            scene_id=14,
            video_url="http://mock/v.mp4",
            audio_url="http://mock/a.mp3",
            steps=[PostprocessStep.AUDIO_DENOISE],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        # AUDIO_DENOISE 返回 URL（保存后的静态路径）
        step_url = resp.data["steps"][0]["output_url"]
        assert "/static/postprocess/" in step_url
        assert step_url.endswith(".mp3")

    async def test_denoise_without_audio_url_raises(self, agent, monkeypatch):
        """AUDIO_DENOISE 步骤未提供 audio_url → 该步失败，不阻断。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        _attach_mock_postprocess(agent)
        _attach_mock_deepfilternet(agent)

        request = PostprocessRequest(
            scene_id=15,
            video_url="http://mock/v.mp4",
            audio_url=None,
            steps=[PostprocessStep.AUDIO_DENOISE],
        )
        resp = await agent.execute(request)

        # best-effort：单步失败不阻断
        assert resp.success is True
        assert resp.data["steps"][0]["success"] is False
        assert "audio_url" in resp.data["steps"][0]["message"]


class TestSaveDenoisedAudio:
    async def test_save_bytes_returns_url(self, agent, monkeypatch, tmp_path):
        """_save_denoised_audio 写入字节并返回 /static/postprocess/ URL。"""
        # 替换 OUTPUT_DIR 到临时目录
        monkeypatch.setattr(
            "app.agents.postprocess_agent.OUTPUT_DIR", tmp_path
        )
        url = await agent._save_denoised_audio(
            b"audio-bytes", scene_id=99
        )
        assert "/static/postprocess/" in url
        assert url.endswith(".mp3")
        # 验证文件已写入
        files = list(tmp_path.glob("denoised_scene_99_*.mp3"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"audio-bytes"


# ============================================================================
# FINAL_ENCODE 步骤
# ============================================================================


class TestFinalEncodeStep:
    async def test_final_encode_invoked(self, agent, monkeypatch):
        """FINAL_ENCODE 步骤调用 _final_encode 方法。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        _attach_mock_postprocess(agent)

        # mock _final_encode 避免实际跑 FFmpeg
        agent._final_encode = AsyncMock(
            return_value="http://localhost/static/postprocess/final.mp4"
        )

        request = PostprocessRequest(
            scene_id=16,
            video_url="http://mock/v.mp4",
            steps=[PostprocessStep.FINAL_ENCODE],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["final_video_url"] == (
            "http://localhost/static/postprocess/final.mp4"
        )
        agent._final_encode.assert_awaited_once()
        # 透传 scene_id 和 video_url
        call_kwargs = agent._final_encode.await_args.kwargs
        assert call_kwargs["scene_id"] == 16
        assert call_kwargs["video_url"] == "http://mock/v.mp4"

    async def test_final_encode_failure_falls_back(self, agent, monkeypatch):
        """_final_encode 失败 → 该步失败，但不阻断后续。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        _attach_mock_postprocess(agent)
        agent._final_encode = AsyncMock(side_effect=RuntimeError("ffmpeg boom"))

        request = PostprocessRequest(
            scene_id=17,
            video_url="http://mock/v.mp4",
            steps=[PostprocessStep.FINAL_ENCODE],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["steps"][0]["success"] is False
        assert "ffmpeg boom" in resp.data["steps"][0]["message"]
        # final_video_url 保持上一步（原视频）
        assert resp.data["final_video_url"] == "http://mock/v.mp4"


# ============================================================================
# 进度回调
# ============================================================================


class TestProgressCallback:
    async def test_progress_callback_invoked_per_step(self, agent, monkeypatch):
        """每个步骤开始时回调被触发，含 '开始' 消息。"""
        monkeypatch.setattr(settings, "postprocess_enabled", True)
        _attach_mock_postprocess(agent)

        received: list[tuple[int, str]] = []

        def cb(p: int, m: str) -> None:
            received.append((p, m))

        request = PostprocessRequest(
            scene_id=18,
            video_url="http://mock/v.mp4",
            steps=[PostprocessStep.SUPER_RESOLUTION],
        )
        await agent.execute(request, progress_callback=cb)

        # 应包含步骤开始（0%）和结束（100%）
        messages = [m for _, m in received]
        assert any("开始" in m for m in messages)
        assert any("完成" in m for m in messages)

    async def test_progress_callback_disabled_path(self, agent):
        """总开关关闭时回调被触发一次（100, '后处理已关闭'）。"""
        received: list[tuple[int, str]] = []

        def cb(p: int, m: str) -> None:
            received.append((p, m))

        request = PostprocessRequest(
            scene_id=19,
            video_url="http://mock/v.mp4",
        )
        await agent.execute(request, progress_callback=cb)

        assert len(received) == 1
        assert received[0][0] == 100
        assert "关闭" in received[0][1]


# ============================================================================
# 辅助函数 _parse_resolution / _local_path_from_url
# ============================================================================


class TestParseResolution:
    def test_parse_4k(self):
        assert _parse_resolution("3840x2160") == (3840, 2160)

    def test_parse_1080p(self):
        assert _parse_resolution("1920x1080") == (1920, 1080)

    def test_parse_vertical(self):
        assert _parse_resolution("1080x1920") == (1080, 1920)


class TestLocalPathFromUrl:
    def test_localhost_static_video_returns_path(self, monkeypatch, tmp_path):
        """localhost:port/static/video/x.mp4 → 返回本地路径。"""
        # 创建视频文件
        video_dir = tmp_path / "video"
        video_dir.mkdir()
        video_file = video_dir / "scene.mp4"
        video_file.write_bytes(b"v")

        # OUTPUT_DIR 是 output/postprocess，所以 OUTPUT_DIR.parent = output/
        postprocess_dir = tmp_path / "postprocess"
        postprocess_dir.mkdir()

        from app.agents import postprocess_agent as mod

        monkeypatch.setattr(mod, "OUTPUT_DIR", postprocess_dir)

        url = "http://localhost:8100/static/video/scene.mp4"
        result = mod._local_path_from_url(url)

        assert result is not None
        assert result.name == "scene.mp4"
        assert result.parent == video_dir

    def test_remote_url_returns_none(self):
        """非 localhost URL → 返回 None。"""
        from app.agents.postprocess_agent import _local_path_from_url

        assert _local_path_from_url("http://example.com/v.mp4") is None

    def test_non_static_path_returns_none(self):
        """localhost 但非 /static/ 路径 → 返回 None。"""
        from app.agents.postprocess_agent import _local_path_from_url

        assert _local_path_from_url("http://localhost:8100/api/drama/health") is None

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        """localhost /static/ 但文件不存在 → 返回 None。"""
        postprocess_dir = tmp_path / "postprocess"
        postprocess_dir.mkdir()

        from app.agents import postprocess_agent as mod

        monkeypatch.setattr(mod, "OUTPUT_DIR", postprocess_dir)

        url = "http://localhost:8100/static/video/nonexistent.mp4"
        result = mod._local_path_from_url(url)
        assert result is None
