"""M20 长视频分块续写服务单元测试 —— mock VideoAgent / ffmpeg，不触真实 GPU。

覆盖：
- extract_last_frame / concat_videos / probe_video_duration 的 ffmpeg/ffprobe 命令构造
- LongVideoService.generate 帧链编排：chunk i+1 首帧 = chunk i 末帧（上传 /view URL）
- max_chunks 截断、fail-fast、开关关闭、空 prompt 等边界
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.schemas import AgentResponse
from app.services.long_video_service import (
    LongVideoError,
    LongVideoResult,
    LongVideoService,
    concat_videos,
    extract_last_frame,
    probe_video_duration,
)


@pytest.fixture(autouse=True)
def enable_long_video(monkeypatch):
    """默认开启开关（PoC 默认 False，测试显式打开）。"""
    monkeypatch.setattr(settings, "long_video_enabled", True)
    monkeypatch.setattr(settings, "long_video_max_chunks", 4)
    monkeypatch.setattr(settings, "long_video_chunk_seconds", 5)


def _ok_response(scene_id: int, url: str) -> AgentResponse:
    return AgentResponse(
        success=True,
        data={"scene_id": scene_id, "video_url": url, "duration_seconds": 5},
        elapsed_seconds=1.0,
    )


def _make_agent(n_chunks: int) -> MagicMock:
    """构造 mock VideoAgent：execute 成功、下载 touch 文件、http.post 模拟上传。"""
    agent = MagicMock()

    async def _execute(req, *args, **kwargs):
        return _ok_response(req.scene_id, f"http://w/view?f=chunk_{req.scene_id}.mp4")

    agent.execute = AsyncMock(side_effect=_execute)

    async def _download(url: str, dest: Path) -> Path:
        dest.write_bytes(b"fake-video-bytes")
        return dest

    agent._download_to_file = AsyncMock(side_effect=_download)

    upload_resp = MagicMock()
    upload_resp.raise_for_status = MagicMock()
    upload_resp.json = MagicMock(return_value={"name": "longvideo_chain_00.png"})
    agent.http = MagicMock()
    agent.http.post = AsyncMock(return_value=upload_resp)
    return agent


async def _fake_ffmpeg_touch(cmd: list[str]) -> None:
    """模拟 ffmpeg 成功：touch 输出文件（命令最后一个参数）。"""
    Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
    Path(cmd[-1]).write_bytes(b"fake-out")


# ---------------------------------------------------------------------------
# ffmpeg 工具层
# ---------------------------------------------------------------------------


class TestExtractLastFrame:
    @pytest.mark.asyncio
    async def test_command_and_output(self, tmp_path):
        src, out = tmp_path / "in.mp4", tmp_path / "frame.png"
        src.write_bytes(b"v")
        with patch(
            "app.services.long_video_service._run_ffmpeg",
            new_callable=AsyncMock,
            side_effect=_fake_ffmpeg_touch,
        ) as m_ff:
            result = await extract_last_frame(src, out)

        assert result == out
        cmd = m_ff.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-sseof" in cmd and "-frames:v" in cmd
        assert str(src) in cmd and cmd[-1] == str(out)

    @pytest.mark.asyncio
    async def test_empty_output_raises(self, tmp_path):
        src, out = tmp_path / "in.mp4", tmp_path / "frame.png"
        src.write_bytes(b"v")
        with patch(
            "app.services.long_video_service._run_ffmpeg",
            new_callable=AsyncMock,  # 不产出文件 → 视为失败
        ):
            with pytest.raises(LongVideoError, match="末帧抽取失败"):
                await extract_last_frame(src, out)


class TestConcatVideos:
    @pytest.mark.asyncio
    async def test_concat_demuxer_command(self, tmp_path):
        vids = []
        for i in range(3):
            p = tmp_path / f"c{i}.mp4"
            p.write_bytes(b"v")
            vids.append(p)
        out = tmp_path / "final.mp4"
        with patch(
            "app.services.long_video_service._run_ffmpeg",
            new_callable=AsyncMock,
            side_effect=_fake_ffmpeg_touch,
        ) as m_ff:
            result = await concat_videos(vids, out)

        assert result == out
        cmd = m_ff.call_args[0][0]
        assert "concat" in cmd and "-safe" in cmd
        # concat 清单文件按序列出全部块
        list_file = Path(cmd[cmd.index("-i") + 1])
        lines = [l for l in list_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 3
        assert all(l.startswith("file '") for l in lines)

    @pytest.mark.asyncio
    async def test_empty_list_raises(self, tmp_path):
        with pytest.raises(LongVideoError, match="至少 1 个视频"):
            await concat_videos([], tmp_path / "x.mp4")


class TestProbeVideoDuration:
    @pytest.mark.asyncio
    async def test_parses_ffprobe_output(self, tmp_path):
        v = tmp_path / "v.mp4"
        v.write_bytes(b"v")
        with patch(
            "app.services.long_video_service._run_capture",
            new_callable=AsyncMock,
            return_value="10.040000\n",
        ) as m_probe:
            duration = await probe_video_duration(v)

        assert duration == pytest.approx(10.04)
        cmd = m_probe.call_args[0][0]
        assert cmd[0] == "ffprobe" and str(v) in cmd


# ---------------------------------------------------------------------------
# 帧链编排层
# ---------------------------------------------------------------------------


class TestGenerateChain:
    @pytest.mark.asyncio
    async def test_chains_last_frame_into_next_chunk(self, tmp_path):
        """3 块续写：块 0 用原始首帧，块 i>0 用上一块末帧的 /view URL。"""
        agent = _make_agent(3)
        service = LongVideoService(video_agent=agent, worker_url="http://h3:8195")
        prompts = ["shot A", "shot B", "shot C"]

        with patch(
            "app.services.long_video_service._run_ffmpeg",
            new_callable=AsyncMock,
            side_effect=_fake_ffmpeg_touch,
        ), patch(
            "app.services.long_video_service._run_capture",
            new_callable=AsyncMock,
            return_value="15.0\n",
        ):
            result = await service.generate(
                first_frame_url="http://assets/first.png",
                chunk_prompts=prompts,
                reference_images=["http://assets/char1.png"],
                style="国漫",
                work_dir=tmp_path,
            )

        assert isinstance(result, LongVideoResult)
        assert result.chunks_completed == 3
        assert len(result.chunk_paths) == 3
        assert result.duration_seconds == pytest.approx(15.0)

        # 逐块 VideoRequest 校验
        calls = agent.execute.await_args_list
        assert len(calls) == 3
        req0 = calls[0][0][0]
        assert req0.image_url == "http://assets/first.png"
        assert req0.prompt == "shot A"
        assert req0.duration_seconds == 5
        assert req0.reference_images == ["http://assets/char1.png"]
        assert req0.style == "国漫"
        # 块 1/2 首帧 = 上传后的 /view URL（type=input）
        for i in (1, 2):
            req = calls[i][0][0]
            assert req.image_url.startswith("http://h3:8195/view?filename=")
            assert "type=input" in req.image_url
        # 每块独立 scene_id
        assert [c[0][0].scene_id for c in calls] == [9500, 9501, 9502]
        # 末帧抽取 + 上传各发生 total-1 次
        assert agent.http.post.await_count == 2
        for p in result.chunk_paths:
            assert p.exists()

    @pytest.mark.asyncio
    async def test_respects_max_chunks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "long_video_max_chunks", 2)
        agent = _make_agent(5)
        service = LongVideoService(video_agent=agent, worker_url="http://h3:8195")

        with patch(
            "app.services.long_video_service._run_ffmpeg",
            new_callable=AsyncMock,
            side_effect=_fake_ffmpeg_touch,
        ), patch(
            "app.services.long_video_service._run_capture",
            new_callable=AsyncMock,
            return_value="10.0\n",
        ):
            result = await service.generate(
                first_frame_url="http://assets/first.png",
                chunk_prompts=["a", "b", "c", "d", "e"],
                work_dir=tmp_path,
            )

        assert agent.execute.await_count == 2
        assert result.chunks_completed == 2

    @pytest.mark.asyncio
    async def test_fail_fast_on_chunk_error(self, tmp_path):
        """块 1 失败 → 立即抛错，不拼接部分结果（防断链视频混入流水线）。"""
        agent = _make_agent(3)

        async def _fail_second(req, *args, **kwargs):
            if req.scene_id == 9501:
                return AgentResponse(success=False, error="GPU OOM", elapsed_seconds=1.0)
            return _ok_response(req.scene_id, "http://w/v.mp4")

        agent.execute = AsyncMock(side_effect=_fail_second)
        service = LongVideoService(video_agent=agent, worker_url="http://h3:8195")

        with patch(
            "app.services.long_video_service._run_ffmpeg",
            new_callable=AsyncMock,
            side_effect=_fake_ffmpeg_touch,
        ):
            with pytest.raises(LongVideoError, match="块 2/3 生成失败"):
                await service.generate(
                    first_frame_url="http://assets/first.png",
                    chunk_prompts=["a", "b", "c"],
                    work_dir=tmp_path,
                )

    @pytest.mark.asyncio
    async def test_disabled_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "long_video_enabled", False)
        service = LongVideoService(video_agent=_make_agent(1), worker_url="http://h3:8195")
        with pytest.raises(LongVideoError, match="未启用"):
            await service.generate(
                first_frame_url="http://assets/first.png",
                chunk_prompts=["a"],
                work_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_empty_prompts_raises(self, tmp_path):
        service = LongVideoService(video_agent=_make_agent(1), worker_url="http://h3:8195")
        with pytest.raises(LongVideoError, match="chunk_prompts 为空"):
            await service.generate(
                first_frame_url="http://assets/first.png",
                chunk_prompts=[],
                work_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_progress_callback_reports_chunks(self, tmp_path):
        agent = _make_agent(2)
        service = LongVideoService(video_agent=agent, worker_url="http://h3:8195")
        events: list[tuple[int, str]] = []

        with patch(
            "app.services.long_video_service._run_ffmpeg",
            new_callable=AsyncMock,
            side_effect=_fake_ffmpeg_touch,
        ), patch(
            "app.services.long_video_service._run_capture",
            new_callable=AsyncMock,
            return_value="10.0\n",
        ):
            await service.generate(
                first_frame_url="http://assets/first.png",
                chunk_prompts=["a", "b"],
                work_dir=tmp_path,
                progress_callback=lambda p, m: events.append((p, m)),
            )

        assert any("1/2" in m for _, m in events)
        assert any("2/2" in m for _, m in events)
        assert events[-1][0] == 100
