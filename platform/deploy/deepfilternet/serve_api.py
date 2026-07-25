"""DeepFilterNet3 HTTP 服务包装器.

提供 OpenAI 风格的音频降噪 API:
- GET  /v1/health        健康检查
- GET  /v1/models        模型列表
- POST /v1/denoise       上传音频文件, 返回降噪后的 WAV

依赖: 仅 Python 3.9+ 标准库 + deep-filter 二进制 (~/deploys/deepfilternet/deep-filter)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
HOST = os.environ.get("DF_HOST", "0.0.0.0")
PORT = int(os.environ.get("DF_PORT", "8301"))
DEEP_FILTER_BIN = os.environ.get(
    "DF_BIN", str(Path.home() / "deploys" / "deepfilternet" / "deep-filter")
)
WORK_DIR = Path(os.environ.get("DF_WORK_DIR", "/tmp/deepfilternet-io"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_AGE_SEC = 3600


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}", flush=True)


def cleanup_old_files() -> None:
    """清理超过 MAX_FILE_AGE_SEC 的临时文件."""
    now = time.time()
    for p in WORK_DIR.iterdir():
        try:
            if p.is_file() and now - p.stat().st_mtime > MAX_FILE_AGE_SEC:
                p.unlink()
        except OSError:
            pass


def run_deep_filter(input_path: Path, output_dir: Path) -> tuple[bool, str, Path | None]:
    """调用 deep-filter CLI 处理音频.

    Returns:
        (success, message, output_path)
    """
    if not Path(DEEP_FILTER_BIN).exists():
        return False, f"deep-filter binary not found at {DEEP_FILTER_BIN}", None

    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [DEEP_FILTER_BIN, "-o", str(output_dir), str(input_path)]
    log(f"running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
    except subprocess.TimeoutExpired:
        return False, "deep-filter timed out after 300s", None
    except OSError as e:
        return False, f"failed to execute deep-filter: {e}", None

    # deep-filter 有时 returncode=1 但实际处理成功 (输出文件已生成),
    # 因此优先检查输出文件而非 returncode
    out_path = output_dir / input_path.name
    if not out_path.exists():
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        return False, f"deep-filter failed (rc={proc.returncode}): {err}", None

    return True, "ok", out_path


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "DeepFilterNet/0.5.6"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_binary(
        self, status: int, data: bytes, content_type: str, filename: str
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log(f"{self.client_address[0]} - {format % args}")

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/v1/health", "/health"):
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model": "deepfilternet3",
                    "version": "0.5.6",
                    "binary": DEEP_FILTER_BIN,
                    "binary_exists": Path(DEEP_FILTER_BIN).exists(),
                },
            )
        elif path == "/v1/models":
            self._send_json(
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "deepfilternet3",
                            "object": "model",
                            "created": 0,
                            "owned_by": "Rikorose",
                        }
                    ],
                },
            )
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/v1/denoise", "/denoise"):
            self._handle_denoise()
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found", "path": path})

    # ---- /v1/denoise ----
    def _handle_denoise(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        cleanup_old_files()
        task_id = uuid.uuid4().hex[:12]
        work = WORK_DIR / task_id
        work.mkdir(parents=True, exist_ok=True)

        try:
            if ctype.startswith("multipart/form-data"):
                input_path = self._read_multipart(work)
            else:
                # 直接 body 上传 (raw audio bytes)
                body = self._read_body()
                # 简单 sniff 文件类型
                if body[:4] == b"RIFF":
                    ext = ".wav"
                elif body[:3] == b"ID3" or body[:2] == b"\xff\xfb":
                    ext = ".mp3"
                elif body[:4] == b"OggS":
                    ext = ".ogg"
                elif body[:4] == b"fLaC":
                    ext = ".flac"
                else:
                    ext = ".wav"
                input_path = work / f"input{ext}"
                input_path.write_bytes(body)

            if not input_path.exists() or input_path.stat().st_size == 0:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "empty or missing audio file"},
                )
                return

            log(
                f"task {task_id}: input={input_path.name} "
                f"size={input_path.stat().st_size}"
            )
            out_dir = work / "out"
            ok, msg, out_path = run_deep_filter(input_path, out_dir)
            if not ok or out_path is None:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "denoise failed", "detail": msg},
                )
                return

            data = out_path.read_bytes()
            log(f"task {task_id}: output size={len(data)}")
            self._send_binary(
                HTTPStatus.OK,
                data,
                "audio/wav",
                f"denoised_{input_path.stem}.wav",
            )
        finally:
            # 保留输入输出, 等 cleanup_old_files 异步清理
            pass

    # ---- multipart/form-data 解析 ----
    def _read_multipart(self, work: Path) -> Path:
        """从 multipart/form-data 中提取第一个文件字段, 返回其本地路径."""
        ctype = self.headers.get("Content-Type", "")
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"')
                break
        if not boundary:
            raise ValueError("missing multipart boundary")

        boundary_bytes = b"--" + boundary.encode()
        body = self._read_body()

        parts = body.split(boundary_bytes)
        for part in parts:
            if not part or part in (b"--", b"--\r\n"):
                continue
            part = part.strip(b"\r\n")
            if b"\r\n\r\n" not in part:
                continue
            header_blob, file_data = part.split(b"\r\n\r\n", 1)
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]

            header_text = header_blob.decode("utf-8", errors="ignore")
            filename = ""
            for line in header_text.split("\r\n"):
                if line.lower().startswith("content-disposition:"):
                    for seg in line.split(";"):
                        seg = seg.strip()
                        if seg.startswith("filename="):
                            filename = seg[len("filename="):].strip('"')
            ext = Path(filename).suffix or ".wav"
            input_path = work / f"input{ext}"
            input_path.write_bytes(file_data)
            return input_path

        raise ValueError("no file part found in multipart body")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    log(f"starting DeepFilterNet3 service on {HOST}:{PORT}")
    log(f"binary: {DEEP_FILTER_BIN} (exists={Path(DEEP_FILTER_BIN).exists()})")
    log(f"work dir: {WORK_DIR}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
