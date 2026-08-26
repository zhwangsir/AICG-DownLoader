"""H3 ref2va :8195 真实冒烟 — WORKFLOW_TEMPLATE_H3_R2V 原结构 + 迷你参数。

验证点：
1. /upload/image 上传关键帧 + 2 张角色参考图（共 3 次）
2. r2v 模板节点图被 H3 实例接受（ref2va UNET/MiniMaxH3ReferenceToVideo/
   audio_vae 连接/ref_images 嵌套 dict 动态组 ref_image_0..2）
3. 输出提取逻辑（SaveVideo -> images key）

用法: ./.venv/bin/python scripts/smoke_h3_r2v.py
"""
import asyncio
import io
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.video_agent import (  # noqa: E402
    H3_R2V_PROMPT_GUIDE,
    WORKFLOW_TEMPLATE_H3_R2V,
    _snap_h3_frames,
)

H3 = "http://192.168.71.127:8195"


def make_png(color: tuple[int, int, int], block: tuple[int, int, int]) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (256, 256), color)
    for x in range(96, 160):  # 亮块模拟主体
        for y in range(96, 160):
            img.putpixel((x, y), block)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def upload(cli: httpx.AsyncClient, name: str, png: bytes) -> str:
    r = await cli.post(
        f"{H3}/upload/image",
        files={"image": (name, png, "image/png")},
        data={"overwrite": "true"},
    )
    r.raise_for_status()
    img_name = r.json()["name"]
    print("upload:", img_name)
    return img_name


async def main() -> None:
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as cli:
        # 关键帧（构图） + 2 张「角色」参考图（不同色块模拟外观差异）
        kf_name = await upload(cli, "smoke_r2v_kf.png", make_png((30, 60, 120), (220, 180, 60)))
        ref1_name = await upload(cli, "smoke_r2v_ref1.png", make_png((30, 60, 120), (220, 180, 60)))
        ref2_name = await upload(cli, "smoke_r2v_ref2.png", make_png((60, 30, 90), (200, 120, 160)))

        # 迷你参数防 OOM：256x256 / 1s(22帧) / 4步（对齐 fl2va 冒烟配置）
        wf = json.loads(json.dumps(WORKFLOW_TEMPLATE_H3_R2V))
        wf["1"]["inputs"]["unet_name"] = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
        wf["2"]["inputs"]["clip_name"] = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        wf["3"]["inputs"]["vae_name"] = "minimax_h3_video_vae_fp16.safetensors"
        wf["4"]["inputs"]["vae_name"] = "minimax_h3_audio_vae_fp32.safetensors"
        wf["10"]["inputs"]["image"] = kf_name
        wf["20"]["inputs"]["prompt"] = (
            "a glowing golden cube on deep blue background, "
            "gentle camera push in, soft ambient hum" + H3_R2V_PROMPT_GUIDE
        )
        wf["20"]["inputs"]["width"] = 256
        wf["20"]["inputs"]["height"] = 256
        wf["20"]["inputs"]["length"] = _snap_h3_frames(1)
        wf["20"]["inputs"]["ref_image_size"] = "match"
        # 角色参考图动态挂接：LoadImage 11/12 → ref_images 组内 ref_image_1/2（与 video_agent 同构）
        ref_group = wf["20"]["inputs"].setdefault("ref_images", {})
        for idx, name in enumerate([ref1_name, ref2_name], start=1):
            node_id = str(10 + idx)
            wf[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            ref_group[f"ref_image_{idx}"] = [node_id, 0]
        wf["30"]["inputs"]["noise_seed"] = 42
        wf["32"]["inputs"]["steps"] = 4
        wf["60"]["inputs"]["filename_prefix"] = "aicg_h3_smoke/r2v"

        print("frames(1s) =", _snap_h3_frames(1), "| ref_images = 3 (kf + 2 refs)")

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

        r = await cli.get(f"{H3}/view?filename=r2v_00001_.mp4&subfolder=aicg_h3_smoke&type=output")
        print("download:", r.status_code, len(r.content), "bytes")
        Path("/tmp/h3_r2v_smoke_out.mp4").write_bytes(r.content)

    print("SMOKE PASS")


asyncio.run(main())
