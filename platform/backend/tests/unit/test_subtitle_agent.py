"""字幕 Agent 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.subtitle_agent import SubtitleAgent, _build_srt, _format_timestamp
from app.config import settings
from app.models.schemas import SubtitleRequest


class TestFormatTimestamp:
    def test_zero(self):
        assert _format_timestamp(0) == "00:00:00,000"

    def test_normal(self):
        assert _format_timestamp(3661.123) == "01:01:01,123"

    def test_negative(self):
        assert _format_timestamp(-1) == "00:00:00,000"


class TestBuildSrt:
    def test_build(self):
        class FakeSeg:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        segments = [FakeSeg(0, 1, "你好"), FakeSeg(1, 2, "世界")]
        srt = _build_srt(segments)
        assert "1" in srt
        assert "00:00:00,000 --> 00:00:01,000" in srt
        assert "你好" in srt
        assert "世界" in srt

    def test_skip_empty_text(self):
        class FakeSeg:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        segments = [FakeSeg(0, 1, "  "), FakeSeg(1, 2, "有效")]
        srt = _build_srt(segments)
        assert "有效" in srt
        assert srt.count("-->") == 1


@pytest.fixture
def agent():
    return SubtitleAgent()


async def _async_bytes_stream(chunks):
    """异步字节迭代器，用于 mock httpx stream 的 aiter_bytes。"""
    for chunk in chunks:
        yield chunk


class TestSubtitleAgentExecute:
    async def test_success_remote_url(self, agent, mock_whisper):
        mock_stream_cm = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = MagicMock(return_value=_async_bytes_stream([b"fake-audio"]))
        mock_stream_cm.__aenter__.return_value = mock_resp
        mock_stream_cm.__aexit__.return_value = None

        with patch("httpx.AsyncClient.stream", return_value=mock_stream_cm):
            request = SubtitleRequest(
                scene_id=1,
                audio_url="http://example.com/audio.mp3",
                language="zh",
            )
            response = await agent.execute(request)

        assert response.success is True
        assert response.data["scene_id"] == 1
        assert "测试字幕" in response.data["srt_content"]
        assert response.data["language"] == "zh"

    async def test_success_local_url(self, agent, mock_whisper, tmp_path):
        # 构造本地音频目录和文件
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        audio_file = audio_dir / "test.mp3"
        audio_file.write_bytes(b"fake")

        with patch.object(
            agent, "_download_audio", return_value=str(audio_file)
        ):
            request = SubtitleRequest(
                scene_id=2,
                audio_url="http://localhost:8100/static/audio/test.mp3",
                language="zh",
            )
            response = await agent.execute(request)

        assert response.success is True
        assert response.data["scene_id"] == 2

    async def test_exception_returns_error(self, agent, mock_whisper):
        mock_stream_cm = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock(side_effect=RuntimeError("下载失败"))
        mock_stream_cm.__aenter__.return_value = mock_resp
        mock_stream_cm.__aexit__.return_value = None

        with patch("httpx.AsyncClient.stream", return_value=mock_stream_cm):
            request = SubtitleRequest(
                scene_id=1,
                audio_url="http://example.com/audio.mp3",
                language="zh",
            )
            response = await agent.execute(request)

        assert response.success is False
        assert "下载失败" in response.error


# ============================================================================
# P4.2: 双 ASR 后端派发与回退测试
# ============================================================================


class TestSubtitleAgentDualBackend:
    """P4.2: FireRedASR 主后端 + faster-whisper 回退。

    conftest._patch_settings 默认将 asr_backend 设为 'whisper'，
    本测试类通过 monkeypatch 局部覆盖为 'firered'。
    """

    def _attach_mock_asr(self, agent, return_value=None, side_effect=None):
        """将 mock ASRService 注入 agent._asr，绕过懒加载。"""
        mock_svc = MagicMock()
        mock_svc.transcribe = AsyncMock()
        if side_effect is not None:
            mock_svc.transcribe.side_effect = side_effect
        else:
            mock_svc.transcribe.return_value = return_value or {
                "text": "FireRedASR 测试字幕",
                "segments": [
                    {"start": 0.0, "end": 1.5, "text": "FireRedASR 测试"},
                    {"start": 1.5, "end": 3.0, "text": "字幕"},
                ],
                "language": "zh",
                "duration": 3.0,
            }
        agent._asr = mock_svc
        return mock_svc

    def _mock_local_audio(self, agent, tmp_path, filename="audio.mp3"):
        """Mock _download_audio 返回本地临时音频路径。"""
        audio_file = tmp_path / filename
        audio_file.write_bytes(b"fake-audio")
        return patch.object(
            agent, "_download_audio", return_value=str(audio_file)
        )

    async def test_firered_backend_success(self, agent, monkeypatch, tmp_path):
        """asr_backend='firered' → 走 FireRedASR 主路径，segments 为字典格式。"""
        monkeypatch.setattr(settings, "asr_backend", "firered")
        mock_svc = self._attach_mock_asr(agent)

        with self._mock_local_audio(agent, tmp_path, filename="test.mp3"):
            # 禁用 AI 优化（避免依赖 LLM 调用）
            with patch(
                "app.agents.subtitle_agent.optimize_content",
                new_callable=AsyncMock,
                side_effect=Exception("LLM skip"),
            ):
                request = SubtitleRequest(
                    scene_id=1,
                    audio_url="http://localhost:8100/static/audio/test.mp3",
                    language="zh",
                )
                resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["scene_id"] == 1
        assert resp.data["language"] == "zh"
        # SRT 包含 FireRedASR 转写文本
        assert "FireRedASR" in resp.data["srt_content"]
        assert "字幕" in resp.data["srt_content"]
        # segments 数量与 mock 一致
        assert len(resp.data["segments"]) == 2
        # ASRService.transcribe 被调用一次，参数包含音频字节和文件名
        mock_svc.transcribe.assert_awaited_once()
        call_args = mock_svc.transcribe.call_args
        # args[0] 是 audio_bytes
        assert call_args.args[0] == b"fake-audio"
        # kwargs 包含 filename 和 language
        assert call_args.kwargs["language"] == "zh"
        assert call_args.kwargs["filename"] == "test.mp3"

    async def test_firered_failure_fallback_to_whisper(
        self, agent, monkeypatch, tmp_path, mock_whisper
    ):
        """FireRedASR 抛异常 → 自动回退到 faster-whisper 并成功。"""
        monkeypatch.setattr(settings, "asr_backend", "firered")
        self._attach_mock_asr(agent, side_effect=RuntimeError("FireRedASR OOM"))

        with self._mock_local_audio(agent, tmp_path):
            with patch(
                "app.agents.subtitle_agent.optimize_content",
                new_callable=AsyncMock,
                side_effect=Exception("LLM skip"),
            ):
                request = SubtitleRequest(
                    scene_id=2,
                    audio_url="http://localhost:8100/static/audio/test.mp3",
                    language="zh",
                )
                resp = await agent.execute(request)

        assert resp.success is True
        # 回退到 whisper，字幕来自 mock_whisper 的 "测试字幕"
        assert "测试字幕" in resp.data["srt_content"]
        # ASRService.transcribe 被调用一次（失败）
        assert agent._asr.transcribe.await_count == 1

    async def test_whisper_backend_skips_firered(
        self, agent, mock_whisper, tmp_path
    ):
        """asr_backend='whisper'（conftest 默认）→ 不调用 FireRedASR。"""
        # 即使注入 mock ASR，也不应被调用
        mock_svc = MagicMock()
        mock_svc.transcribe = AsyncMock()
        agent._asr = mock_svc

        with self._mock_local_audio(agent, tmp_path):
            with patch(
                "app.agents.subtitle_agent.optimize_content",
                new_callable=AsyncMock,
                side_effect=Exception("LLM skip"),
            ):
                request = SubtitleRequest(
                    scene_id=3,
                    audio_url="http://localhost:8100/static/audio/test.mp3",
                    language="zh",
                )
                resp = await agent.execute(request)

        assert resp.success is True
        # 字幕来自 faster-whisper mock
        assert "测试字幕" in resp.data["srt_content"]
        # FireRedASR 未被调用
        mock_svc.transcribe.assert_not_awaited()

    async def test_firered_segments_dict_format(
        self, agent, monkeypatch, tmp_path
    ):
        """FireRedASR 返回的字典 segments 与 _build_srt 兼容（start/end/text 键）。"""
        monkeypatch.setattr(settings, "asr_backend", "firered")
        self._attach_mock_asr(
            agent,
            return_value={
                "text": "完整文本",
                "segments": [
                    {"start": 0.5, "end": 2.0, "text": "第一段"},
                    {"start": 2.0, "end": 4.0, "text": "第二段"},
                ],
                "language": "zh",
                "duration": 4.0,
            },
        )

        with self._mock_local_audio(agent, tmp_path):
            with patch(
                "app.agents.subtitle_agent.optimize_content",
                new_callable=AsyncMock,
                side_effect=Exception("LLM skip"),
            ):
                request = SubtitleRequest(
                    scene_id=4,
                    audio_url="http://localhost:8100/static/audio/test.mp3",
                    language="zh",
                )
                resp = await agent.execute(request)

        assert resp.success is True
        srt = resp.data["srt_content"]
        # 字典格式 segments 正确转换为 SRT
        assert "第一段" in srt
        assert "第二段" in srt
        # 时间戳格式正确
        assert "00:00:00,500 --> 00:00:02,000" in srt
        assert "00:00:02,000 --> 00:00:04,000" in srt

    async def test_firered_auto_language_passes_zh(
        self, agent, monkeypatch, tmp_path
    ):
        """language='auto' 时传给 FireRedASR 的是 'zh'（避免服务端处理 auto）。"""
        monkeypatch.setattr(settings, "asr_backend", "firered")
        mock_svc = self._attach_mock_asr(agent)

        with self._mock_local_audio(agent, tmp_path):
            with patch(
                "app.agents.subtitle_agent.optimize_content",
                new_callable=AsyncMock,
                side_effect=Exception("LLM skip"),
            ):
                request = SubtitleRequest(
                    scene_id=5,
                    audio_url="http://localhost:8100/static/audio/test.mp3",
                    language="auto",
                )
                resp = await agent.execute(request)

        assert resp.success is True
        call_kwargs = mock_svc.transcribe.call_args.kwargs
        # language='auto' 被转换为 'zh' 传给 FireRedASR
        assert call_kwargs["language"] == "zh"

    async def test_firered_empty_segments(
        self, agent, monkeypatch, tmp_path
    ):
        """FireRedASR 返回空 segments 时仍能生成有效 SRT（空内容）。"""
        monkeypatch.setattr(settings, "asr_backend", "firered")
        self._attach_mock_asr(
            agent,
            return_value={
                "text": "",
                "segments": [],
                "language": "zh",
                "duration": 0.0,
            },
        )

        with self._mock_local_audio(agent, tmp_path):
            with patch(
                "app.agents.subtitle_agent.optimize_content",
                new_callable=AsyncMock,
                side_effect=Exception("LLM skip"),
            ):
                request = SubtitleRequest(
                    scene_id=6,
                    audio_url="http://localhost:8100/static/audio/test.mp3",
                    language="zh",
                )
                resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["segments"] == []

    async def test_ai_omni_backend_success(self, agent, monkeypatch, tmp_path):
        """asr_backend='ai_omni' → 调用 AI-Omni ASR 端点并生成 SRT。"""
        monkeypatch.setattr(settings, "asr_backend", "ai_omni")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "text": "AI Omni 测试字幕",
            "language": "zh",
            "duration": 2.5,
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "AI Omni 测试"},
                {"start": 1.2, "end": 2.5, "text": "字幕"},
            ],
        }

        with self._mock_local_audio(agent, tmp_path, filename="test.mp3"):
            with patch.object(
                agent, "http", new=MagicMock(post=AsyncMock(return_value=mock_resp))
            ):
                with patch(
                    "app.agents.subtitle_agent.optimize_content",
                    new_callable=AsyncMock,
                    side_effect=Exception("LLM skip"),
                ):
                    request = SubtitleRequest(
                        scene_id=7,
                        audio_url="http://localhost:8100/static/audio/test.mp3",
                        language="zh",
                    )
                    resp = await agent.execute(request)

        assert resp.success is True
        assert "AI Omni 测试" in resp.data["srt_content"]
        assert "字幕" in resp.data["srt_content"]
        assert len(resp.data["segments"]) == 2

    async def test_ai_omni_failure_fallback_to_whisper(
        self, agent, monkeypatch, tmp_path, mock_whisper
    ):
        """AI-Omni ASR 抛异常 → 自动回退到 faster-whisper 并成功。"""
        monkeypatch.setattr(settings, "asr_backend", "ai_omni")

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=RuntimeError("AI-Omni 422")
        )

        with self._mock_local_audio(agent, tmp_path, filename="test.mp3"):
            with patch.object(
                agent, "http", new=MagicMock(post=AsyncMock(return_value=mock_resp))
            ):
                with patch(
                    "app.agents.subtitle_agent.optimize_content",
                    new_callable=AsyncMock,
                    side_effect=Exception("LLM skip"),
                ):
                    request = SubtitleRequest(
                        scene_id=8,
                        audio_url="http://localhost:8100/static/audio/test.mp3",
                        language="zh",
                    )
                    resp = await agent.execute(request)

        assert resp.success is True
        assert "测试字幕" in resp.data["srt_content"]
