#!/usr/bin/env python3
"""接管已在 :8195 队列中的任务（如 N2），等其完成入库后退出。

背景：driver 的 wait() 超时重启场景下，已在 ComfyUI 队列排队的 prompt 不应重复提交；
本脚本按 filename_prefix 在 history 中轮询定位产物并回写 state。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample_matrix_driver import (  # noqa: E402
    WORKS,
    Comfy,
    H3,
    load_state,
    save_state,
)

# 接管任务清单：prefix -> task_id
TAKEOVER = {"N2_anime_15s": "N2"}


def main() -> None:
    h3 = Comfy(H3, "h3:8195")
    st = load_state()
    todo = {p: tid for p, tid in TAKEOVER.items() if st["tasks"].get(tid, {}).get("status") != "done"}
    if not todo:
        print("无需接管", flush=True)
        return
    print(f"接管: {todo}", flush=True)
    t0 = time.time()
    while todo and time.time() - t0 < 21600:
        try:
            with urllib.request.urlopen(f"{H3}/history", timeout=60) as r:
                hist = json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            print(f"[history] {e}", flush=True)
            time.sleep(30)
            continue
        for pid, entry in hist.items():
            outputs = entry.get("outputs", {})
            files = h3.files_of(outputs)
            for f in files:
                for prefix, tid in list(todo.items()):
                    if prefix in f.get("filename", ""):
                        dest = WORKS / prefix / "raw.mp4"
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        h3.download(f, dest)
                        st["tasks"][tid] = {
                            "status": "done",
                            "out": str(dest),
                            "attempts": st["tasks"].get(tid, {}).get("attempts", 0) + 1,
                            "rescued": True,
                        }
                        save_state(st)
                        print(f"[rescue] {tid} 完成 -> {dest}", flush=True)
                        del todo[prefix]
        time.sleep(60)
    print("接管结束", flush=True)


if __name__ == "__main__":
    main()
