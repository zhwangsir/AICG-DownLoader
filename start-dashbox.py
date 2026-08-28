"""Alias: AIGCPannel is the product. Delegates to start-aigcpannel.py."""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
print("[AIGCPannel] AIGCPannel is the product. DashBox is the finishing engine on :8080.", flush=True)
print("[AIGCPannel] start-dashbox is an alias; canonical entry is ./start-aigcpannel.sh", flush=True)
target = ROOT / "start-aigcpannel.py"
os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
