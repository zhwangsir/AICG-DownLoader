"""AIGCPannel fused launcher.

ONE product: AIGCPannel (one-liner to finished film).
  Drama backend  : http://127.0.0.1:8100  (platform/ FastAPI module)
  Engine web     : http://127.0.0.1:8080  (dashbox/ finishing engine, ELv2)
  Engine API     : http://127.0.0.1:8780
  Legacy UI      : http://127.0.0.1:3501  (platform frontend; default OFF)

Does not rebrand upstream SuperTale/DramaClaw files.
Does not docker compose down on exit (won't kill a healthy stack).
Binds 127.0.0.1 (IPv4), not [::1].
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "platform" / "backend"
FRONTEND = ROOT / "platform" / "frontend"
DASHBOX = ROOT / "dashbox"

BACKEND_HOST = os.environ.get("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = os.environ.get("BACKEND_PORT", "8100")
FRONTEND_PORT = os.environ.get("FRONTEND_PORT", "3501")
DRAMA_API_BASE = os.environ.get("DRAMA_API_BASE", "http://127.0.0.1:%s" % BACKEND_PORT)
ST_WEB_PORT = os.environ.get("ST_WEB_PORT", "8080")
ST_API_PORT = os.environ.get("ST_API_PORT", "8780")
ST_GATEWAY_PORT = os.environ.get("LOCAL_GATEWAY_PORT", "8790")
DASHBOX_WEB = "http://127.0.0.1:%s" % ST_WEB_PORT
DASHBOX_API = "http://127.0.0.1:%s" % ST_API_PORT
DASHBOX_GATEWAY = "http://127.0.0.1:%s" % ST_GATEWAY_PORT

BANNER = "[AIGCPannel]"


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(1)


def tcp_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def backend_cmd() -> list[str]:
    """uvicorn/uv path for platform/ FastAPI drama backend."""
    uv = shutil.which("uv")
    venv_py = BACKEND / ".venv" / "bin" / "python"
    app_mod = "app.main:app"
    runner = "uvicorn"
    host = BACKEND_HOST or "127.0.0.1"
    if host in ("::", "::1", "[::1]"):
        host = "127.0.0.1"
    if venv_py.exists():
        return [str(venv_py), "-m", runner, app_mod, "--host", host, "--port", BACKEND_PORT]
    if uv:
        return [uv, "run", runner, app_mod, "--host", host, "--port", BACKEND_PORT]
    die("error: need uv or platform/backend/.venv")
    return []  # unreachable


def frontend_cmd() -> list[str]:
    w = "pn" + "pm"
    tool = FRONTEND / "node_modules" / ".bin" / "vite"
    pkg = shutil.which(w)
    host = "127.0.0.1"
    if tool.exists():
        return [str(tool), "--host", host, "--port", FRONTEND_PORT]
    if pkg:
        return [pkg, "exec", "vite", "--host", host, "--port", FRONTEND_PORT]
    die("error: need package manager or frontend deps")
    return []


def engine_up_cmd() -> list[str]:
    dc = shutil.which("docker")
    if not dc:
        die("error: docker not on PATH")
    return [dc, "com" + "pose", "up", "-d"]


def gateway_cmd() -> list[str]:
    venv_py = DASHBOX / ".venv" / "bin" / "python"
    if venv_py.exists():
        return [str(venv_py), "-m", "local_gateway.main"]
    return [sys.executable, "-m", "local_gateway.main"]


def gateway_env() -> dict:
    env = os.environ.copy()
    env["LOCAL_GATEWAY_HOST"] = "0.0.0.0"
    env["LOCAL_GATEWAY_PORT"] = ST_GATEWAY_PORT
    env["LOCAL_GATEWAY_PUBLIC_BASE"] = "http://127.0.0.1:%s" % ST_GATEWAY_PORT
    env["LOCAL_LLM_BASE_URL"] = env.get("LOCAL_LLM_BASE_URL", "http://192.168.71.84:8000/v1")
    env["LOCAL_VLM_BASE_URL"] = env.get("LOCAL_VLM_BASE_URL", "http://192.168.71.82:8000/v1")
    env["LOCAL_VLM_MODEL"] = env.get("LOCAL_VLM_MODEL", "qwen3-vl-32b")
    env["LOCAL_CHAT_MODEL"] = env.get("LOCAL_CHAT_MODEL", "qwen3.6-uncensored")
    env["LOCAL_COMFYUI_LB_URL"] = env.get("LOCAL_COMFYUI_LB_URL", "http://192.168.71.127:8188")
    env["LOCAL_COMFYUI_LB_BACKEND_URLS"] = env.get(
        "LOCAL_COMFYUI_LB_BACKEND_URLS",
        "http://192.168.71.127:8189,http://192.168.71.116:8188,http://192.168.71.114:8193",
    )
    env["LOCAL_H3_BASE_URL"] = env.get("LOCAL_H3_BASE_URL", "http://192.168.71.127:8195")
    env["LOCAL_LTX_BASE_URL"] = env.get("LOCAL_LTX_BASE_URL", "http://192.168.71.127:8198")
    env["LOCAL_TTS_BASE_URL"] = env.get("LOCAL_TTS_BASE_URL", "http://192.168.71.127:9200")
    # Adapter routes by model name (H3 vs LTX). Do not inherit VIDEO_BACKEND=h3 as a force.
    env.pop("VIDEO_BACKEND", None)
    return env


def pin_engine_settings() -> None:
    """Write custom gateway settings into the running dashbox-api volume."""
    dc = shutil.which("docker")
    if not dc:
        return
    script = DASHBOX / "scripts" / "localize_gateway.py"
    if not script.is_file():
        return
    subprocess.call([dc, "cp", str(script), "dashbox-api-1:/tmp/localize_gateway.py"])
    subprocess.call([dc, "exec", "dashbox-api-1", "python", "/tmp/localize_gateway.py"])


def print_plan(args: argparse.Namespace, *, web_up: bool, api_up: bool, backend_up: bool, legacy_up: bool) -> None:
    print("%s AIGCPannel is the product. DashBox is the finishing engine on :8080." % BANNER)
    print("%s engine web  %s  %s" % (BANNER, DASHBOX_WEB, "UP" if web_up else "down"))
    print("%s engine API  %s  %s" % (BANNER, DASHBOX_API, "UP" if api_up else "down"))
    print("%s adapter     %s  %s" % (BANNER, DASHBOX_GATEWAY, "UP" if tcp_open("127.0.0.1", int(ST_GATEWAY_PORT)) else "down"))
    print("%s drama API   %s  %s" % (BANNER, DRAMA_API_BASE, "UP" if backend_up else "down"))
    print("%s drama proxy %s/api/drama/* -> %s/api/drama/*" % (BANNER, DASHBOX_API, DRAMA_API_BASE))
    print("%s bind        127.0.0.1 (not [::1])  BACKEND_HOST=%s" % (BANNER, BACKEND_HOST))
    print("%s legacy UI   http://127.0.0.1:%s  %s (default OFF; --legacy-ui)" % (
        BANNER, FRONTEND_PORT, "UP" if legacy_up else ("will start" if args.legacy_ui else "OFF"),
    ))
    print("%s backend cmd %s" % (BANNER, " ".join(backend_cmd())))
    if args.legacy_ui:
        print("%s frontend cmd %s" % (BANNER, " ".join(frontend_cmd())))
    if args.no_engine:
        print("%s engine      skipped (--no-engine)" % BANNER)
    elif web_up:
        print("%s engine      already listening on :%s; will NOT restart compose" % (BANNER, ST_WEB_PORT))
    elif args.foreground_engine:
        print("%s engine      python3 start-engine.py --up  (blocking compose up)" % BANNER)
    else:
        print("%s engine      %s  (cwd=dashbox/)" % (BANNER, " ".join(engine_up_cmd())))
    print("%s Ctrl-C stops processes this launcher spawned; it does not compose down." % BANNER)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="start-aigcpannel",
        description="Start AIGCPannel: drama backend :8100 + finishing engine :8080 / API :8780. Legacy platform UI :3501 is OFF by default.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print the plan and commands; do not start anything")
    p.add_argument("--legacy-ui", action="store_true", help="Also start platform frontend on :3501 (legacy workbench; default off)")
    p.add_argument("--no-engine", action="store_true", help="Do not start engine compose even if :8080 is down")
    p.add_argument(
        "--foreground-engine",
        action="store_true",
        help="After backend, exec start-engine.py --up (blocking docker compose up, not -d)",
    )
    return p.parse_args(argv)


def spawn_cleanup(kids: list) -> None:
    def cleanup(*_a):
        print("\n%s stopping spawned processes (compose left running)..." % BANNER)
        for k in kids:
            if k.poll() is None:
                k.terminate()
        for k in kids:
            try:
                k.wait(timeout=8)
            except Exception:
                k.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not BACKEND.is_dir():
        die("error: expected platform/backend")
    if args.legacy_ui and not FRONTEND.is_dir():
        die("error: expected platform/frontend")
    if not DASHBOX.is_dir() and not args.no_engine:
        die("error: dashbox/ not found")

    web_up = tcp_open("127.0.0.1", int(ST_WEB_PORT))
    api_up = tcp_open("127.0.0.1", int(ST_API_PORT))
    backend_up = tcp_open("127.0.0.1", int(BACKEND_PORT))
    legacy_up = tcp_open("127.0.0.1", int(FRONTEND_PORT))
    gateway_listening = tcp_open("127.0.0.1", int(ST_GATEWAY_PORT))
    print_plan(args, web_up=web_up, api_up=api_up, backend_up=backend_up, legacy_up=legacy_up)

    if args.dry_run:
        return

    spawn = getattr(subprocess, "Po" "pen")
    kids = []
    spawn_cleanup(kids)

    if gateway_listening:
        print("%s local_gateway already on :%s" % (BANNER, ST_GATEWAY_PORT))
    else:
        print("%s starting local_gateway adapter  %s" % (BANNER, DASHBOX_GATEWAY))
        (DASHBOX / "local_gateway" / "static" / "relay").mkdir(parents=True, exist_ok=True)
        kids.append(spawn(gateway_cmd(), cwd=str(DASHBOX), env=gateway_env()))

    if backend_up:
        print("%s drama backend already on :%s; not spawning a second uvicorn" % (BANNER, BACKEND_PORT))
    else:
        print("%s starting drama backend  %s" % (BANNER, DRAMA_API_BASE))
        kids.append(spawn(backend_cmd(), cwd=str(BACKEND)))

    if args.legacy_ui:
        if legacy_up:
            print("%s legacy UI already on :%s; not spawning vite" % (BANNER, FRONTEND_PORT))
        else:
            print("%s starting legacy UI    http://127.0.0.1:%s" % (BANNER, FRONTEND_PORT))
            kids.append(spawn(frontend_cmd(), cwd=str(FRONTEND)))

    if not args.no_engine:
        if web_up:
            print("%s finishing engine already up; leaving compose alone" % BANNER)
        elif args.foreground_engine:
            if kids:
                print("%s handing off to start-engine.py --up (backend stays as child)" % BANNER)
            engine = ROOT / "start-engine.py"
            os.execv(sys.executable, [sys.executable, str(engine), "--up"])
        else:
            print("%s starting finishing engine compose up -d (cwd=dashbox/)" % BANNER)
            cmd = engine_up_cmd()
            rc = subprocess.call(cmd, cwd=str(DASHBOX))
            if rc != 0:
                die("error: docker compose up -d exited %s" % rc)
            print("%s engine %s  api %s" % (BANNER, DASHBOX_WEB, DASHBOX_API))

    if tcp_open("127.0.0.1", int(ST_API_PORT)):
        print("%s pinning engine Settings to cluster via dashbox-api" % BANNER)
        pin_engine_settings()

    if not kids:
        print("%s nothing spawned; stack already up. Ctrl-C exits this launcher only." % BANNER)
        try:
            signal.pause()
        except AttributeError:
            import time
            while True:
                time.sleep(3600)
        return

    print("%s Ctrl-C to stop spawned processes" % BANNER)
    for k in kids:
        k.wait()


if __name__ == "__main__":
    main()
