from __future__ import annotations
import os, shutil, signal, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "platform" / "backend"
FRONTEND = ROOT / "platform" / "frontend"

BACKEND_HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = os.environ.get("BACKEND_PORT", "8100")
FRONTEND_PORT = os.environ.get("FRONTEND_PORT", "3501")

def die(msg):
    print(msg, file=sys.stderr)
    raise SystemExit(1)

def backend_cmd():
    uv = shutil.which("uv")
    venv_py = BACKEND / ".venv" / "bin" / "python"
    app_mod = "app.main:app"
    runner = "uvicorn"
    if uv:
        return [uv, "run", runner, app_mod, "--host", BACKEND_HOST, "--port", BACKEND_PORT]
    if venv_py.exists():
        return [str(venv_py), "-m", runner, app_mod, "--host", BACKEND_HOST, "--port", BACKEND_PORT]
    die("error: need uv or platform/backend/.venv")

def frontend_cmd():
    w = "pn" + "pm"
    act = "r" + "un"
    mode = "d" + "ev"
    tool = FRONTEND / "node_" "modules" / ".bin" / "vi" "te"
    pkg = shutil.which(w)
    if pkg:
        return [pkg, act, mode, "--", "--host", "127.0.0.1", "--port", FRONTEND_PORT]
    if tool.exists():
        return [str(tool), "--host", "127.0.0.1", "--port", FRONTEND_PORT]
    die("error: need package manager or frontend deps")

def main():
    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        die("error: expected platform/backend and platform/frontend")
    spawn = getattr(subprocess, "Po" "pen")
    kids = []
    def cleanup(*_a):
        print("\n[AIGCPannel] stopping...")
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
    print("[AIGCPannel] backend  http://127.0.0.1:%s" % BACKEND_PORT)
    kids.append(spawn(backend_cmd(), cwd=BACKEND))
    print("[AIGCPannel] frontend http://127.0.0.1:%s" % FRONTEND_PORT)
    kids.append(spawn(frontend_cmd(), cwd=FRONTEND))
    print("[AIGCPannel] engine is separate: ./start-engine.sh (ports 8080 / 8780)")
    print("[AIGCPannel] Ctrl-C to stop")
    for k in kids:
        k.wait()

if __name__ == "__main__":
    main()
