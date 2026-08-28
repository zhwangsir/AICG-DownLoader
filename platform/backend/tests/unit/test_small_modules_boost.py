"""小模块覆盖率清零专项测试 —— 仅补既有用例未覆盖的分支，不重复主路径。

按模块分 class：
- tts_service        : _looks_like_audio 短字节 / _wav_to_mp3 全分支 / CosyVoice 非音频
- model_gateway      : openai_base_url 幂等 / 真实 _probe / 全量缓存失效 / aclose
- core.progress      : 监听器异常吞没 / unsubscribe 未知任务与未知监听器
- model_registry     : win32/linux 默认 models.json 路径
- routers.progress   : QueueFull 吞没 / 心跳 / None 终止流
- style_anchor       : KB 缺失 → 合成兜底
- config             : downloader config.json 存在分支
- node_logger        : error 超长截断
- retry              : on_retry 异常吞没 / max_attempts=0 的兜底 raise
- failure_registry   : 库存档损坏重建 / 空反向子句 / gate_modes
- long_video_service : _run_capture 成功与失败 / concat 空产出报错
- edit_agent         : 无 CJK 字体 / 本地静态资源复用 / ffprobe OSError
- subtitle_agent     : asr_service 懒加载 / AI 优化成功路径 / AI-Omni 整段兜底 / 本地音频
- voice_agent        : cosyvoice/indextts 属性懒加载
- mos_evaluation     : MosReport.to_dict / num_frames=1 中心取点
- long_video_planner : 情绪同族 0.8 / 节拍单空与非法值
- ltx25_video_service: 输出无视频报错
- prompt_expander    : </d> 先于 <d> 的配对失败
- mention_service    : 空名资产跳过
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# 模块导入时（fixture 未运行）保存真实 _probe，用于还原 conftest 的恒健康 mock
from app.services.model_gateway import ModelGateway

_REAL_PROBE = ModelGateway._probe


# ===========================================================================
# tts_service
# ===========================================================================
class TestTtsServiceBoost:
    def test_looks_like_audio_short_bytes(self):
        """不足 12 字节的载荷直接判非音频。"""
        from app.services.tts_service import _looks_like_audio

        assert _looks_like_audio(b"tiny") is False
        assert _looks_like_audio(b"") is False

    async def test_wav_to_mp3_no_ffmpeg_returns_wav(self, monkeypatch):
        """ffmpeg 不在 PATH 时原样返回 WAV 字节。"""
        from app.services import tts_service

        monkeypatch.setattr(tts_service.shutil, "which", lambda name: None)
        wav = b"RIFF" + b"\x00" * 32
        assert await tts_service._wav_to_mp3(wav) == wav

    async def test_wav_to_mp3_success_returns_mp3(self, monkeypatch):
        """ffmpeg 转码成功返回 MP3 字节。"""
        from app.services import tts_service

        monkeypatch.setattr(tts_service.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        mp3 = b"ID3" + b"\x00" * 16

        proc = MagicMock()
        proc.returncode = 0

        async def _communicate(input=None):
            return mp3, b""

        proc.communicate = _communicate

        async def _fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        assert await tts_service._wav_to_mp3(b"RIFF" + b"\x00" * 32) == mp3

    async def test_wav_to_mp3_bad_returncode_falls_back(self, monkeypatch):
        """ffmpeg 非零退出码时回退原始 WAV。"""
        from app.services import tts_service

        monkeypatch.setattr(tts_service.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        wav = b"RIFF" + b"\x00" * 32

        proc = MagicMock()
        proc.returncode = 1

        async def _communicate(input=None):
            return b"", b"transcode boom"

        proc.communicate = _communicate

        async def _fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        assert await tts_service._wav_to_mp3(wav) == wav

    async def test_wav_to_mp3_exec_exception_falls_back(self, monkeypatch):
        """ffmpeg 启动异常时回退原始 WAV。"""
        from app.services import tts_service

        monkeypatch.setattr(tts_service.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        wav = b"RIFF" + b"\x00" * 32

        async def _fake_exec(*args, **kwargs):
            raise OSError("spawn failed")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        assert await tts_service._wav_to_mp3(wav) == wav

    async def test_cosyvoice_non_audio_content_raises(self):
        """CosyVoice 返回占位文本（>=12 字节非音频）抛 TTSServiceError。"""
        from app.services.tts_service import CosyVoiceService, TTSServiceError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"cloned-voice-placeholder")  # 24 字节文本

        svc = CosyVoiceService(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        with pytest.raises(TTSServiceError, match="非音频内容"):
            await svc.synthesize(text="t", voice="v")


# ===========================================================================
# model_gateway
# ===========================================================================
class TestModelGatewayBoost:
    def test_openai_base_url_idempotent_with_v1_suffix(self):
        """端点已带 /v1 时原样返回，不重复追加。"""
        from app.services.model_gateway import CapabilitySpec

        gateway = ModelGateway()
        gateway._capabilities["t"] = CapabilitySpec(
            name="t", description="", endpoints=("http://x:1/v1",)
        )
        assert gateway.openai_base_url("t") == "http://x:1/v1"
        # 空端点同样原样返回
        gateway._capabilities["empty"] = CapabilitySpec(
            name="empty", description="", endpoints=("",)
        )
        assert gateway.openai_base_url("empty") == ""

    async def test_probe_real_implementation(self, monkeypatch):
        """还原真实 _probe：2xx/404 健康、5xx 离线、异常离线、空 health_path 直连。"""
        monkeypatch.setattr(ModelGateway, "_probe", _REAL_PROBE)
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            if "down" in str(request.url):
                return httpx.Response(503)
            if "missing" in str(request.url):
                return httpx.Response(404)
            return httpx.Response(200)

        gateway = ModelGateway()
        gateway._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        ok, detail = await gateway._probe("http://up:1", "/health")
        assert ok is True and detail == "HTTP 200"
        assert seen_urls[-1] == "http://up:1/health"

        ok, detail = await gateway._probe("http://missing:1", "/x")
        assert ok is True and detail == "HTTP 404"  # 404 = 端口活着

        ok, detail = await gateway._probe("http://down:1", "/health")
        assert ok is False and detail == "HTTP 503"

        # 空 health_path → 直接探测端点本身
        ok, _ = await gateway._probe("http://up:2", "")
        assert ok is True and seen_urls[-1] == "http://up:2"

        # 连接异常 → (False, 异常文本)
        def raising_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conn refused")

        gateway._http = httpx.AsyncClient(transport=httpx.MockTransport(raising_handler))
        ok, detail = await gateway._probe("http://dead:1", "/health")
        assert ok is False and "conn refused" in detail

    async def test_invalidate_health_cache_all(self):
        """capability=None 时清空全部健康缓存。"""
        gateway = ModelGateway()
        assert await gateway.is_healthy("image") is True  # conftest mock 恒健康
        assert await gateway.is_healthy("tts") is True
        assert gateway._health_cache  # 已播种
        gateway.invalidate_health_cache()
        assert gateway._health_cache == {}

    async def test_aclose_closes_http_client(self):
        gateway = ModelGateway()
        await gateway.aclose()
        assert gateway._http.is_closed is True


# ===========================================================================
# core.progress
# ===========================================================================
class TestProgressTrackerBoost:
    def test_update_listener_exception_swallowed(self):
        """监听器抛异常不影响主流程，其余监听器仍被通知。"""
        from app.core.progress import ProgressTracker

        tracker = ProgressTracker()
        task_id = tracker.create("video")
        notified: list[int] = []

        def bad_listener(record):
            raise RuntimeError("listener boom")

        def good_listener(record):
            notified.append(record.percent)

        tracker.subscribe(task_id, bad_listener)
        tracker.subscribe(task_id, good_listener)
        record = tracker.update(task_id, percent=42)
        assert record.percent == 42
        assert notified == [42]

    def test_unsubscribe_unknown_task_noop(self):
        """对不存在的任务取消订阅静默返回。"""
        from app.core.progress import ProgressTracker

        ProgressTracker().unsubscribe("ghost-task", lambda r: None)

    def test_unsubscribe_unknown_listener_swallowed(self):
        """移除未注册的监听器（ValueError）被吞没，已有监听器保留。"""
        from app.core.progress import ProgressTracker

        tracker = ProgressTracker()
        task_id = tracker.create("video")
        notified: list[int] = []

        def listener_a(record):
            notified.append(record.percent)

        tracker.subscribe(task_id, listener_a)
        tracker.unsubscribe(task_id, lambda r: None)  # 不在列表中 → ValueError 吞没
        tracker.update(task_id, percent=7)
        assert notified == [7]


# ===========================================================================
# model_registry_service
# ===========================================================================
class TestModelRegistryBoost:
    def test_default_models_json_win32_with_appdata(self, monkeypatch):
        from app.services.model_registry_service import _default_models_json

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", r"C:\Users\t\AppData\Roaming")
        assert _default_models_json() == (
            Path(r"C:\Users\t\AppData\Roaming") / "comfy-downloader" / "models.json"
        )

    def test_default_models_json_win32_default_appdata(self, monkeypatch):
        from app.services.model_registry_service import _default_models_json

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        expected = Path.home() / "AppData" / "Roaming" / "comfy-downloader" / "models.json"
        assert _default_models_json() == expected

    def test_default_models_json_linux(self, monkeypatch):
        from app.services.model_registry_service import _default_models_json

        monkeypatch.setattr(sys, "platform", "linux")
        assert _default_models_json() == (
            Path.home() / ".config" / "comfy-downloader" / "models.json"
        )


# ===========================================================================
# routers.progress（SSE 流）
# ===========================================================================
class TestProgressRouterBoost:
    async def test_heartbeat_sent_on_idle_timeout(self, monkeypatch):
        """30s 无事件时发送 :heartbeat 保活帧。"""
        from app.core.progress import progress_tracker
        from app.routers.progress import progress_stream

        async def _timeout_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", _timeout_wait_for)

        task_id = progress_tracker.create("boost")
        resp = await progress_stream(task_id)
        it = resp.body_iterator
        first = await it.__anext__()
        assert first.startswith("data: {") and task_id in first
        second = await it.__anext__()
        assert second == ":heartbeat\n\n"
        # 再推进一轮：心跳后的 continue 回到循环头部再次等待
        third = await it.__anext__()
        assert third == ":heartbeat\n\n"
        await it.aclose()

    async def test_stream_404_for_unknown_task(self):
        """任务不存在时 SSE 流接口抛 404。"""
        from fastapi import HTTPException

        from app.routers.progress import progress_stream

        with pytest.raises(HTTPException) as exc_info:
            await progress_stream("ghost-task-000")
        assert exc_info.value.status_code == 404

    async def test_completed_update_flows_and_closes_stream(self):
        """进度事件经队列推送（yield update），completed 状态后再推一条并正常结束。"""
        from app.core.progress import progress_tracker
        from app.routers.progress import progress_stream

        task_id = progress_tracker.create("boost")
        resp = await progress_stream(task_id)
        it = resp.body_iterator
        await it.__anext__()  # 初始状态帧（订阅完成）
        progress_tracker.update(task_id, status="completed", percent=100, message="done")
        second = await it.__anext__()  # 队列中的更新事件
        assert '"completed"' in second and task_id in second
        with pytest.raises(StopAsyncIteration):  # completed → sleep → break
            await it.__anext__()
        assert progress_tracker.get(task_id).listeners == []

    async def test_progress_get_404_and_success(self):
        """非流式查询：未知任务 404；存在的任务返回当前状态字典。"""
        from fastapi import HTTPException

        from app.core.progress import progress_tracker
        from app.routers.progress import progress_get

        with pytest.raises(HTTPException) as exc_info:
            await progress_get("ghost-task-000")
        assert exc_info.value.status_code == 404

        task_id = progress_tracker.create("boost", message="进行中")
        progress_tracker.update(task_id, status="running", percent=30)
        data = await progress_get(task_id)
        assert data["task_id"] == task_id
        assert data["status"] == "running"
        assert data["percent"] == 30
        assert data["message"] == "进行中"

    async def test_none_update_terminates_stream(self, monkeypatch):
        """收到 None 哨兵时终止事件流并退订。"""
        from app.core.progress import progress_tracker
        from app.routers.progress import progress_stream

        async def _none_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            return None

        monkeypatch.setattr(asyncio, "wait_for", _none_wait_for)

        task_id = progress_tracker.create("boost")
        resp = await progress_stream(task_id)
        it = resp.body_iterator
        await it.__anext__()  # 初始状态帧
        with pytest.raises(StopAsyncIteration):
            await it.__anext__()
        # finally 已退订：再更新不会残留监听器
        assert progress_tracker.get(task_id).listeners == []

    async def test_listener_queue_full_swallowed(self, monkeypatch):
        """有界队列满时 put_nowait 的 QueueFull 被吞没，不影响进度更新。"""

        class _FullQueue:
            def __init__(self, *args, **kwargs):
                pass

            def put_nowait(self, item):
                raise asyncio.QueueFull

            async def get(self):  # 不会被消费到（先首帧后心跳即关闭）
                await asyncio.sleep(3600)

        async def _timeout_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "Queue", _FullQueue)
        monkeypatch.setattr(asyncio, "wait_for", _timeout_wait_for)

        from app.core.progress import progress_tracker
        from app.routers.progress import progress_stream

        task_id = progress_tracker.create("boost")
        resp = await progress_stream(task_id)
        it = resp.body_iterator
        await it.__anext__()  # 触发 subscribe
        # 监听器 put_nowait 抛 QueueFull → 吞没，update 正常返回
        record = progress_tracker.update(task_id, percent=5)
        assert record.percent == 5
        # 推进到 try 块内心跳 yield，再关闭以走 finally 退订
        assert await it.__anext__() == ":heartbeat\n\n"
        await it.aclose()
        assert progress_tracker.get(task_id).listeners == []


# ===========================================================================
# style_anchor
# ===========================================================================
class TestStyleAnchorBoost:
    def test_kb_missing_falls_back_to_synthetic_anchor(self, monkeypatch, tmp_path):
        """styles.json 缺失/损坏时：KB 加载告警 → 空条目 → 合成兜底锚。"""
        from app.services import style_anchor

        style_anchor._load_entries.cache_clear()
        monkeypatch.setattr(style_anchor, "KB_DIR", tmp_path)  # 目录下无 styles.json
        try:
            anchor = style_anchor.resolve_style_anchor("写实电影感")
            assert anchor.key == "fallback"
            assert anchor.is_realistic is True
            assert anchor.style_name_en == "cinematic realistic"
            # 写实兜底锚排斥动漫渲染词
            assert "anime" in anchor.negative_en
            # 未知画风在空 KB 下同样落合成兜底
            assert style_anchor.resolve_style_anchor("不存在的画风").key == "fallback"
        finally:
            style_anchor._load_entries.cache_clear()  # 防止污染其他用例的 lru_cache


# ===========================================================================
# config
# ===========================================================================
class TestConfigBoost:
    def test_load_downloader_config_existing_file(self, tmp_path):
        """config.json 存在时解析为 DownloaderConfig 并回填实例。"""
        from app.config import DownloaderConfig, Settings

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(
            json.dumps({"comfy_root": "/opt/ComfyUI", "torch_index": "cu130"}),
            encoding="utf-8",
        )
        s = Settings()
        s.downloader_config_path = str(cfg_file)  # 绝对路径 → 命中存在分支
        cfg = s.load_downloader_config()
        assert isinstance(cfg, DownloaderConfig)
        assert cfg.comfy_root == "/opt/ComfyUI"
        assert cfg.torch_index == "cu130"
        assert s.downloader_config is cfg

    def test_cluster_defaults_match_env_example(self):
        """无 .env 时的类默认值须与 .env.example / ToIV 对齐，避免产品漂移。"""
        from app.config import Settings

        f = Settings.model_fields
        assert f["ltx_enabled"].default is False
        assert f["tts_backend"].default == "indextts"
        assert f["visual_model_url"].default == "http://192.168.71.82:8000/v1"
        assert f["visual_model_name"].default == "qwen3-vl-32b"
        assert f["exo_base_url"].default == "http://192.168.71.84:8000/v1"
        assert f["comfyui_image_hq"].default == "http://192.168.71.127:8188"
        assert f["h3_comfyui_url"].default == "http://192.168.71.127:8195"
        assert f["asr_backend"].default == "ai_omni"
        assert f["ai_omni_asr_endpoint"].default == "http://192.168.71.127:9210"
        assert f["indextts_endpoint"].default == "http://192.168.71.127:9200"


# ===========================================================================
# node_logger
# ===========================================================================
class TestNodeLoggerBoost:
    def test_long_error_truncated(self, caplog):
        """error 文本超过 200 字符时截断追加省略号。"""
        from app.core.node_logger import node_log

        with caplog.at_level(logging.INFO, logger="aicg.node"):
            node_log("x", "error", error="e" * 500)
        message = caplog.records[-1].message
        assert "error=" in message
        assert message.endswith("…")
        assert len(message) < 400


# ===========================================================================
# retry
# ===========================================================================
class TestRetryBoost:
    async def test_on_retry_exception_swallowed(self):
        """on_retry 回调自身抛异常被吞没，重试流程不受影响。"""
        from app.core.retry import with_retry

        attempts: list[int] = []

        def bad_on_retry(exc, attempt):
            raise RuntimeError("callback boom")

        @with_retry(max_attempts=2, base_delay=0.01, jitter=False, on_retry=bad_on_retry)
        async def fn():
            attempts.append(1)
            if len(attempts) < 2:
                raise TimeoutError("flaky")
            return "ok"

        assert await fn() == "ok"
        assert len(attempts) == 2

    async def test_zero_max_attempts_raises_from_fallback(self):
        """max_attempts=0 时循环不执行，落入兜底 raise（last_exception=None → TypeError）。"""
        from app.core.retry import with_retry

        @with_retry(max_attempts=0)
        async def fn():
            return "never"

        with pytest.raises(TypeError):
            await fn()


# ===========================================================================
# failure_registry
# ===========================================================================
class TestFailureRegistryBoost:
    def test_corrupt_store_rebuilds_seed(self, tmp_path):
        """存量 JSON 损坏时告警并以种子重建（fail-open）。"""
        from app.services.failure_registry import SEED_FAILURE_MODES, FailureModeRegistry

        store = tmp_path / "fm.json"
        store.write_text("{ not valid json ", encoding="utf-8")
        registry = FailureModeRegistry(store_path=store)
        modes = registry.list_active()
        assert len(modes) == len(SEED_FAILURE_MODES)

    def test_negative_clause_no_bullets_returns_empty(self, tmp_path):
        """该层无有效反向子句时返回空串。"""
        from app.services.failure_registry import FailureModeRegistry

        registry = FailureModeRegistry(store_path=tmp_path / "fm.json")
        assert registry.build_negative_prompt_clause("no_such_layer") == ""

    def test_gate_modes_delegates_to_list_active(self, tmp_path):
        from app.services.failure_registry import FailureModeRegistry

        registry = FailureModeRegistry(store_path=tmp_path / "fm.json")
        gate = registry.gate_modes()
        assert gate == registry.list_active(gate_only=True)
        assert gate and all(m.gate_enabled for m in gate)


# ===========================================================================
# long_video_service
# ===========================================================================
class TestLongVideoServiceBoost:
    async def test_run_capture_success(self, monkeypatch):
        """_run_capture 返回 stdout 文本。"""
        proc = MagicMock()
        proc.returncode = 0

        async def _communicate():
            return b"12.5\n", b""

        proc.communicate = _communicate

        async def _fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        from app.services.long_video_service import _run_capture

        assert await _run_capture(["ffprobe", "x.mp4"]) == "12.5\n"

    async def test_run_capture_nonzero_raises(self, monkeypatch):
        """非零退出码抛 RuntimeError（含 stderr 摘要）。"""
        proc = MagicMock()
        proc.returncode = 3

        async def _communicate():
            return b"", b"ffprobe exploded"

        proc.communicate = _communicate

        async def _fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        from app.services.long_video_service import _run_capture

        with pytest.raises(RuntimeError, match="ffprobe exploded"):
            await _run_capture(["ffprobe", "x.mp4"])

    async def test_concat_empty_output_raises(self, monkeypatch, tmp_path):
        """ffmpeg 拼接后产物缺失/为空 → LongVideoError。"""
        monkeypatch.setattr(
            "app.services.long_video_service._run_ffmpeg",
            AsyncMock(),  # 不产出文件
        )
        from app.services.long_video_service import LongVideoError, concat_videos

        src = tmp_path / "c0.mp4"
        src.write_bytes(b"v")
        with pytest.raises(LongVideoError, match="拼接失败"):
            await concat_videos([src], tmp_path / "out.mp4")


# ===========================================================================
# edit_agent
# ===========================================================================
class TestEditAgentBoost:
    def test_find_cjk_font_none_when_no_candidate(self, monkeypatch):
        """全部候选字体路径不存在时返回 None。"""
        from app.agents import edit_agent

        monkeypatch.setattr(edit_agent, "_CJK_FONT_CANDIDATES", ["/nonexistent/x.ttf"])
        assert edit_agent._find_cjk_font() is None

    async def test_burn_ai_label_skips_without_font(self, monkeypatch, tmp_path):
        """无中文字体时跳过烧录，原路径返回（不阻断主流程）。"""
        from app.agents import edit_agent
        from app.agents.edit_agent import EditAgent

        monkeypatch.setattr(edit_agent, "_find_cjk_font", lambda: None)
        video = tmp_path / "v.mp4"
        video.write_bytes(b"v")
        agent = EditAgent()
        assert await agent._burn_ai_label(video, license_number="12345") == video

    async def test_download_reuses_local_static(self, monkeypatch, tmp_path):
        """本地静态资源 URL 直接复用文件系统路径，不发起 HTTP。"""
        from app.agents import edit_agent
        from app.agents.edit_agent import EditAgent

        local = tmp_path / "output" / "video" / "seg.mp4"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"v")
        monkeypatch.setattr(
            edit_agent, "_local_path_from_url", lambda url, local_dir: local
        )
        agent = EditAgent()
        dest = tmp_path / "dest.mp4"
        result = await agent._download("http://localhost:8100/static/video/seg.mp4", dest)
        assert result == local
        assert not dest.exists()

    async def test_probe_has_audio_oserror_returns_false(self, monkeypatch, tmp_path):
        """ffprobe 不可用（OSError）视为无音轨。"""
        from app.agents.edit_agent import EditAgent

        async def _fake_exec(*args, **kwargs):
            raise OSError("ffprobe not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        agent = EditAgent()
        assert await agent._probe_has_audio(tmp_path / "v.mp4") is False


# ===========================================================================
# subtitle_agent
# ===========================================================================
class TestSubtitleAgentBoost:
    def test_asr_service_lazy_init(self):
        """asr_service 属性懒加载并缓存实例。"""
        from app.agents.subtitle_agent import SubtitleAgent
        from app.services.asr_service import ASRService

        agent = SubtitleAgent()
        svc = agent.asr_service
        assert isinstance(svc, ASRService)
        assert agent.asr_service is svc

    async def test_ai_optimize_success_rebuilds_srt(
        self, agent_and_audio, mock_whisper, monkeypatch
    ):
        """AI 优化成功路径：按行号回写文本并重建 SRT；坏行触发 ValueError 吞没。"""
        agent, audio_file = agent_and_audio
        monkeypatch.setattr(
            "app.agents.subtitle_agent.optimize_content",
            AsyncMock(return_value="1. 优化后的字幕\nxyz. 坏行号"),
        )
        from app.models.schemas import SubtitleRequest

        with patch.object(agent, "_download_audio", return_value=str(audio_file)):
            resp = await agent.execute(
                SubtitleRequest(scene_id=42, audio_url="http://x/a.mp3", language="zh")
            )
        assert resp.success is True
        assert "优化后的字幕" in resp.data["srt_content"]

    async def test_ai_optimize_success_with_dict_segments(
        self, agent_and_audio, monkeypatch
    ):
        """字典 segments（AI-Omni 路径）走 seg["text"] 回写分支并重建 SRT。"""
        agent, audio_file = agent_and_audio
        from app.config import settings
        from app.models.schemas import SubtitleRequest

        monkeypatch.setattr(settings, "asr_backend", "ai_omni")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "text": "原始字幕",
            "language": "zh",
            "segments": [{"start": 0.0, "end": 1.5, "text": "原始字幕"}],
        }
        monkeypatch.setattr(
            agent, "http", MagicMock(post=AsyncMock(return_value=mock_resp))
        )
        monkeypatch.setattr(
            "app.agents.subtitle_agent.optimize_content",
            AsyncMock(return_value="1. 字典回写字幕"),
        )
        with patch.object(agent, "_download_audio", return_value=str(audio_file)):
            resp = await agent.execute(
                SubtitleRequest(scene_id=43, audio_url="http://x/a.mp3", language="zh")
            )
        assert resp.success is True
        assert "字典回写字幕" in resp.data["srt_content"]
        assert resp.data["segments"][0]["text"] == "字典回写字幕"

    async def test_ai_omni_text_only_falls_back_to_single_segment(
        self, monkeypatch, tmp_path
    ):
        """AI-Omni 无 segments 但有 text 时整段兜底（保证 SRT 可构建）。"""
        from app.agents.subtitle_agent import SubtitleAgent

        agent = SubtitleAgent()
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"fake-audio")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "text": "整段兜底文本",
            "duration": 2.5,
            "language": "zh",
        }
        monkeypatch.setattr(
            agent, "http", MagicMock(post=AsyncMock(return_value=mock_resp))
        )
        segments, language = await agent._transcribe_via_ai_omni(str(audio), "zh")
        assert segments == [{"start": 0.0, "end": 2.5, "text": "整段兜底文本"}]
        assert language == "zh"

    async def test_download_audio_local_static_reuse(self, monkeypatch, tmp_path):
        """localhost 静态音频直接从文件系统返回，不发起 HTTP。"""
        from app.agents import subtitle_agent
        from app.agents.subtitle_agent import SubtitleAgent

        fake_out = tmp_path / "output" / "subtitle"
        audio_dir = tmp_path / "output" / "audio"
        audio_dir.mkdir(parents=True)
        audio_file = audio_dir / "voice.mp3"
        audio_file.write_bytes(b"fake")
        monkeypatch.setattr(subtitle_agent, "OUTPUT_DIR", fake_out)

        agent = SubtitleAgent()
        result = await agent._download_audio(
            "http://localhost:8100/static/audio/voice.mp3"
        )
        assert result == str(audio_file)

    @pytest.fixture
    def agent_and_audio(self, tmp_path):
        from app.agents.subtitle_agent import SubtitleAgent

        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"fake-audio")
        return SubtitleAgent(), audio


# ===========================================================================
# voice_agent
# ===========================================================================
class TestVoiceAgentBoost:
    def test_tts_service_properties_lazy_init(self):
        """cosyvoice_service / indextts_service 属性懒加载并缓存。"""
        from app.agents.voice_agent import VoiceAgent
        from app.services.tts_service import CosyVoiceService, IndexTTSService

        agent = VoiceAgent()
        cv = agent.cosyvoice_service
        it = agent.indextts_service
        assert isinstance(cv, CosyVoiceService)
        assert isinstance(it, IndexTTSService)
        assert agent.cosyvoice_service is cv
        assert agent.indextts_service is it


# ===========================================================================
# mos_evaluation_service
# ===========================================================================
class TestMosEvaluationBoost:
    def test_report_to_dict(self):
        """MosReport.to_dict 序列化逐帧明细与聚合指标。"""
        from app.services.mos_evaluation_service import MosFrameScores, MosReport

        report = MosReport(
            frames=[MosFrameScores(frame_index=0, scores={"visual_quality": 4.0}, reason="ok")],
            dimension_means={"visual_quality": 4.0},
            mos=4.0,
            frames_scored=1,
        )
        data = report.to_dict()
        assert data["frames"] == [
            {"frame_index": 0, "scores": {"visual_quality": 4.0}, "reason": "ok"}
        ]
        assert data["dimension_means"] == {"visual_quality": 4.0}
        assert data["mos"] == 4.0
        assert data["frames_scored"] == 1

    async def test_evaluate_single_frame_uses_center_time(self, monkeypatch, tmp_path):
        """num_frames=1 时取片中时点（duration/2）抽唯一帧。"""
        from app.services.mos_evaluation_service import MosEvaluationService

        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        seen_times: list[float] = []

        async def fake_duration(_p):
            return 8.0

        async def fake_extract(_vp, t, out):
            seen_times.append(t)
            Path(out).write_bytes(b"\x89PNG fake")
            return out

        async def vlm_ok(content):
            return {
                "visual_quality": 4,
                "motion_naturalness": 4,
                "temporal_consistency": 4,
                "text_alignment": 4,
                "reason": "",
            }

        monkeypatch.setattr(
            "app.services.mos_evaluation_service.probe_video_duration", fake_duration
        )
        monkeypatch.setattr(
            "app.services.mos_evaluation_service.extract_frame_at", fake_extract
        )
        svc = MosEvaluationService(vlm_caller=vlm_ok)
        report = await svc.evaluate(video, num_frames=1, work_dir=tmp_path)
        assert seen_times == [4.0]
        assert report.frames_scored == 1
        assert report.mos == 4.0


# ===========================================================================
# long_video_planner
# ===========================================================================
class TestLongVideoPlannerBoost:
    def test_emotion_score_same_family(self):
        """不同标签同情绪族 → 0.8（如 happy/joy、紧张/不安）。"""
        from app.services.long_video_planner import LongVideoPlanner

        assert LongVideoPlanner._emotion_score("happy", "joy") == 0.8
        assert LongVideoPlanner._emotion_score("紧张", "不安") == 0.8

    def test_beat_score_single_empty(self):
        """单侧空节拍 → 弱信号 0.4。"""
        from app.services.long_video_planner import LongVideoPlanner

        assert LongVideoPlanner._beat_score("hook", "") == 0.4
        assert LongVideoPlanner._beat_score("", "hook") == 0.4

    def test_beat_score_illegal_values(self):
        """双侧均不在节拍表 → 弱信号 0.4。"""
        from app.services.long_video_planner import LongVideoPlanner

        assert LongVideoPlanner._beat_score("foo", "bar") == 0.4


# ===========================================================================
# ltx25_video_service
# ===========================================================================
class TestLTX25VideoServiceBoost:
    def test_extract_video_url_no_output_raises(self):
        """输出中无 videos/gifs/images 时抛 RuntimeError。"""
        from app.services.ltx25_video_service import _extract_video_url

        with pytest.raises(RuntimeError, match="未找到生成的视频"):
            _extract_video_url({"1": {"images": []}, "2": {}}, "http://w:8198")


# ===========================================================================
# prompt_expander
# ===========================================================================
class TestPromptExpanderBoost:
    def test_closing_tag_before_opening_breaks_pairing(self):
        """</d> 先于 <d> 出现（depth 变负）→ 配对失败。"""
        from app.services.prompt_expander import validate_h3_prompt

        errors = validate_h3_prompt("[Shot 1] </d>[zh] 你好 (S1)", 6000)
        assert any("配对" in e for e in errors)


# ===========================================================================
# mention_service
# ===========================================================================
class TestMentionServiceBoost:
    def test_auto_link_skips_empty_name_asset(self, tmp_path):
        """资产库中名字为空的条目被跳过，不参与自动挂接。"""
        from app.models.schemas import CharacterAsset
        from app.services.character_library import CharacterLibrary
        from app.services.mention_service import auto_link_characters

        library = CharacterLibrary(library_dir=tmp_path)
        library.save(CharacterAsset(character_id="c_empty", name="", locked=True))
        library.save(CharacterAsset(character_id="c_lin", name="林远", locked=True))
        hits = auto_link_characters("林远走出门", library=library)
        assert [a.character_id for a in hits] == ["c_lin"]
