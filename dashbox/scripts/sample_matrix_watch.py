#!/usr/bin/env python3
"""周期性把 driver state 中已完成的作品 finalize 到 works/（作品库实时可见）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample_matrix_driver import finalize_works, load_state  # noqa: E402

while True:
    try:
        st = load_state()
        finalize_works(st)
    except Exception as e:  # noqa: BLE001
        print(f"[watch] {e}", flush=True)
    # 全部完成则退出
    st = load_state()
    if len(st["tasks"]) >= 47 and all(v.get("status") in ("done", "skipped") for v in st["tasks"].values()):
        print("[watch] 全部完成，退出", flush=True)
        break
    time.sleep(300)
