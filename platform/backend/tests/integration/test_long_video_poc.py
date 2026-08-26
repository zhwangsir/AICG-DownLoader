"""M20 长视频分块续写实机 PoC —— H3 I2V 帧链 2 块拼接。

本测试需要 workstation 上的 H3 专用 ComfyUI 实例（:8195）可用，
属于长时间 GPU 测试（2 块 × 5s 原生 20 步，约 8-15 分钟），默认跳过。

运行方式：
    cd platform/backend
    .venv/bin/python -m pytest tests/integration/test_long_video_poc.py -m slow --no-cov -s

验证维度（对应 2026-08-10 长视频调研路线 A 的 PoC 目标）：
- 帧链编排：chunk 1 首帧 = chunk 0 末帧（抽取 → 上传 → I2V）
- 拼接产物：ffmpeg concat 输出时长 ≈ 块时长 × 块数，文件非空
- 接缝观测：拼接缝前后帧直方图差异（打印观测值，不作硬断言）
"""

from __future__ import annotations

import socket
import socketserver
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Generator

import pytest

from app.agents.video_agent import VideoAgent
from app.config import settings
from app.services.long_video_service import LongVideoService, probe_video_duration


def _free_port() -> int:
    """获取一个可用临时端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StaticHandler(SimpleHTTPRequestHandler):
    """仅服务单个目录的极简 HTTP Handler（关闭访问日志）。"""

    def log_message(self, format: str, *args) -> None:
        pass


@pytest.fixture(scope="module")
def synthetic_image_server() -> Generator[str, None, None]:
    """创建 768x1344 的渐变测试关键帧，并通过本地 HTTP 服务提供 URL。"""
    try:
        from PIL import Image
    except ImportError as exc:
        pytest.skip(f"PIL 未安装，无法生成测试图: {exc}")

    with tempfile.TemporaryDirectory(prefix="h3_long_") as tmp:
        img_path = Path(tmp) / "keyframe.png"
        width, height = settings.h3_width, settings.h3_height  # 9:16 竖屏
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
            lambda *args, **kwargs: _StaticHandler(*args, directory=tmp, **kwargs),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}/keyframe.png"
        finally:
            server.shutdown()
            server.server_close()


@pytest.fixture
def real_h3_settings(monkeypatch, tmp_path):
    """指向真实 H3 服务并开启长视频开关；PoC 产物落在测试临时目录。"""
    monkeypatch.setattr(settings, "video_backend", "h3")
    monkeypatch.setattr(settings, "h3_comfyui_url", "http://192.168.71.127:8195")
    monkeypatch.setattr(settings, "h3_result_timeout", 1800.0)
    monkeypatch.setattr(settings, "h3_multishot_enabled", False)
    monkeypatch.setattr(settings, "storyboard_keyframe_anchor_enabled", False)
    monkeypatch.setattr(settings, "h3_turbo_enabled", False)  # PoC 走原生高质量路径
    monkeypatch.setattr(settings, "long_video_enabled", True)
    monkeypatch.setattr(settings, "long_video_chunk_seconds", 5)
    monkeypatch.setattr(settings, "long_video_max_chunks", 4)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_h3_frame_chain_2chunk_poc(
    synthetic_image_server: str,
    real_h3_settings,
    tmp_path,
):
    """2 块 × 5s 帧链续写：验证编排链路、拼接时长与产物有效性。"""
    chunk_prompts = [
        (
            "A cinematic vertical shot: a young woman stands on a rainy street at night, "
            "neon lights reflected on wet pavement, the camera slowly pushes in, soft bokeh."
        ),
        (
            "She slowly turns her head toward the camera, rain keeps falling, "
            "neon reflections shift across her face, smooth continuous motion."
        ),
    ]

    service = LongVideoService(
        video_agent=VideoAgent(),
        worker_url=settings.h3_comfyui_url,
    )
    events: list[str] = []
    result = await service.generate(
        first_frame_url=synthetic_image_server,
        chunk_prompts=chunk_prompts,
        work_dir=tmp_path,
        progress_callback=lambda p, m: events.append(f"{p}% {m}"),
    )

    # ---- 结构断言：两块全部完成、产物齐全 ----
    assert result.chunks_completed == 2
    assert len(result.chunk_paths) == 2
    for p in result.chunk_paths:
        assert p.exists() and p.stat().st_size > 100_000, f"块视频异常: {p}"
    assert result.video_path.exists()
    final_size = result.video_path.stat().st_size
    assert final_size > 200_000, f"拼接视频过小: {final_size} bytes"

    # ---- 时长断言：拼接时长 ≈ 2 × 5s（重编码帧边界容差 ±2s）----
    final_duration = await probe_video_duration(result.video_path)
    assert abs(final_duration - 10.0) <= 2.0, f"拼接时长异常: {final_duration:.2f}s"

    # ---- 接缝观测：拼接缝前后帧像素差异（观测值，辅助判断接缝质量）----
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        Image = None
    seam_diff = None
    if Image is not None:
        import subprocess

        def _grab(t: float, out: Path) -> Path:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(result.video_path),
                 "-frames:v", "1", "-q:v", "2", str(out)],
                check=True, capture_output=True,
            )
            return out

        pre = _grab(4.90, tmp_path / "seam_pre.png")
        post = _grab(5.10, tmp_path / "seam_post.png")
        from PIL import Image as _Image
        import PIL.ImageChops as Chops

        a = _Image.open(pre).convert("L").resize((192, 336))
        b = _Image.open(post).convert("L").resize((192, 336))
        diff = Chops.difference(a, b)
        hist = diff.histogram()
        seam_diff = sum(i * v for i, v in enumerate(hist)) / (192 * 336)

    print("\n=== M20 长视频帧链 PoC 结果 ===")
    print(f"  chunks={result.chunks_completed} elapsed={result.elapsed_seconds:.1f}s")
    print(f"  final={result.video_path} size={final_size / 1024 / 1024:.2f}MB")
    print(f"  duration={final_duration:.2f}s (期望 ≈10s)")
    if seam_diff is not None:
        print(f"  接缝帧平均像素差={seam_diff:.2f}/255 (越小越连续)")

    # ---- 产物归档：tmp_path 会随测试清理，拷贝到持久目录供人工审阅 ----
    import shutil

    artifact_dir = (
        Path(__file__).resolve().parents[2] / "test_artifacts" / "longvideo_poc"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result.video_path, artifact_dir / "long_video_2chunk.mp4")
    for p in result.chunk_paths:
        shutil.copy2(p, artifact_dir / p.name)
    for name in ("seam_pre.png", "seam_post.png"):
        f = tmp_path / name
        if f.exists():
            shutil.copy2(f, artifact_dir / name)
    print(f"  产物已归档: {artifact_dir}")

