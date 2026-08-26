"""MiniMax H3 原生 vs Turbo LoRA 实机 A/B 测试。

本测试需要 workstation 上的 H3 专用 ComfyUI 实例（:8195）及
ComfyUI-MiniMax-H3-Turbo 节点可用，属于长时间 GPU 测试，默认跳过。

运行方式：
    cd platform/backend
    .venv/bin/python -m pytest tests/integration/test_h3_turbo_ab.py -m slow --no-cov -s

测试维度：
- 生成耗时（AgentResponse.elapsed_seconds）
- 成功率
- 输出视频文件可下载且非空
- 加速比（原生耗时 / Turbo 耗时）
"""

from __future__ import annotations

import socket
import socketserver
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Generator

import pytest

from app.agents.video_agent import VideoAgent
from app.config import settings
from app.models.schemas import VideoRequest


def _free_port() -> int:
    """获取一个可用临时端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StaticHandler(SimpleHTTPRequestHandler):
    """仅服务单个目录的极简 HTTP Handler。"""

    def __init__(self, directory: str, *args, **kwargs):
        self._directory = directory
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format: str, *args) -> None:
        # 测试期间关闭访问日志，避免刷屏
        pass


@pytest.fixture(scope="module")
def synthetic_image_server() -> Generator[str, None, None]:
    """创建 768x1344 的渐变测试关键帧，并通过本地 HTTP 服务提供 URL。"""
    try:
        from PIL import Image
    except ImportError as exc:
        pytest.skip(f"PIL 未安装，无法生成测试图: {exc}")

    with tempfile.TemporaryDirectory(prefix="h3_ab_") as tmp:
        img_path = Path(tmp) / "keyframe.png"
        # 9:16 竖屏，与 H3 短剧画布一致
        width, height = settings.h3_width, settings.h3_height
        img = Image.new("RGB", (width, height), color=(30, 25, 40))
        pixels = img.load()
        for y in range(height):
            for x in range(width):
                pixels[x, y] = (
                    int(30 + (x / width) * 80),
                    int(25 + (y / height) * 60),
                    int(40 + ((x + y) / (width + height)) * 100),
                )
        img.save(img_path, "PNG")

        port = _free_port()
        server = socketserver.TCPServer(
            ("127.0.0.1", port),
            lambda *args, **kwargs: _StaticHandler(tmp, *args, **kwargs),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}/keyframe.png"
        finally:
            server.shutdown()
            server.server_close()


@pytest.fixture
def real_h3_settings(monkeypatch):
    """将 H3 相关配置指向真实服务，并关闭会引入额外变量的开关。"""
    monkeypatch.setattr(settings, "video_backend", "h3")
    monkeypatch.setattr(settings, "h3_comfyui_url", "http://192.168.71.127:8195")
    monkeypatch.setattr(settings, "h3_result_timeout", 1800.0)
    monkeypatch.setattr(settings, "h3_multishot_enabled", False)
    monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", False)
    # 单镜无 style，画风 QC 会自动跳过；保持开启可验证 fail-open 不阻断
    monkeypatch.setattr(settings, "h3_style_qc_enabled", True)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_native_vs_turbo_ab(
    synthetic_image_server: str,
    real_h3_settings,
):
    """原生 H3（20 步）与 Turbo LoRA（6 步）各跑一次，对比耗时与成功率。"""
    prompt = (
        "A cinematic vertical shot: a young woman stands on a rainy street at night, "
        "neon lights reflected on wet pavement, soft bokeh, smooth camera drift."
    )

    base_request = VideoRequest(
        scene_id=9001,
        image_url=synthetic_image_server,
        prompt=prompt,
        duration_seconds=5,  # 124 帧，H3 最小训练长度
    )

    results: list[dict] = []

    # ---- 原生 H3（20 步） ----
    settings.h3_turbo_enabled = False
    settings.h3_turbo_steps = 20
    agent_native = VideoAgent()
    resp_native = await agent_native.execute(
        base_request.model_copy(update={"scene_id": 9001})
    )
    assert resp_native.success, f"原生 H3 失败: {resp_native.error}"
    results.append(
        {
            "mode": "native",
            "steps": 20,
            "elapsed": resp_native.elapsed_seconds,
            "video_url": resp_native.data["video_url"],
        }
    )

    # 等待 ComfyUI 队列释放，避免两任务并发导致 OOM
    time.sleep(5)

    # ---- Turbo LoRA（6 步） ----
    settings.h3_turbo_enabled = True
    settings.h3_turbo_steps = 6
    agent_turbo = VideoAgent()
    resp_turbo = await agent_turbo.execute(
        base_request.model_copy(update={"scene_id": 9002})
    )
    assert resp_turbo.success, f"Turbo LoRA 失败: {resp_turbo.error}"
    results.append(
        {
            "mode": "turbo",
            "steps": 6,
            "elapsed": resp_turbo.elapsed_seconds,
            "video_url": resp_turbo.data["video_url"],
        }
    )

    # ---- 下载校验：输出文件存在且非空 ----
    for r in results:
        async with VideoAgent().http as client:
            video_resp = await client.get(r["video_url"])
        video_resp.raise_for_status()
        r["video_bytes"] = len(video_resp.content)
        assert r["video_bytes"] > 100_000, f"{r['mode']} 输出视频过小: {r['video_bytes']} bytes"

    # ---- 加速比 ----
    speedup = results[0]["elapsed"] / results[1]["elapsed"]
    print("\n=== H3 Turbo A/B 结果 ===")
    for r in results:
        print(
            f"  {r['mode']:8s} steps={r['steps']} elapsed={r['elapsed']:.1f}s "
            f"video={r['video_bytes'] / 1024 / 1024:.2f}MB"
        )
    print(f"  speedup={speedup:.2f}x")

    # Turbo 至少快 1.5 倍；若未达到则记录为实验性数据，测试不因此失败
    # （LoRA 加速比受模型、分辨率、显存影响，这里只保证更快）
    assert results[1]["elapsed"] < results[0]["elapsed"], (
        f"Turbo 未比原生快: native={results[0]['elapsed']:.1f}s, "
        f"turbo={results[1]['elapsed']:.1f}s"
    )
