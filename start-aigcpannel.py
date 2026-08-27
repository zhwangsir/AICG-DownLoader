"""Thin wrapper: DashBox is the user-facing product. Delegates to start-dashbox.py."""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
print("[DashBox] start-aigcpannel is a thin wrapper. DashBox is the product.", flush=True)
print("[DashBox] Main UI http://127.0.0.1:8080  drama backend http://127.0.0.1:8100", flush=True)
print("[DashBox] Legacy workbench :3501 is OFF unless you pass --legacy-ui", flush=True)
target = ROOT / "start-dashbox.py"
os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
