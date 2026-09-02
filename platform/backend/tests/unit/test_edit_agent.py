"""剪辑 Agent 单元测试 —— 使用 mock FFmpeg/下载，避免真实音视频处理。"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.edit_agent import (
    EditAgent,
    _local_path_from_url,
    _parse_resolution,
    compute_ambience_gain,
)
from app.config import settings
from app.models.schemas import EditRequest, EditSegment


class TestParseResolution:
    def test_1080x1920(self):
        assert _parse_resolution("1080x1920") == (1080, 1920)

    def test_720p(self):
        assert _parse_resolution("1280x720") == (1280, 720)


class TestEditSegmentSchema:
    def test_subtitle_url_optional(self):
        """Studio 无字幕时可省略 subtitle_url，仍能构造 EditSegment。"""
        seg = EditSegment.model_validate(
            {
                "scene_id": 1,
                "video_url": "http://example.com/v1.mp4",
                "audio_url": "http://example.com/a1.mp3",
            }
        )
        assert seg.subtitle_url == ""


class TestLocalPathFromUrl:
    def test_local_audio(self, tmp_path):
        # OUTPUT_DIR 预期为 .../output/video，其父目录是 .../output
        output_video_dir = tmp_path / "output" / "video"
        output_video_dir.mkdir(parents=True)
        audio_dir = tmp_path / "output" / "audio"
        audio_dir.mkdir()
        audio_file = audio_dir / "test.mp3"
        audio_file.write_text("fake")

        with patch("app.agents.edit_agent.OUTPUT_DIR", output_video_dir):
            result = _local_path_from_url(
                "http://localhost:8100/static/audio/test.mp3", output_video_dir
            )
        assert result == audio_file

    def test_remote_url(self):
        assert _local_path_from_url("http://example.com/x.mp4", Path("/tmp")) is None

    def test_missing_file(self, tmp_path):
        output_video_dir = tmp_path / "output" / "video"
        output_video_dir.mkdir(parents=True)
        with patch("app.agents.edit_agent.OUTPUT_DIR", output_video_dir):
            result = _local_path_from_url(
                "http://localhost:8100/static/audio/nope.mp3", output_video_dir
            )
        assert result is None


@pytest.fixture
def agent():
    return EditAgent()


@pytest.fixture
def mock_ffmpeg_success():
    """Mock ffmpeg 和 ffprobe 都成功返回；ffmpeg 调用会 touch 输出文件。"""
    with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.returncode = 0

        async def fake_communicate():
            # 根据命令最后一个参数推断输出文件并创建空文件
            call_args = mock_exec.call_args
            if call_args and call_args.args:
                output_path = Path(call_args.args[-1])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"")
            return b"", b""

        proc.communicate.side_effect = fake_communicate
        mock_exec.return_value = proc
        yield mock_exec


async def _async_bytes_stream(chunks):
    for chunk in chunks:
        yield chunk


@pytest.fixture
def mock_httpx_download():
    """Mock httpx 下载，返回固定字节文件。"""
    mock_stream_cm = AsyncMock()
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_bytes = MagicMock(return_value=_async_bytes_stream([b"fake-downloaded-bytes"]))
    mock_stream_cm.__aenter__.return_value = mock_resp
    mock_stream_cm.__aexit__.return_value = None

    with patch("httpx.AsyncClient.stream", return_value=mock_stream_cm):
        yield


class TestEditAgentExecute:
    async def test_success_two_segments(
        self, agent, mock_ffmpeg_success, mock_httpx_download, tmp_path
    ):
        with patch("app.agents.edit_agent.OUTPUT_DIR", tmp_path / "output" / "video"):
            output_dir = tmp_path / "output" / "video"
            output_dir.mkdir(parents=True, exist_ok=True)

            request = EditRequest(
                project_id="test-001",
                title="测试成片",
                segments=[
                    EditSegment(
                        scene_id=1,
                        video_url="http://example.com/v1.mp4",
                        audio_url="http://example.com/a1.mp3",
                        subtitle_url="http://example.com/s1.srt",
                    ),
                    EditSegment(
                        scene_id=2,
                        video_url="http://example.com/v2.mp4",
                        audio_url="http://example.com/a2.mp3",
                        subtitle_url="http://example.com/s2.srt",
                    ),
                ],
            )
            response = await agent.execute(request)

        assert response.success is True
        assert response.data["project_id"] == "test-001"
        assert response.data["title"] == "测试成片"
        assert response.data["segments_count"] == 2
        assert "/static/video/" in response.data["final_video_url"]

    async def test_success_with_bgm(
        self, agent, mock_ffmpeg_success, mock_httpx_download, tmp_path
    ):
        with patch("app.agents.edit_agent.OUTPUT_DIR", tmp_path / "output" / "video"):
            output_dir = tmp_path / "output" / "video"
            output_dir.mkdir(parents=True, exist_ok=True)

            request = EditRequest(
                project_id="test-002",
                title="带 BGM 成片",
                segments=[
                    EditSegment(
                        scene_id=1,
                        video_url="http://example.com/v1.mp4",
                        audio_url="http://example.com/a1.mp3",
                        subtitle_url="http://example.com/s1.srt",
                    ),
                ],
                bgm_url="http://example.com/bgm.mp3",
            )
            response = await agent.execute(request)

        assert response.success is True
        assert response.data["segments_count"] == 1

    async def test_success_with_fade_transition(
        self, agent, mock_ffmpeg_success, mock_httpx_download, tmp_path
    ):
        with patch("app.agents.edit_agent.OUTPUT_DIR", tmp_path / "output" / "video"):
            output_dir = tmp_path / "output" / "video"
            output_dir.mkdir(parents=True, exist_ok=True)

            request = EditRequest(
                project_id="test-003",
                title="淡入淡出成片",
                segments=[
                    EditSegment(
                        scene_id=1,
                        video_url="http://example.com/v1.mp4",
                        audio_url="http://example.com/a1.mp3",
                        subtitle_url="http://example.com/s1.srt",
                    ),
                ],
                transition="fade",
            )
            response = await agent.execute(request)

        assert response.success is True

    async def test_empty_segments_returns_error(self, agent):
        request = EditRequest(project_id="empty", segments=[])
        response = await agent.execute(request)

        assert response.success is False
        assert "剪辑合成失败" in response.error

    async def test_ffmpeg_failure_returns_error(
        self, agent, mock_httpx_download, tmp_path
    ):
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.returncode = 1
            proc.communicate.return_value = (b"", b"some ffmpeg error")
            mock_exec.return_value = proc

            with patch("app.agents.edit_agent.OUTPUT_DIR", tmp_path / "output" / "video"):
                output_dir = tmp_path / "output" / "video"
                output_dir.mkdir(parents=True, exist_ok=True)

                request = EditRequest(
                    project_id="fail",
                    segments=[
                        EditSegment(
                            scene_id=1,
                            video_url="http://example.com/v1.mp4",
                            audio_url="http://example.com/a1.mp3",
                            subtitle_url="http://example.com/s1.srt",
                        ),
                    ],
                )
                response = await agent.execute(request)

        assert response.success is False
        assert "FFmpeg failed" in response.error


# ---------------------------------------------------------------------------
# H3 原生音轨混音（环境音垫底 + 人声为主）
# ---------------------------------------------------------------------------


def _make_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    """构造 fake 子进程句柄。"""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


def _make_side_effect(*, has_audio: bool, fail_filter_complex: bool = False):
    """按命令内容分发 ffprobe/ffmpeg 结果。

    - 音频流探测（-select_streams a:0）：按 has_audio 返回是否有音轨
    - 时长探测（format=duration）：固定返回 5.0
    - ffmpeg：touch 输出文件；fail_filter_complex 时对混音调用返回失败
    """

    def side_effect(*cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "-select_streams" in cmd_str:
            return _make_proc(0, b"audio\n" if has_audio else b"")
        if "format=duration" in cmd_str:
            return _make_proc(0, b"5.0\n")
        # ffmpeg：按最后一个参数 touch 输出文件（与 mock_ffmpeg_success 行为一致）
        output_path = Path(cmd[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        if fail_filter_complex and "-filter_complex" in cmd_str:
            return _make_proc(1, b"", b"amix: invalid stream specifier")
        return _make_proc(0)

    return side_effect


def _ffmpeg_calls(mock_exec) -> list:
    """筛出 ffmpeg 调用（排除 ffprobe 探测调用）。"""
    return [
        c
        for c in mock_exec.call_args_list
        if c.args and not Path(str(c.args[0])).name.startswith("ffprobe")
    ]


def _ffprobe_audio_probe_calls(mock_exec) -> list:
    """筛出「音频流探测」的 ffprobe 调用。"""
    return [
        c for c in mock_exec.call_args_list if "-select_streams" in " ".join(map(str, c.args))
    ]


def _segment(
    scene_id: int = 1,
    audio_url: str = "http://example.com/a1.mp3",
    audio_type: str = "narration",
) -> EditSegment:
    return EditSegment(
        scene_id=scene_id,
        video_url=f"http://example.com/v{scene_id}.mp4",
        audio_url=audio_url,
        audio_type=audio_type,
        subtitle_url=f"http://example.com/s{scene_id}.srt",
    )


class TestH3NativeAudioMix:
    """H3 原生音轨（环境音）与人声混音 — _process_segment 级单元测试。"""

    def test_config_defaults(self):
        """新增配置项默认值：开启原生音轨保留，环境音增益 0.25（约 -12dB）。"""
        assert settings.h3_native_audio_enabled is True
        assert settings.h3_ambience_gain == 0.25

    async def test_mix_when_video_has_audio_and_voice(
        self, agent, mock_httpx_download, tmp_path
    ):
        """有音轨 + 有人声：走 amix 混音，人声 1.0、环境音按动态增益垫底。

        _make_side_effect 时长探测固定返回 5.0/5.0 → 对白密度 100% → M12.2 dense 档。
        """
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = _make_side_effect(has_audio=True)
            out = await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        assert out.name == "segment_final.mp4"
        ffmpeg_cmds = _ffmpeg_calls(mock_exec)
        assert len(ffmpeg_cmds) == 1
        args = list(ffmpeg_cmds[0].args)
        cmd_str = " ".join(map(str, args))
        assert "-filter_complex" in cmd_str
        # 人声为主（1.0），环境音按对白密度动态增益（5.0/5.0 → dense 档）
        assert "[1:a]volume=1.0" in cmd_str
        assert f"volume={settings.h3_ambience_gain_dense}" in cmd_str
        # amix 双输入；duration=longest 让环境音铺满整个镜头
        assert "amix=inputs=2:duration=longest" in cmd_str
        # 混音输出被显式映射
        assert "[aout]" in args

    async def test_dialogue_keeps_h3_native_skips_tts_mix(
        self, agent, mock_httpx_download, tmp_path
    ):
        """dialogue + H3 原生音轨：不叠 IndexTTS，单输入保留原声。"""
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = _make_side_effect(has_audio=True)
            await agent._process_segment(
                _segment(audio_type="dialogue"), tmp_path, 1080, 1920, 24
            )

        ffmpeg_cmds = _ffmpeg_calls(mock_exec)
        assert len(ffmpeg_cmds) == 1
        args = list(ffmpeg_cmds[0].args)
        cmd_str = " ".join(map(str, args))
        assert "-filter_complex" not in cmd_str
        assert "amix" not in cmd_str
        assert args.count("-i") == 1

    async def test_skip_mix_when_video_has_no_audio_stream(
        self, agent, mock_httpx_download, tmp_path
    ):
        """无音轨（回退后端视频）：跳过混音，保持原「人声替换」命令。"""
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = _make_side_effect(has_audio=False)
            await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        ffmpeg_cmds = _ffmpeg_calls(mock_exec)
        assert len(ffmpeg_cmds) == 1
        args = list(ffmpeg_cmds[0].args)
        cmd_str = " ".join(map(str, args))
        assert "-filter_complex" not in cmd_str
        assert args.count("-i") == 2  # 视频 + 人声两个输入

    async def test_keep_native_audio_when_no_voice(
        self, agent, mock_httpx_download, tmp_path
    ):
        """无人声（纯场景镜头）：单输入直出，原音轨随视频保留，且无需探测音轨。"""
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = _make_side_effect(has_audio=True)
            await agent._process_segment(
                _segment(audio_url=""), tmp_path, 1080, 1920, 24
            )

        ffmpeg_cmds = _ffmpeg_calls(mock_exec)
        assert len(ffmpeg_cmds) == 1
        args = list(ffmpeg_cmds[0].args)
        cmd_str = " ".join(map(str, args))
        assert args.count("-i") == 1  # 仅视频输入，无人声文件
        assert "-filter_complex" not in cmd_str
        assert "-an" not in args  # 未禁音：默认映射保留原音轨
        # 无人声时无需探测音轨
        assert _ffprobe_audio_probe_calls(mock_exec) == []

    async def test_skip_subtitles_when_url_empty(
        self, agent, mock_httpx_download, tmp_path
    ):
        """无字幕 URL：不下载 SRT、不烧 subtitles 滤镜，仍能处理片段。"""
        downloaded: list[str] = []
        orig = agent._download

        async def spy(url, dest):
            downloaded.append(url)
            return await orig(url, dest)

        with patch.object(agent, "_download", side_effect=spy):
            with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
                mock_exec.side_effect = _make_side_effect(has_audio=True)
                await agent._process_segment(
                    EditSegment(
                        scene_id=1,
                        video_url="http://example.com/v1.mp4",
                        audio_url="http://example.com/a1.mp3",
                        subtitle_url="",
                    ),
                    tmp_path,
                    1080,
                    1920,
                    24,
                )

        ffmpeg_cmds = _ffmpeg_calls(mock_exec)
        assert len(ffmpeg_cmds) == 1
        cmd_str = " ".join(map(str, ffmpeg_cmds[0].args))
        assert "subtitles=" not in cmd_str
        assert "http://example.com/v1.mp4" in downloaded
        assert all(".srt" not in url for url in downloaded)

    async def test_fallback_to_voice_only_when_mix_fails(
        self, agent, mock_httpx_download, tmp_path, caplog
    ):
        """混音 ffmpeg 失败：优雅降级为纯人声重试，记 warning，不抛错。"""
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = _make_side_effect(has_audio=True, fail_filter_complex=True)
            with caplog.at_level(logging.WARNING, logger="app.agents.edit_agent"):
                out = await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        assert out.name == "segment_final.mp4"  # 未抛异常
        ffmpeg_cmds = _ffmpeg_calls(mock_exec)
        assert len(ffmpeg_cmds) == 2  # 混音尝试 + 降级重试
        first, second = (" ".join(map(str, c.args)) for c in ffmpeg_cmds)
        assert "-filter_complex" in first  # 第一次是混音
        assert "-filter_complex" not in second  # 降级为纯人声
        assert any("混音失败" in r.message for r in caplog.records)

    async def test_mix_disabled_by_config(
        self, agent, mock_httpx_download, tmp_path, monkeypatch
    ):
        """h3_native_audio_enabled=False：即使有音轨也不混音、不探测。"""
        monkeypatch.setattr(settings, "h3_native_audio_enabled", False)
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = _make_side_effect(has_audio=True)
            await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        ffmpeg_cmds = _ffmpeg_calls(mock_exec)
        assert len(ffmpeg_cmds) == 1
        assert "-filter_complex" not in " ".join(map(str, ffmpeg_cmds[0].args))
        assert _ffprobe_audio_probe_calls(mock_exec) == []

    async def test_ambience_gain_from_config(
        self, agent, mock_httpx_download, tmp_path, monkeypatch
    ):
        """环境音增益可由 settings.h3_ambience_gain 调整（关闭动态增益走恒定路径）。"""
        monkeypatch.setattr(settings, "h3_dynamic_gain_enabled", False)
        monkeypatch.setattr(settings, "h3_ambience_gain", 0.4)
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = _make_side_effect(has_audio=True)
            await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        cmd_str = " ".join(map(str, _ffmpeg_calls(mock_exec)[0].args))
        assert "volume=0.4" in cmd_str


class TestDynamicAmbienceGain:
    """M12.2：按对白密度动态调整 H3 环境音增益（compute_ambience_gain 纯函数 + 接线）。"""

    def test_config_defaults(self):
        """新增配置默认值：动态增益开启，密集 0.15 / 稀疏 0.40。"""
        assert settings.h3_dynamic_gain_enabled is True
        assert settings.h3_ambience_gain_dense == 0.15
        assert settings.h3_ambience_gain_sparse == 0.40

    def test_dense_dialogue_lowers_gain(self):
        """人声占视频 ≥85%（对白密集）→ 增益压到 dense 档，避免环境音盖人声。"""
        assert compute_ambience_gain(4.5, 5.0) == settings.h3_ambience_gain_dense
        assert compute_ambience_gain(5.0, 5.0) == settings.h3_ambience_gain_dense

    def test_medium_density_keeps_default(self):
        """人声占比 40%-85% → 维持基准增益 0.25。"""
        assert compute_ambience_gain(2.5, 5.0) == settings.h3_ambience_gain
        assert compute_ambience_gain(2.0, 5.0) == settings.h3_ambience_gain

    def test_sparse_dialogue_raises_gain(self):
        """人声占比 <40%（大量留白）→ 提升环境音增益营造氛围。"""
        assert compute_ambience_gain(1.0, 5.0) == settings.h3_ambience_gain_sparse
        assert compute_ambience_gain(0.5, 5.0) == settings.h3_ambience_gain_sparse

    def test_invalid_duration_falls_back_to_default(self):
        """探测失败（0/负值）→ 回退基准增益，主链路不中断。"""
        assert compute_ambience_gain(0, 5.0) == settings.h3_ambience_gain
        assert compute_ambience_gain(3.0, 0) == settings.h3_ambience_gain
        assert compute_ambience_gain(-1, 5.0) == settings.h3_ambience_gain

    @staticmethod
    def _duration_side_effect(*, voice: float, video: float, has_audio: bool = True):
        """按时长探测目标文件后缀返回不同时长（.mp3=人声 / .mp4=视频）。"""

        def side_effect(*cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "-select_streams" in cmd_str:
                return _make_proc(0, b"audio\n" if has_audio else b"")
            if "format=duration" in cmd_str:
                target = str(cmd[-1])
                value = voice if target.endswith(".mp3") else video
                return _make_proc(0, f"{value}\n".encode())
            output_path = Path(cmd[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"")
            return _make_proc(0)

        return side_effect

    async def test_dense_dialogue_mix_uses_dense_gain(
        self, agent, mock_httpx_download, tmp_path
    ):
        """接线：人声 4.5s / 视频 5.0s（密度 90%）→ 混音命令用 dense 增益。"""
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = self._duration_side_effect(voice=4.5, video=5.0)
            await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        cmd_str = " ".join(map(str, _ffmpeg_calls(mock_exec)[0].args))
        assert f"volume={settings.h3_ambience_gain_dense}" in cmd_str

    async def test_sparse_dialogue_mix_uses_sparse_gain(
        self, agent, mock_httpx_download, tmp_path
    ):
        """接线：人声 1.0s / 视频 5.0s（密度 20%）→ 混音命令用 sparse 增益。"""
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = self._duration_side_effect(voice=1.0, video=5.0)
            await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        cmd_str = " ".join(map(str, _ffmpeg_calls(mock_exec)[0].args))
        assert f"volume={settings.h3_ambience_gain_sparse}" in cmd_str

    async def test_dynamic_gain_disabled_uses_constant(
        self, agent, mock_httpx_download, tmp_path, monkeypatch
    ):
        """h3_dynamic_gain_enabled=False → 恒定基准增益，且不做人声时长探测。"""
        monkeypatch.setattr(settings, "h3_dynamic_gain_enabled", False)
        with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = self._duration_side_effect(voice=4.5, video=5.0)
            await agent._process_segment(_segment(), tmp_path, 1080, 1920, 24)

        cmd_str = " ".join(map(str, _ffmpeg_calls(mock_exec)[0].args))
        assert f"volume={settings.h3_ambience_gain}" in cmd_str


class TestEditAgentH3MixWiring:
    """接线测试：execute 全流程中混音被正确触发/跳过。"""

    async def test_execute_mixes_native_audio_per_segment(
        self, agent, mock_httpx_download, tmp_path
    ):
        """execute 处理带音轨视频时，片段级命令包含 amix 混音。"""
        with patch("app.agents.edit_agent.OUTPUT_DIR", tmp_path / "output" / "video"):
            (tmp_path / "output" / "video").mkdir(parents=True, exist_ok=True)
            with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
                mock_exec.side_effect = _make_side_effect(has_audio=True)
                request = EditRequest(project_id="h3-001", segments=[_segment()])
                response = await agent.execute(request)

        assert response.success is True
        seg_cmds = [
            c for c in _ffmpeg_calls(mock_exec) if "segment_final" in str(c.args[-1])
        ]
        assert len(seg_cmds) == 1
        assert "amix=inputs=2:duration=longest" in " ".join(map(str, seg_cmds[0].args))

    async def test_execute_skips_mix_for_silent_video(
        self, agent, mock_httpx_download, tmp_path
    ):
        """execute 处理无音轨视频（回退后端）时，片段级命令不含混音。"""
        with patch("app.agents.edit_agent.OUTPUT_DIR", tmp_path / "output" / "video"):
            (tmp_path / "output" / "video").mkdir(parents=True, exist_ok=True)
            with patch("app.agents.edit_agent.asyncio.create_subprocess_exec") as mock_exec:
                mock_exec.side_effect = _make_side_effect(has_audio=False)
                request = EditRequest(project_id="h3-002", segments=[_segment()])
                response = await agent.execute(request)

        assert response.success is True
        seg_cmds = [
            c for c in _ffmpeg_calls(mock_exec) if "segment_final" in str(c.args[-1])
        ]
        assert len(seg_cmds) == 1
        assert "-filter_complex" not in " ".join(map(str, seg_cmds[0].args))


