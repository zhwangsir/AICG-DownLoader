"""剪辑 Agent 单元测试 —— 使用 mock FFmpeg/下载，避免真实音视频处理。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.edit_agent import EditAgent, _local_path_from_url, _parse_resolution
from app.models.schemas import EditRequest, EditSegment


class TestParseResolution:
    def test_1080x1920(self):
        assert _parse_resolution("1080x1920") == (1080, 1920)

    def test_720p(self):
        assert _parse_resolution("1280x720") == (1280, 720)


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


