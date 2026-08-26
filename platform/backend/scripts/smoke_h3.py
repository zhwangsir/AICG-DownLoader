"""H3 :8195 真实冒烟 — 用 video_agent 的 WORKFLOW_TEMPLATE_H3 原结构 + 迷你参数。

验证点：
1. /upload/image 上传分镜关键帧
2. 模板节点图被 H3 实例接受（UNET/CLIP/双VAE/MiniMaxH3ImageToVideo/采样链/双解码/CreateVideo/SaveVideo）
3. 输出提取逻辑（SaveVideo -> images key）

用法: ./.venv/bin/python scripts/smoke_h3.py
"""
import asyncio
import io
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.video_agent import WORKFLOW_TEMPLATE_H3, _snap_h3_frames  # noqa: E402

H3 = "http://192.168.71.127:8195"


def make_png() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (256, 256), (30, 60, 120))
    for x in range(96, 160):  # 亮块模拟主体
        for y in range(96, 160):
            img.putpixel((x, y), (220, 180, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def main() -> None:
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as cli:
        png = make_png()
        r = await cli.post(
            f"{H3}/upload/image",
            files={"image": ("smoke_kf.png", png, "image/png")},
            data={"overwrite": "true"},
        )
        r.raise_for_status()
        img_name = r.json()["name"]
        print("upload:", img_name)

        # 迷你参数防 OOM：256x256 / 22帧 / 4步（对齐用户已成功的冒烟配置）
        wf = json.loads(json.dumps(WORKFLOW_TEMPLATE_H3))
        wf["1"]["inputs"]["unet_name"] = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
        wf["2"]["inputs"]["clip_name"] = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        wf["3"]["inputs"]["vae_name"] = "minimax_h3_video_vae_fp16.safetensors"
        wf["4"]["inputs"]["vae_name"] = "minimax_h3_audio_vae_fp32.safetensors"
        wf["10"]["inputs"]["image"] = img_name
        wf["20"]["inputs"]["prompt"] = (
            "a glowing golden cube on deep blue background, "
            "gentle camera push in, soft ambient hum"
        )
        wf["20"]["inputs"]["width"] = 256
        wf["20"]["inputs"]["height"] = 256
        wf["20"]["inputs"]["length"] = _snap_h3_frames(1)
        wf["30"]["inputs"]["noise_seed"] = 42
        wf["32"]["inputs"]["steps"] = 4
        wf["60"]["inputs"]["filename_prefix"] = "aicg_h3_smoke/i2v"

        print("frames(1s) =", _snap_h3_frames(1), "| frames(5s) =", _snap_h3_frames(5))

        r = await cli.post(f"{H3}/prompt", json={"prompt": wf})
        if r.status_code != 200:
            print("PROMPT REJECTED:", r.status_code, r.text[:2000])
            sys.exit(1)
        pid = r.json()["prompt_id"]
        print("prompt_id:", pid)

        t0 = time.time()
        while time.time() - t0 < 600:
            await asyncio.sleep(5)
            h = (await cli.get(f"{H3}/history/{pid}")).json()
            if pid not in h:
                print(f"  ... {int(time.time() - t0)}s sampling")
                continue
            entry = h[pid]
            st = entry.get("status", {})
            print("done:", st.get("status_str"), "completed:", st.get("completed"))
            for nid, o in entry.get("outputs", {}).items():
                print("OUT", nid, json.dumps(o, ensure_ascii=False)[:300])
            if st.get("status_str") != "success":
                print("MESSAGES:", json.dumps(st.get("messages", []), ensure_ascii=False)[:2000])
                sys.exit(1)
            break
        else:
            print("TIMEOUT")
            sys.exit(1)

        r = await cli.get(f"{H3}/view?filename=i2v_00001_.mp4&subfolder=aicg_h3_smoke&type=output")
        print("download:", r.status_code, len(r.content), "bytes")
        Path("/tmp/h3_smoke_out.mp4").write_bytes(r.content)

    print("SMOKE PASS")


asyncio.run(main())
