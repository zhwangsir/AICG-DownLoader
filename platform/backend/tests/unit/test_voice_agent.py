"""配音 Agent 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.voice_agent import VoiceAgent, _parse_rate, select_voice
from app.config import settings
from app.models.schemas import DialogueLine, VoiceRequest


class TestSelectVoice:
    def test_protagonist_young(self):
        voice = select_voice("林远", "主角", 26, 0)
        assert voice == "zh-CN-YunxiNeural"

    def test_protagonist_middle_age(self):
        voice = select_voice("林远", "主角", 40, 0)
        assert voice == "zh-CN-YunyangNeural"

    def test_antagonist(self):
        voice = select_voice("赵恒", "反派", 35, 0)
        assert voice == "zh-CN-YunjianNeural"

    def test_female(self):
        voice = select_voice("小红", "女主角", 25, 0)
        assert voice == "zh-CN-XiaoxiaoNeural"

    def test_default_rotation(self):
        voice1 = select_voice("路人甲", "群演", None, 0)
        voice2 = select_voice("路人乙", "群演", None, 1)
        assert voice1 != voice2


@pytest.fixture
def agent():
    return VoiceAgent()


class TestVoiceAgentExecute:
    async def test_success(self, agent, mock_edge_tts):
        request = VoiceRequest(
            scene_id=1,
            dialogues=[
                DialogueLine(text="你好", character_name="林远", character_role="主角"),
                DialogueLine(text="再见", character_name="小红", character_role="女主角"),
            ],
        )
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["scene_id"] == 1
        assert response.data["total_lines"] == 2
        assert len(response.data["audio_urls"]) == 2

    async def test_empty_dialogues(self, agent, mock_edge_tts):
        request = VoiceRequest(scene_id=1, dialogues=[])
        response = await agent.execute(request)

        assert response.success is True
        assert response.data["total_lines"] == 0

    async def test_exception_returns_error(self, agent, mock_edge_tts):
        mock_edge_tts.side_effect = RuntimeError("TTS 失败")

        request = VoiceRequest(
            scene_id=1,
            dialogues=[DialogueLine(text="你好")],
        )
        response = await agent.execute(request)

        assert response.success is False
        assert "TTS 失败" in response.error


# ============================================================================
# _parse_rate 辅助函数测试
# ============================================================================


class TestParseRate:
    """P4.2: edge-tts 语速格式 → float 转换。"""

    def test_zero(self):
        assert _parse_rate("+0%") == 1.0

    def test_empty_returns_default(self):
        assert _parse_rate("") == 1.0

    def test_positive_rate(self):
        assert _parse_rate("+10%") == 1.1
        assert _parse_rate("+20%") == 1.2

    def test_negative_rate(self):
        assert _parse_rate("-10%") == 0.9
        assert _parse_rate("-20%") == 0.8

    def test_clamped_to_range(self):
        """超出 0.5-2.0 范围时被钳制。"""
        assert _parse_rate("+200%") == 2.0  # 上限
        assert _parse_rate("-90%") == 0.5   # 下限

    def test_invalid_format_returns_default(self):
        """非法格式回退到默认 1.0。"""
        assert _parse_rate("invalid") == 1.0
        assert _parse_rate(None) == 1.0


# ============================================================================
# P4.2: 双 TTS 后端派发与回退测试
# ============================================================================


class TestVoiceAgentDualBackend:
    """P4.2: CosyVoice / IndexTTS 主后端 + edge-tts 回退。

    conftest._patch_settings 默认将 tts_backend 设为 'edge'，
    本测试类通过 monkeypatch 局部覆盖为 'cosyvoice' / 'indextts'。
    """

    def _attach_mock_cosyvoice(self, agent, return_value=None, side_effect=None):
        """将 mock CosyVoiceService 注入 agent._cosyvoice，绕过懒加载。"""
        mock_svc = MagicMock()
        mock_svc.synthesize = AsyncMock()
        if side_effect is not None:
            mock_svc.synthesize.side_effect = side_effect
        else:
            mock_svc.synthesize.return_value = return_value or b"cosyvoice-mp3"
        agent._cosyvoice = mock_svc
        return mock_svc

    def _attach_mock_indextts(self, agent, return_value=None, side_effect=None):
        """将 mock IndexTTSService 注入 agent._indextts，绕过懒加载。"""
        mock_svc = MagicMock()
        mock_svc.synthesize = AsyncMock()
        if side_effect is not None:
            mock_svc.synthesize.side_effect = side_effect
        else:
            mock_svc.synthesize.return_value = return_value or b"indextts-mp3"
        agent._indextts = mock_svc
        return mock_svc

    async def test_cosyvoice_backend_success(self, agent, monkeypatch):
        """tts_backend='cosyvoice' → 走 CosyVoice 主路径并保存返回的音频字节。"""
        monkeypatch.setattr(settings, "tts_backend", "cosyvoice")
        mock_svc = self._attach_mock_cosyvoice(agent, return_value=b"cloned-voice")

        request = VoiceRequest(
            scene_id=1,
            dialogues=[
                DialogueLine(
                    text="你好", character_name="林远", character_role="主角"
                ),
            ],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["total_lines"] == 1
        assert resp.data["audio_urls"][0]["backend"] == "cosyvoice"
        assert resp.data["audio_urls"][0]["voice"] == "zh-CN-YunxiNeural"
        # 验证文件被写入（audio_bytes = b"cloned-voice"）
        from pathlib import Path

        audio_path = Path(resp.data["audio_urls"][0]["audio_url"].replace(
            f"http://localhost:{settings.backend_port}/static/audio/", ""
        ))
        # 文件路径位于 OUTPUT_DIR，验证写入
        from app.agents.voice_agent import OUTPUT_DIR
        expected_file = OUTPUT_DIR / audio_path.name
        assert expected_file.exists()
        assert expected_file.read_bytes() == b"cloned-voice"
        # CosyVoice 被调用一次，参数包含 text/voice/speed
        mock_svc.synthesize.assert_awaited_once()
        call_kwargs = mock_svc.synthesize.call_args.kwargs
        assert call_kwargs["text"] == "你好"
        assert call_kwargs["voice"] == "zh-CN-YunxiNeural"
        assert call_kwargs["speed"] == 1.0

    async def test_indextts_backend_success(self, agent, monkeypatch):
        """tts_backend='indextts' → 走 IndexTTS 主路径并写入音频。"""
        monkeypatch.setattr(settings, "tts_backend", "indextts")
        mock_svc = self._attach_mock_indextts(agent, return_value=b"emotion-voice")

        request = VoiceRequest(
            scene_id=2,
            dialogues=[
                DialogueLine(
                    text="情感台词",
                    character_name="小红",
                    character_role="女主角",
                ),
            ],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["audio_urls"][0]["backend"] == "indextts"
        # IndexTTS 调用参数包含 emotion（默认 'neutral'，因 DialogueLine 无 emotion 字段）
        call_kwargs = mock_svc.synthesize.call_args.kwargs
        assert call_kwargs["emotion"] == "neutral"
        assert call_kwargs["speed"] == 1.0

    async def test_cosyvoice_failure_fallback_to_edge(
        self, agent, monkeypatch, mock_edge_tts
    ):
        """CosyVoice 抛异常 → 自动回退到 edge-tts 并成功。"""
        monkeypatch.setattr(settings, "tts_backend", "cosyvoice")
        self._attach_mock_cosyvoice(agent, side_effect=RuntimeError("CV OOM"))

        request = VoiceRequest(
            scene_id=3,
            dialogues=[DialogueLine(text="回退测试", character_role="主角")],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["audio_urls"][0]["backend"] == "edge"
        # CosyVoice 被调用一次（失败）
        assert agent._cosyvoice.synthesize.await_count == 1
        # edge-tts 被调用一次（回退）
        assert mock_edge_tts.await_count == 1

    async def test_indextts_failure_fallback_to_edge(
        self, agent, monkeypatch, mock_edge_tts
    ):
        """IndexTTS 抛异常 → 自动回退到 edge-tts。"""
        monkeypatch.setattr(settings, "tts_backend", "indextts")
        self._attach_mock_indextts(agent, side_effect=RuntimeError("IT boom"))

        request = VoiceRequest(
            scene_id=4,
            dialogues=[DialogueLine(text="回退测试", character_role="主角")],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["audio_urls"][0]["backend"] == "edge"
        assert agent._indextts.synthesize.await_count == 1

    async def test_edge_backend_skips_cosyvoice(
        self, agent, mock_edge_tts
    ):
        """tts_backend='edge'（conftest 默认）→ 不调用 CosyVoice/IndexTTS。"""
        # 即使注入 mock TTS 服务，也不应被调用
        mock_cv = MagicMock()
        mock_cv.synthesize = AsyncMock()
        agent._cosyvoice = mock_cv
        mock_it = MagicMock()
        mock_it.synthesize = AsyncMock()
        agent._indextts = mock_it

        request = VoiceRequest(
            scene_id=5,
            dialogues=[DialogueLine(text="edge 路径", character_role="主角")],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["audio_urls"][0]["backend"] == "edge"
        # 两个 TTS 服务均未被调用
        mock_cv.synthesize.assert_not_awaited()
        mock_it.synthesize.assert_not_awaited()
        assert mock_edge_tts.await_count == 1

    async def test_multiple_dialogues_parallel(
        self, agent, monkeypatch
    ):
        """多条台词并行生成（cosyvoice 后端），均成功。"""
        monkeypatch.setattr(settings, "tts_backend", "cosyvoice")
        self._attach_mock_cosyvoice(agent, return_value=b"audio")

        request = VoiceRequest(
            scene_id=6,
            dialogues=[
                DialogueLine(text=f"台词{i}", character_role="主角")
                for i in range(3)
            ],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        assert resp.data["total_lines"] == 3
        assert len(resp.data["audio_urls"]) == 3
        # 每条都标记为 cosyvoice 后端
        for item in resp.data["audio_urls"]:
            assert item["backend"] == "cosyvoice"
        # CosyVoice 被调用 3 次（并行）
        assert agent._cosyvoice.synthesize.await_count == 3

    async def test_cosyvoice_empty_audio_triggers_fallback(
        self, agent, monkeypatch, mock_edge_tts
    ):
        """CosyVoice 服务抛 TTSServiceError（空音频）→ 回退 edge-tts。"""
        monkeypatch.setattr(settings, "tts_backend", "cosyvoice")
        # 真实 CosyVoiceService 在返回空字节时抛 TTSServiceError，
        # 这里直接 mock 抛异常以模拟该行为
        from app.services.tts_service import TTSServiceError

        self._attach_mock_cosyvoice(
            agent, side_effect=TTSServiceError("CosyVoice 返回空音频")
        )

        request = VoiceRequest(
            scene_id=7,
            dialogues=[DialogueLine(text="空音频测试", character_role="主角")],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        # 空字节异常 → 回退到 edge-tts
        assert resp.data["audio_urls"][0]["backend"] == "edge"

    async def test_rate_converted_to_speed(
        self, agent, monkeypatch
    ):
        """edge-tts rate='+10%' → speed=1.1 传给 CosyVoice。"""
        monkeypatch.setattr(settings, "tts_backend", "cosyvoice")
        mock_svc = self._attach_mock_cosyvoice(agent, return_value=b"a")

        request = VoiceRequest(
            scene_id=8,
            dialogues=[
                DialogueLine(
                    text="加速", character_role="主角", rate="+20%"
                ),
            ],
        )
        resp = await agent.execute(request)

        assert resp.success is True
        call_kwargs = mock_svc.synthesize.call_args.kwargs
        assert call_kwargs["speed"] == 1.2  # +20% → 1.2
