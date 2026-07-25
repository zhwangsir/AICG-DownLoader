"""Pytest 全局配置和共享 fixtures。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """将所有外部服务端点指向本地占位地址，避免测试调用真实服务。"""
    from app.config import settings

    monkeypatch.setattr(settings, "exo_base_url", "http://localhost:9999/v1")
    monkeypatch.setattr(settings, "exo_api_key", "test-key")
    monkeypatch.setattr(settings, "exo_model_glm52", "test-model")
    monkeypatch.setattr(settings, "comfyui_image_hq", "http://localhost:9000")
    monkeypatch.setattr(settings, "comfyui_image_fast", "http://localhost:9002")
    monkeypatch.setattr(settings, "comfyui_video_a", "http://localhost:9003")
    monkeypatch.setattr(settings, "comfyui_video_b", "http://localhost:9004")
    monkeypatch.setattr(settings, "backend_port", 8100)
    # P4.1: 默认走 ComfyUI 路径，保持向后兼容；
    # 专门测试 xDiT 的用例可局部 monkeypatch settings.video_backend = "xdit"
    monkeypatch.setattr(settings, "video_backend", "comfyui")
    monkeypatch.setattr(settings, "xdit_endpoint", "http://localhost:8288")
    # P4.2: 默认走回退路径（whisper/edge），保持向后兼容；
    # 专门测试 FireRedASR/CosyVoice/IndexTTS 的用例局部 monkeypatch 覆盖
    monkeypatch.setattr(settings, "asr_backend", "whisper")
    monkeypatch.setattr(settings, "firered_asr_endpoint", "http://localhost:8300/v1")
    monkeypatch.setattr(settings, "tts_backend", "edge")
    monkeypatch.setattr(settings, "cosyvoice_endpoint", "http://localhost:8400/v1")
    monkeypatch.setattr(settings, "indextts_endpoint", "http://localhost:8500/v1")
    # P4.3: 默认走回退路径（sdxl），保持向后兼容；
    # 专门测试 HunyuanImage/FLUX+PuLID/LTX-Video 的用例局部 monkeypatch 覆盖
    monkeypatch.setattr(settings, "image_backend", "sdxl")
    monkeypatch.setattr(settings, "hunyuanimage_endpoint", "http://localhost:8600/v1")
    monkeypatch.setattr(settings, "flux_pulid_endpoint", "http://localhost:8601/v1")
    monkeypatch.setattr(settings, "ltx_video_enabled", False)
    monkeypatch.setattr(settings, "ltx_video_endpoint", "http://localhost:8700/v1")
    # P4.4: 默认关闭唇形同步与后处理，保持向后兼容；
    # 专门测试 LatentSync/RealBasicVSR/RIFE/ProPainter/DeepFilterNet3 的用例局部 monkeypatch 覆盖
    monkeypatch.setattr(settings, "lip_sync_enabled", False)
    monkeypatch.setattr(settings, "latentsync_endpoint", "http://localhost:8289/v1")
    monkeypatch.setattr(settings, "postprocess_enabled", False)
    monkeypatch.setattr(settings, "postprocess_endpoint", "http://localhost:8290/v1")
    monkeypatch.setattr(settings, "deepfilternet_endpoint", "http://localhost:8301/v1")


@pytest.fixture
def base_agent():
    """返回一个未特化的 BaseAgent 实例。"""
    from app.agents.base import BaseAgent

    return BaseAgent("test_agent")


@pytest.fixture
def mock_call_llm():
    """Mock BaseAgent.call_llm，返回固定字符串。"""
    with patch("app.agents.base.BaseAgent.call_llm", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_call_comfyui():
    """Mock BaseAgent.call_comfyui，返回固定 prompt_id。"""
    with patch("app.agents.base.BaseAgent.call_comfyui", new_callable=AsyncMock) as mock:
        mock.return_value = {"prompt_id": "test-prompt-id"}
        yield mock


@pytest.fixture
def mock_get_comfyui_result():
    """Mock BaseAgent.get_comfyui_result，返回固定输出。"""
    with patch("app.agents.base.BaseAgent.get_comfyui_result", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_upload_image():
    """Mock BaseAgent.upload_image_to_comfyui，返回固定文件名。"""
    with patch(
        "app.agents.base.BaseAgent.upload_image_to_comfyui", new_callable=AsyncMock
    ) as mock:
        mock.return_value = "input.png"
        yield mock


@pytest.fixture
def mock_edge_tts():
    """Mock edge_tts.Communicate.save，避免真实 TTS 调用。"""
    with patch("edge_tts.Communicate.save", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_whisper():
    """Mock faster_whisper.WhisperModel，返回固定转写结果。"""
    fake_segment = MagicMock()
    fake_segment.start = 0.0
    fake_segment.end = 1.0
    fake_segment.text = "测试字幕"

    fake_info = MagicMock()
    fake_info.language = "zh"

    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([fake_segment], fake_info)

    with patch("faster_whisper.WhisperModel", return_value=fake_model) as mock:
        yield mock


@pytest.fixture
def mock_httpx_get():
    """Mock httpx.AsyncClient.get 返回固定字节内容。"""
    with patch("app.agents.base.httpx.AsyncClient.get", new_callable=AsyncMock) as mock:
        resp = MagicMock()
        resp.content = b"fake-image-bytes"
        resp.raise_for_status = MagicMock()
        mock.return_value = resp
        yield mock


@pytest.fixture
def sample_character():
    """返回一个示例 Character 模型。"""
    from app.models.schemas import Character

    return Character(
        character_id="char_001",
        name="林远",
        role="主角",
        age=26,
        description="年轻外卖员，肤色偏黑，眼神坚毅，穿黄色外卖服。",
        personality="沉稳、有责任心",
    )


@pytest.fixture
def sample_scene(sample_character):
    """返回一个示例 Scene 模型。"""
    from app.models.schemas import Scene

    return Scene(
        scene_id=1,
        episode=1,
        shot_type="特写",
        description="主角低头看着手机，眉头紧锁。",
        prompt="cinematic close-up...",
        negative_prompt="blurry",
        dialogue="这单地址怎么这么熟悉？",
        emotion="tension",
        duration_seconds=5,
        camera_movement="static",
    )


@pytest.fixture
def sample_script(sample_character, sample_scene):
    """返回一个示例 Script 字典。"""
    from app.models.schemas import Script

    return Script(
        project_id="test-project",
        title="最后的订单",
        genre="都市悬疑",
        total_episodes=1,
        characters=[sample_character],
        scenes=[sample_scene],
    )
