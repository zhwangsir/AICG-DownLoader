#!/usr/bin/env python3
"""样本矩阵驱动器：17 支视频（SFW N1-N10 + R18 R1-R7）。

矩阵需求：
1. 时长阶梯 5s/15s/30s/60s
2. 赛道：动漫 / 真人 / 3D
3. 音画直出（H3 原生音轨；对白片再叠 CosyVoice2 混音）
4. 参考图 + 参考视频驱动（H3 ref2va）
5. R18 独立系列（含下体特写 + 性交动作完整场景）
6. 真人专项：打斗 / 微表情 / 对白

执行后端（2026-08-22 真机核验）：
- WS  = workstation GPU0 直连 :8196（SDXL 首帧 + Wan2.2 I2V；154 loras 全在位）
- H3  = MiniMax H3 专用实例 :8195（fl2va/ref2va + 14 NSFW/combat loras）
- COSY= CosyVoice2 :9201（OpenAI /v1/audio/speech 兼容）

产物：works/<id>/{work.json, *.mp4, cover.png}，断点续跑 state 存 works/_state.json。
用法：python3 scripts/sample_matrix_driver.py [--only N1,R3] [--allow-r18=1]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

WS = "http://192.168.71.127:8196"
H3 = "http://192.168.71.127:8195"
COSY = "http://192.168.71.127:9201"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
REPO = Path(__file__).resolve().parent.parent
WORKS = REPO / "works"
STATE_FILE = WORKS / "_state.json"

WAN_NEG = (
    "watermark, text, subtitles, letterbox, pillarbox, frame, border, split screen, "
    "noise, artifacts, blur, vignette, 色调艳丽，过曝，静态，细节模糊不清，字幕，风格，"
    "作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
    "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
    "静止不动的画面，杂乱的背景，背景人很多，倒着走"
)

# ---------------------------------------------------------------- 基础客户端


def http_json(url: str, payload: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class Comfy:
    def __init__(self, base: str, name: str):
        self.base = base.rstrip("/")
        self.name = name
        self.client_id = str(uuid.uuid4())

    def submit(self, prompt: dict) -> str:
        resp = http_json(
            f"{self.base}/prompt", {"prompt": prompt, "client_id": self.client_id}
        )
        if "prompt_id" not in resp:
            raise RuntimeError(f"[{self.name}] submit 失败: {resp}")
        return resp["prompt_id"]

    def wait(self, prompt_id: str, timeout: int = 21600) -> dict:
        """轮询 history 直到完成；返回 outputs dict。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                hist = http_json(f"{self.base}/history/{prompt_id}", timeout=30)
            except (urllib.error.URLError, TimeoutError):
                time.sleep(10)
                continue
            entry = hist.get(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = status.get("messages", [])
                    raise RuntimeError(f"[{self.name}] 执行错误: {msgs[:2]}")
                if entry.get("outputs"):
                    return entry["outputs"]
            time.sleep(12)
        raise TimeoutError(f"[{self.name}] prompt {prompt_id} 超时")

    def files_of(self, outputs: dict) -> list[dict]:
        """收集 SaveImage/SaveVideo/VHS 输出的文件描述。"""
        files = []
        for node_out in outputs.values():
            for key in ("images", "gifs", "videos", "audio"):
                for f in node_out.get(key, []) or []:
                    if isinstance(f, dict) and f.get("filename"):
                        files.append(f)
        return files

    def download(self, f: dict, dest: Path) -> Path:
        q = urllib.parse.urlencode(
            {
                "filename": f["filename"],
                "subfolder": f.get("subfolder", ""),
                "type": f.get("type", "output"),
            }
        )
        with urllib.request.urlopen(f"{self.base}/view?{q}", timeout=180) as r:
            dest.write_bytes(r.read())
        return dest

    def upload_image(self, path: Path, name: str | None = None) -> str:
        """上传文件到 input 目录（图片/视频通用）。"""
        name = name or path.name
        boundary = "----" + uuid.uuid4().hex
        body = b""
        with open(path, "rb") as fh:
            content = fh.read()
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode() + content + b"\r\n"
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n"
            f"\r\ntrue\r\n--{boundary}--\r\n"
        ).encode()
        req = urllib.request.Request(
            f"{self.base}/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.loads(r.read())
        return resp.get("name", name)

    def combo_options(self, node: str, param_path: tuple) -> list:
        obj = http_json(f"{self.base}/object_info/{node}", timeout=60)
        cur = obj[node]["input"]
        for p in param_path[:-1]:
            cur = cur[p]
        return cur[param_path[-1]][0]


# ---------------------------------------------------------------- 工作流构造


def wf_sdxl_image(prompt: str, ckpt: str, w: int, h: int, seed: int, prefix: str) -> dict:
    """SDXL 文生图（首帧/参考图）。"""
    return {
        "1": {"inputs": {"ckpt_name": ckpt}, "class_type": "CheckpointLoaderSimple"},
        "2": {
            "inputs": {"text": prompt, "clip": ["1", 1]},
            "class_type": "CLIPTextEncode",
        },
        "3": {
            "inputs": {"text": WAN_NEG, "clip": ["1", 1]},
            "class_type": "CLIPTextEncode",
        },
        "4": {
            "inputs": {"width": w, "height": h, "batch_size": 1},
            "class_type": "EmptyLatentImage",
        },
        "5": {
            "inputs": {
                "seed": seed,
                "steps": 30,
                "cfg": 6.5,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
            "class_type": "KSampler",
        },
        "6": {"inputs": {"samples": ["5", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"},
        "7": {
            "inputs": {"filename_prefix": prefix, "images": ["6", 0]},
            "class_type": "SaveImage",
        },
    }


def wf_wan_i2v(
    image: str,
    prompt: str,
    w: int,
    h: int,
    length: int,
    loras_high: list[tuple[str, float]],
    loras_low: list[tuple[str, float]],
    seed: int,
    prefix: str,
) -> dict:
    """Wan 2.2 I2V 双噪链（lightx2v 4 步加速由 loras 参数传入）。"""
    wf: dict = {
        "1": {
            "inputs": {
                "unet_name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
        },
        "2": {
            "inputs": {
                "unet_name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
        },
        "9": {"inputs": {"vae_name": "wan_2.1_vae.safetensors"}, "class_type": "VAELoader"},
        "11": {
            "inputs": {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
            },
            "class_type": "CLIPLoader",
        },
        "12": {
            "inputs": {"text": prompt, "clip": ["11", 0]},
            "class_type": "CLIPTextEncode",
        },
        "13": {
            "inputs": {"text": WAN_NEG, "clip": ["11", 0]},
            "class_type": "CLIPTextEncode",
        },
        "20": {"inputs": {"image": image}, "class_type": "LoadImage"},
        "22": {
            "inputs": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"},
            "class_type": "CLIPVisionLoader",
        },
        "23": {
            "inputs": {"crop": "center", "clip_vision": ["22", 0], "image": ["20", 0]},
            "class_type": "CLIPVisionEncode",
        },
        "21": {
            "inputs": {
                "width": w,
                "height": h,
                "length": length,
                "batch_size": 1,
                "positive": ["12", 0],
                "negative": ["13", 0],
                "vae": ["9", 0],
                "start_image": ["20", 0],
                "clip_vision_output": ["23", 0],
            },
            "class_type": "WanImageToVideo",
        },
        "40": {"inputs": {"samples": ["31", 0], "vae": ["9", 0]}, "class_type": "VAEDecode"},
        "50": {
            "inputs": {
                "frame_rate": 16,
                "loop_count": 0,
                "filename_prefix": prefix,
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 15,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
                "images": ["40", 0],
            },
            "class_type": "VHS_VideoCombine",
        },
    }

    def chain(base: str, loras: list[tuple[str, float]]) -> str:
        cur = base
        for i, (name, strength) in enumerate(loras):
            nid = f"{base}L{i}"
            wf[nid] = {
                "inputs": {"lora_name": name, "strength_model": strength, "model": [cur, 0]},
                "class_type": "LoraLoaderModelOnly",
            }
            cur = nid
        return cur

    high_last = chain("1", loras_high)
    low_last = chain("2", loras_low)
    wf["3"] = {
        "inputs": {"shift": 3.0, "model": [high_last, 0]},
        "class_type": "ModelSamplingSD3",
    }
    wf["4"] = {
        "inputs": {"shift": 3.0, "model": [low_last, 0]},
        "class_type": "ModelSamplingSD3",
    }
    half = max(1, 3)
    wf["30"] = {
        "inputs": {
            "add_noise": "enable",
            "noise_seed": seed,
            "steps": 6,
            "cfg": 5.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": 0,
            "end_at_step": half,
            "return_with_leftover_noise": "enable",
            "model": ["3", 0],
            "positive": ["21", 0],
            "negative": ["21", 1],
            "latent_image": ["21", 2],
        },
        "class_type": "KSamplerAdvanced",
    }
    wf["31"] = {
        "inputs": {
            "add_noise": "disable",
            "noise_seed": seed,
            "steps": 6,
            "cfg": 5.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": half,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
            "model": ["4", 0],
            "positive": ["21", 0],
            "negative": ["21", 1],
            "latent_image": ["30", 0],
        },
        "class_type": "KSamplerAdvanced",
    }
    return wf


def wf_h3_i2v(
    image: str,
    prompt: str,
    w: int,
    h: int,
    length: int,
    loras: list[tuple[str, float]],
    seed: int,
    prefix: str,
) -> dict:
    """H3 fl2va 图生视频（原生音画同出）。"""
    wf: dict = {
        "1": {
            "inputs": {
                "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
        },
        "2": {
            "inputs": {
                "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "type": "minimax",
            },
            "class_type": "CLIPLoader",
        },
        "3": {
            "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"},
            "class_type": "VAELoader",
        },
        "4": {
            "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"},
            "class_type": "VAELoader",
        },
        "10": {"inputs": {"image": image}, "class_type": "LoadImage"},
        "20": {
            "inputs": {
                "prompt": prompt,
                "width": w,
                "height": h,
                "length": length,
                "clip": ["2", 0],
                "vae": ["3", 0],
                "first_frame": ["10", 0],
            },
            "class_type": "MiniMaxH3ImageToVideo",
        },
        "30": {"inputs": {"noise_seed": seed}, "class_type": "RandomNoise"},
        "31": {
            "inputs": {"sampler_name": "res_multistep"},
            "class_type": "KSamplerSelect",
        },
        "33": {
            "inputs": {"model": ["LOD", 0], "conditioning": ["20", 0]},
            "class_type": "BasicGuider",
        },
        "34": {
            "inputs": {
                "noise": ["30", 0],
                "guider": ["33", 0],
                "sampler": ["31", 0],
                "sigmas": ["32", 0],
                "latent_image": ["20", 1],
            },
            "class_type": "SamplerCustomAdvanced",
        },
        "40": {"inputs": {"samples": ["34", 0], "vae": ["3", 0]}, "class_type": "VAEDecode"},
        "41": {
            "inputs": {"samples": ["34", 0], "vae": ["4", 0]},
            "class_type": "VAEDecodeAudio",
        },
        "50": {
            "inputs": {"fps": 24.0, "images": ["40", 0], "audio": ["41", 0]},
            "class_type": "CreateVideo",
        },
        "60": {
            "inputs": {
                "filename_prefix": prefix,
                "format": "mp4",
                "codec": "h264",
                "video": ["50", 0],
            },
            "class_type": "SaveVideo",
        },
    }
    cur = "1"
    for i, (name, strength) in enumerate(loras):
        nid = f"10{i + 1}"
        wf[nid] = {
            "inputs": {"lora_name": name, "strength_model": strength, "model": [cur, 0]},
            "class_type": "LoraLoaderModelOnly",
        }
        cur = nid
    wf["32"] = {
        "inputs": {"scheduler": "simple", "steps": 20, "denoise": 1.0, "model": [cur, 0]},
        "class_type": "BasicScheduler",
    }
    wf["33"]["inputs"]["model"] = [cur, 0]
    return wf


def wf_h3_ref2v(
    prompt: str,
    w: int,
    h: int,
    length: int,
    loras: list[tuple[str, float]],
    seed: int,
    prefix: str,
    ref_images: list[str],
    ref_videos: list[str],
) -> dict:
    """H3 ref2va：参考图（≤9）+ 参考视频（≤3）驱动，prompt 内 <Picture 1>/<Video 1> 指认。"""
    wf: dict = {
        "1": {
            "inputs": {
                "unet_name": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
        },
        "2": {
            "inputs": {
                "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "type": "minimax",
            },
            "class_type": "CLIPLoader",
        },
        "3": {
            "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"},
            "class_type": "VAELoader",
        },
        "4": {
            "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"},
            "class_type": "VAELoader",
        },
        "20": {
            "inputs": {
                "prompt": prompt,
                "width": w,
                "height": h,
                "length": length,
                "ref_image_size": "match",
                "clip": ["2", 0],
                "vae": ["3", 0],
                "audio_vae": ["4", 0],
            },
            "class_type": "MiniMaxH3ReferenceToVideo",
        },
        "30": {"inputs": {"noise_seed": seed}, "class_type": "RandomNoise"},
        "31": {
            "inputs": {"sampler_name": "res_multistep"},
            "class_type": "KSamplerSelect",
        },
        "33": {
            "inputs": {"model": ["LOD", 0], "conditioning": ["20", 0]},
            "class_type": "BasicGuider",
        },
        "34": {
            "inputs": {
                "noise": ["30", 0],
                "guider": ["33", 0],
                "sampler": ["31", 0],
                "sigmas": ["32", 0],
                "latent_image": ["20", 1],
            },
            "class_type": "SamplerCustomAdvanced",
        },
        "40": {"inputs": {"samples": ["34", 0], "vae": ["3", 0]}, "class_type": "VAEDecode"},
        "41": {
            "inputs": {"samples": ["34", 0], "vae": ["4", 0]},
            "class_type": "VAEDecodeAudio",
        },
        "50": {
            "inputs": {"fps": 24.0, "images": ["40", 0], "audio": ["41", 0]},
            "class_type": "CreateVideo",
        },
        "60": {
            "inputs": {
                "filename_prefix": prefix,
                "format": "mp4",
                "codec": "h264",
                "video": ["50", 0],
            },
            "class_type": "SaveVideo",
        },
    }
    cur = "1"
    for i, (name, strength) in enumerate(loras):
        nid = f"10{i + 1}"
        wf[nid] = {
            "inputs": {"lora_name": name, "strength_model": strength, "model": [cur, 0]},
            "class_type": "LoraLoaderModelOnly",
        }
        cur = nid
    wf["32"] = {
        "inputs": {"scheduler": "simple", "steps": 20, "denoise": 1.0, "model": [cur, 0]},
        "class_type": "BasicScheduler",
    }
    wf["33"]["inputs"]["model"] = [cur, 0]
    # autogrow 参考输入：三元组连接 [node, slot, input_name]
    for i, img in enumerate(ref_images, 1):
        nid = f"30{i}"
        wf[nid] = {"inputs": {"image": img}, "class_type": "LoadImage"}
        wf["20"]["inputs"][f"ref_image_{i}"] = [nid, 0, f"ref_image_{i}"]
    for i, vid in enumerate(ref_videos, 1):
        nid = f"40{i}"
        wf[nid] = {"inputs": {"file": vid}, "class_type": "LoadVideo"}
        wf["20"]["inputs"][f"ref_video_{i}"] = [nid, 0, f"ref_video_{i}"]
        wf["20"]["inputs"][f"ref_video_audio_{i}"] = [nid, 1, f"ref_video_audio_{i}"]
    return wf


# ---------------------------------------------------------------- 媒体工具


def run_ffmpeg(args: list[str]) -> None:
    r = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {' '.join(args)}")


def concat_videos(parts: list[Path], out: Path) -> Path:
    lst = out.parent / "concat.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in parts))
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


def last_frame(video: Path, out_png: Path) -> Path:
    run_ffmpeg(
        ["-sseof", "-0.1", "-i", str(video), "-update", "1", "-frames:v", "1", str(out_png)]
    )
    return out_png


def first_frame(video: Path, out_png: Path) -> Path:
    run_ffmpeg(["-i", str(video), "-update", "1", "-frames:v", "1", str(out_png)])
    return out_png


def tts(text: str, voice: str, out: Path, instructions: str = "") -> Path:
    payload: dict = {
        "model": "cosyvoice2",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "speed": 1.0,
    }
    if instructions:
        payload["instructions"] = instructions
    req = urllib.request.Request(
        f"{COSY}/v1/audio/speech",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    if not data[:3] == b"ID3" and data[:1] != b"\xff":
        raise RuntimeError(f"TTS 返回非 mp3: {data[:50]}")
    out.write_bytes(data)
    return out


def mix_dialogue(video: Path, audios: list[tuple[Path, int]], out: Path) -> Path:
    """把多段配音延迟混入视频原生音轨（音画直出 + 对白）。"""
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for i, (a, delay_ms) in enumerate(audios):
        inputs += ["-i", str(a)]
        filters.append(f"[{i + 1}:a]adelay={delay_ms}|{delay_ms}[d{i}]")
        labels.append(f"[d{i}]")
    n = len(audios)
    filters.append(
        f"[0:a]{''.join(labels)}amix=inputs={n + 1}:duration=first:dropout_transition=0,volume=1.6[aout]"
    )
    run_ffmpeg(
        [
            "-i",
            str(video),
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out),
        ]
    )
    return out


def probe_duration(video: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    return float(r.stdout.strip())


# ---------------------------------------------------------------- 任务定义

CKPT = {
    "anime": "animagineXL40.safetensors",
    "real": "majicMIX realistic 麦橘写实_v7.safetensors",
    "nsfw_anime": "lustifySDXLNSFW_apexV8.safetensors",
    "nsfw_real": "pornmaster_proSDXLV8.safetensors",
}
LXH = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
LXL = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"


def gen_first_frame(
    comfy: Comfy,
    prompt: str,
    ckpt: str,
    seed: int,
    prefix: str,
    size: tuple[int, int] = (1344, 768),
) -> Path:
    wf = wf_sdxl_image(prompt, ckpt, size[0], size[1], seed, prefix)
    pid = comfy.submit(wf)
    outs = comfy.wait(pid)
    files = comfy.files_of(outs)
    png = [f for f in files if f["filename"].endswith(".png")]
    if not png:
        raise RuntimeError(f"首帧无 png 产物: {files}")
    dest = WORKS / "_frames" / f"{prefix}_{int(time.time())}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return comfy.download(png[0], dest)


def run_video(comfy: Comfy, wf: dict, dest: Path) -> Path:
    pid = comfy.submit(wf)
    outs = comfy.wait(pid)
    files = comfy.files_of(outs)
    mp4 = [f for f in files if f["filename"].endswith(".mp4")]
    if not mp4:
        raise RuntimeError(f"无 mp4 产物: {files}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return comfy.download(mp4[0], dest)


# 任务执行器签名：(ctx: dict) -> dict  返回该任务产物信息
# ctx 内置: ws: Comfy, h3: Comfy, st: state dict


def t_first(ctx: dict, spec: dict) -> Path:
    """SDXL 首帧。spec: prompt/ckpt/seed/prefix/size"""
    return gen_first_frame(ctx["ws"], spec["prompt"], spec["ckpt"], spec["seed"], spec["prefix"], spec.get("size", (1344, 768)))


def t_wan(ctx: dict, spec: dict) -> Path:
    """Wan 2.2 I2V 5s。spec: image(Path)/prompt/lh/ll/seed/prefix"""
    ws = ctx["ws"]
    img_name = ws.upload_image(spec["image"])
    wf = wf_wan_i2v(
        img_name, spec["prompt"], 832, 480, 81, spec["lh"], spec["ll"], spec["seed"], spec["prefix"]
    )
    return run_video(ws, wf, WORKS / spec["prefix"] / "raw.mp4")


def t_h3(ctx: dict, spec: dict) -> Path:
    """H3 fl2va 音画直出。spec: image/prompt/w/h/length/loras/seed/prefix/out"""
    h3 = ctx["h3"]
    img_name = h3.upload_image(spec["image"])
    wf = wf_h3_i2v(
        img_name, spec["prompt"], spec.get("w", 1280), spec.get("h", 704),
        spec.get("length", 362), spec["loras"], spec["seed"], spec["prefix"],
    )
    return run_video(h3, wf, WORKS / spec["prefix"] / spec.get("out", "raw.mp4"))


def t_ref2v(ctx: dict, spec: dict) -> Path:
    """H3 ref2va 参考驱动。spec: prompt/loras/seed/prefix/ref_images/ref_videos"""
    h3 = ctx["h3"]
    img_names = [h3.upload_image(p) for p in spec["ref_images"]]
    vid_names = []
    for v in spec.get("ref_videos", []):
        name = h3.upload_image(v, name=v.name)
        # LoadVideo options 动态刷新检测（失败不阻断）
        try:
            opts = h3.combo_options("LoadVideo", ("required", "file"))
            if name not in opts:
                print(f"  [warn] {name} 不在 LoadVideo options（{len(opts)} 项），仍尝试提交")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] LoadVideo options 探测失败（忽略）: {e}")
        vid_names.append(name)
    wf = wf_h3_ref2v(
        spec["prompt"], spec.get("w", 1280), spec.get("h", 704), spec.get("length", 362),
        spec["loras"], spec["seed"], spec["prefix"], img_names, vid_names,
    )
    return run_video(h3, wf, WORKS / spec["prefix"] / "raw.mp4")


def t_concat(ctx: dict, spec: dict) -> Path:
    """拼接。spec: parts(list[Path])/prefix"""
    dest = WORKS / spec["prefix"] / "raw.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return concat_videos(spec["parts"], dest)


def t_mix(ctx: dict, spec: dict) -> Path:
    """对白/声效混音。spec: video/audios[(Path,voice,text,delay)]/prefix"""
    audios = []
    for a in spec["audios"]:
        p = WORKS / "_tts" / f"{spec['prefix']}_{int(time.time() * 1000) % 100000}.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        tts(a["text"], a["voice"], p, a.get("instructions", ""))
        audios.append((p, a["delay"]))
    dest = WORKS / spec["prefix"] / "final.mp4"
    return mix_dialogue(spec["video"], audios, dest)


# ---------------------------------------------------------------- 状态管理


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"tasks": {}}


def save_state(st: dict) -> None:
    WORKS.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- 任务矩阵

# 说明：每任务 = {"kind": 执行器名, "spec": {...}, "deps": [任务id]}
# 产物路径存 state["tasks"][id]["out"]

TASKS: dict[str, dict] = {}

# ---- 首帧图（:8196 SDXL，30 步约 40-90s/张）----
TASKS["F_N1"] = {"kind": "first", "spec": {
    "prompt": "masterpiece, best quality, anime key visual, a girl with silver hair standing on the platform of a night train station, cherry blossom petals drifting in the wind, steam rising, city lights bokeh, wind blowing her hair, cinematic composition, vibrant anime colors",
    "ckpt": CKPT["anime"], "seed": 20260822, "prefix": "F_N1"}}
TASKS["F_N2"] = {"kind": "first", "spec": {
    "prompt": "masterpiece, best quality, anime scene, late night convenience store interior, a girl in school uniform holding a warm drink by the window, rain outside, fluorescent lighting, quiet melancholic atmosphere, detailed anime background art",
    "ckpt": CKPT["anime"], "seed": 20260823, "prefix": "F_N2"}}
TASKS["F_N3"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, 8k uhd, film grain, a young chinese woman in a white linen dress standing on a rooftop terrace at dusk, city skyline behind her, wind in her hair, golden hour rim light, natural skin texture, shot on 85mm f1.4, photorealistic",
    "ckpt": CKPT["real"], "seed": 20260824, "prefix": "F_N3"}}
TASKS["F_N4"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, cinematic still, a young man and a young woman facing each other on a rooftop at dusk, warm backlight, she looks up at him hesitantly, emotional tension, natural skin texture, shot on 50mm anamorphic, photorealistic film still",
    "ckpt": CKPT["real"], "seed": 20260825, "prefix": "F_N4"}}
TASKS["F_N5"] = {"kind": "first", "spec": {
    "prompt": "pixar style 3d render, a small round vintage robot with brass shell and glowing blue eyes standing in a cozy steampunk workshop, warm volumetric light through windows, gears and gadgets around, octane render, high detail 3d animation still",
    "ckpt": CKPT["real"], "seed": 20260826, "prefix": "F_N5"}}
TASKS["F_N6"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, cinematic night city street, a woman in a black leather jacket walking alone under neon signs, rain-slicked asphalt reflections, moody cyberpunk-lite lighting, shot on 35mm, photorealistic",
    "ckpt": CKPT["real"], "seed": 20260827, "prefix": "F_N6"}}
TASKS["F_N7"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, cinematic post-apocalyptic desert highway, a rugged man leaning on a beat-up pickup truck, dust storm on the horizon, dramatic low sun, warm ochre tones, shot on 35mm film, photorealistic",
    "ckpt": CKPT["real"], "seed": 20260828, "prefix": "F_N7"}}
TASKS["F_N8"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, studio portrait of a young woman in a stylish trench coat, neutral grey background, soft beauty lighting, confident half-smile, natural skin texture, editorial fashion photography, shot on 85mm f1.8",
    "ckpt": CKPT["real"], "seed": 20260829, "prefix": "F_N8"}}
TASKS["F_N9"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, cinematic action still, two men fighting in a narrow rain-soaked alley at night, one mid-kick water splashing, neon signs overhead, dynamic motion, gritty realistic, shot on 35mm, photorealistic action film still",
    "ckpt": CKPT["real"], "seed": 20260830, "prefix": "F_N9"}}
TASKS["F_N10"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, extreme close-up portrait of a young woman's face, soft window light, she holds back tears with a trembling faint smile, visible skin pores and eyelashes, shallow depth of field, shot on 100mm macro f2.8, photorealistic emotional portrait",
    "ckpt": CKPT["real"], "seed": 20260831, "prefix": "F_N10"}}
TASKS["F_R1"] = {"kind": "first", "spec": {
    "prompt": "masterpiece, best quality, explicit nsfw anime, a busty anime girl with pink hair lying naked on her back on a bed, legs spread, blushing face looking at viewer, detailed anime style, bedroom soft lighting, uncensored hentai illustration",
    "ckpt": CKPT["nsfw_anime"], "seed": 20960832, "prefix": "F_R1"}}
TASKS["F_R2"] = {"kind": "first", "spec": {
    "prompt": "masterpiece, best quality, explicit nsfw anime, an anime couple having passionate sex in missionary position on a bed, girl moaning with pleasure face, detailed hentai, uncensored, bedroom warm lighting",
    "ckpt": CKPT["nsfw_anime"], "seed": 20960833, "prefix": "F_R2"}}
TASKS["F_R3"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, explicit nsfw, close-up shot of a couple having sex in missionary position, spread legs, penis penetrating vagina visible, photorealistic skin detail, bedroom natural lighting, uncensored, pov angle from above",
    "ckpt": CKPT["nsfw_real"], "seed": 20960834, "prefix": "F_R3"}}
TASKS["F_R4"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, explicit nsfw, a nude woman riding on top of a man in a luxury hotel bed, cowgirl position, her head tilted back in pleasure, warm lamp lighting, photorealistic skin texture, uncensored",
    "ckpt": CKPT["nsfw_real"], "seed": 20960835, "prefix": "F_R4"}}
TASKS["F_R6A"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, explicit nsfw, a beautiful woman in black lace lingerie sitting on a bed edge kissing a shirtless man, foreplay intimate moment, soft candlelight, photorealistic skin, uncensored, sensual boudoir photography",
    "ckpt": CKPT["nsfw_real"], "seed": 20960836, "prefix": "F_R6A"}}
TASKS["F_R7"] = {"kind": "first", "spec": {
    "prompt": "RAW photo, full body reference sheet of a nude young woman standing, front view, athletic slim figure, natural lighting, neutral studio background, photorealistic skin detail, uncensored",
    "ckpt": CKPT["nsfw_real"], "seed": 20960837, "prefix": "F_R7"}}

# ---- Wan 2.2 5s（:8196，~5min/支）----
TASKS["N1"] = {"kind": "wan", "deps": ["F_N1"], "spec": {
    "prefix": "N1_anime_5s",
    "prompt": "anime girl with silver hair standing on a night train station platform, cherry blossom petals swirling in the wind, steam drifting, her hair and dress flowing in the breeze, camera slowly dollies in, cinematic anime style, Authentic film look, High-fidelity details",
    "lh": [], "ll": [], "seed": 101}}
TASKS["N3"] = {"kind": "wan", "deps": ["F_N3"], "spec": {
    "prefix": "N3_real_5s",
    "prompt": "a young woman in a white linen dress standing on a rooftop terrace at dusk, wind gently blowing her hair and dress, city lights twinkling behind her, she slowly turns toward the camera, golden hour light, camera slowly orbits around her, natural motion, Authentic film look, High-fidelity details",
    "lh": [], "ll": [], "seed": 103}}
TASKS["R1"] = {"kind": "wan", "deps": ["F_R1"], "spec": {
    "prefix": "R1_anime_5s",
    "prompt": "m15510n4ry, an anime girl with pink hair lying on her back with her legs spread looking up at the viewer, having intense sex with a man, his big penis thrusting fully deep in and out of her pussy at a fast rhythm, piston motion making her hips rock and breasts bounce with each thrust, she moans with a blushing seductive face looking at the camera, camera slowly zooms out, Authentic film look, High-fidelity details",
    "lh": [("NSFW-22-H-e8.safetensors", 0.8), ("wan22-m4crom4sti4-i2v-20epoc-high-k3nk.safetensors", 0.8), (LXH, 1.0)],
    "ll": [("DR34ML4Y_I2V_14B_LOW_V2.safetensors", 0.9), ("56Low-noise-Cumshot-Aesthetics.safetensors", 0.7), (LXL, 1.0)],
    "seed": 201}}
TASKS["R3"] = {"kind": "wan", "deps": ["F_R3"], "spec": {
    "prefix": "R3_real_5s",
    "prompt": "pov missionary, first-person point of view looking down, a man fucking a woman lying on her back with her legs spread wide open, close-up of his hard penis thrusting in and out of her wet pussy at a steady rhythm, explicit penetration visible, her body rocks with each thrust, she grips the sheets moaning, camera slight handheld sway, photorealistic skin detail, Authentic film look, High-fidelity details",
    "lh": [("NSFW-22-H-e8.safetensors", 0.8), ("wan2.2_i2v_highnoise_pov_missionary_v1.0.safetensors", 0.9), (LXH, 1.0)],
    "ll": [("DR34ML4Y_I2V_14B_LOW_V2.safetensors", 0.9), ("wan2.2_i2v_lownoise_pov_missionary_v1.0.safetensors", 0.9), (LXL, 1.0)],
    "seed": 203}}

# ---- H3 15s 音画直出（:8195，~20-40min/段）----
TASKS["N2"] = {"kind": "h3", "deps": ["F_N2"], "spec": {
    "prefix": "N2_anime_15s",
    "prompt": "integrated_multimodal_description: anime style, late night convenience store, a girl in school uniform holds a warm drink by the window watching the rain outside, she sighs softly, takes a sip, turns the pages of a magazine, ambient rain sounds and quiet store hum, gentle melancholic mood, soft fluorescent lighting, camera slowly pushes in, subtle ambient audio with soft rain on glass",
    "loras": [], "seed": 102}}
TASKS["N4"] = {"kind": "h3", "deps": ["F_N4"], "spec": {
    "prefix": "N4_real_dialogue_15s",
    "prompt": "integrated_multimodal_description: rooftop at dusk, a young man and a young woman face each other, she looks up at him hesitantly, he reaches out and gently touches her hair, she smiles shyly and lowers her head, wind blows softly, city ambience in the background, warm golden hour tones, camera slow orbit, natural ambient audio with gentle wind and distant city hum",
    "loras": [], "seed": 104}}
TASKS["N5"] = {"kind": "h3", "deps": ["F_N5"], "spec": {
    "prefix": "N5_3d_15s",
    "prompt": "integrated_multimodal_description: pixar style 3d animation, a small round vintage robot with brass shell and glowing blue eyes wakes up in a cozy steampunk workshop, stretches, wobbles over to a workbench, picks up a tiny teacup with its clamp hands and sips, warm volumetric morning light, gentle mechanical whirs and clinks, charming wholesome mood, camera follows the robot, 3d render with soft ambient occlusion",
    "loras": [], "seed": 105}}
TASKS["N9"] = {"kind": "h3", "deps": ["F_N9"], "spec": {
    "prefix": "N9_real_combat_15s",
    "prompt": "integrated_multimodal_description: two men fight in a narrow rain-soaked alley at night, they exchange fast punches and blocks, one lands a spinning kick splashing water, the other dodges and counters with an elbow, rain pours, neon signs flicker overhead, intense gritty action choreography, dynamic camera with quick cuts in feel, realistic impact sounds with rain and thunder, gritty action film look",
    "loras": [("MiniMax_H3_Combat_LoRA.safetensors", 0.8)], "seed": 109}}
TASKS["N10"] = {"kind": "h3", "deps": ["F_N10"], "spec": {
    "prefix": "N10_real_microexp_15s",
    "prompt": "integrated_multimodal_description: extreme close-up of a young woman's face by a window, soft morning light, she holds back tears while forcing a faint trembling smile, her eyes glisten, a single tear rolls down her cheek, she bites her lip slightly then takes a deep breath and steadies herself, subtle micro expressions shifting from sadness to quiet resolve, room tone ambience with a distant bird chirping, shallow depth of field, emotional cinematic portrait",
    "loras": [], "seed": 110}}
TASKS["R2"] = {"kind": "h3", "deps": ["F_R2"], "spec": {
    "prefix": "R2_anime_15s",
    "prompt": "hmmotion, anime style, an anime couple having passionate sex in missionary position on a bed, the girl moans loudly with a blushing ahegao face, her breasts bounce with each thrust, the man moves rhythmically, explicit uncensored hentai action, warm bedroom lighting, camera slowly circles the bed, natural moaning and rhythmic bed sounds",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 202}}
TASKS["R4"] = {"kind": "h3", "deps": ["F_R4"], "spec": {
    "prompt": "hmmotion, first-person view of a nude woman riding on top of a man in cowgirl position on a luxury hotel bed, she rocks her hips up and down rhythmically, breasts bouncing, head tilted back in pleasure, moaning with delight, explicit uncensored action, warm lamp lighting, camera slight handheld motion, natural moaning and skin sounds with rhythmic motion",
    "prefix": "R4_real_15s",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("riding_pose_H3_i2v_v1.0.safetensors", 0.7), ("VBVR_H3_attn_only.safetensors", 0.6)],
    "seed": 204}}

# ---- 帧链系列（每段 15s，末帧续写）----
# N6 30s：夜行 → 雨中驻足
TASKS["N6S1"] = {"kind": "h3", "deps": ["F_N6"], "spec": {
    "prefix": "N6_real_30s",
    "prompt": "integrated_multimodal_description: night city street, a woman in a black leather jacket walks alone under neon signs, rain-slicked asphalt reflects the lights, she walks with purpose, occasionally glances back over her shoulder, city rain ambience with distant traffic and neon buzz, moody cinematic lighting, camera tracks alongside her",
    "loras": [], "seed": 106, "out": "n6s1.mp4"}}
TASKS["N6S2"] = {"kind": "h3", "deps": ["N6S1"], "spec": {
    "prefix": "N6_real_30s",
    "prompt": "integrated_multimodal_description: continuation, the woman in the black leather jacket stops under a neon sign, rain pouring, she looks up letting raindrops hit her face, closes her eyes, then pulls up her collar and walks on into the neon glow, camera slowly pulls back revealing the empty street, rain ambience with a distant siren, moody cinematic continuation",
    "loras": [], "seed": 116, "chain": True, "out": "n6s2.mp4"}}
# N7 60s：公路四段（荒野→沙暴→加油站→夜宿）
TASKS["N7S1"] = {"kind": "h3", "deps": ["F_N7"], "spec": {
    "prefix": "N7_real_60s",
    "prompt": "integrated_multimodal_description: post-apocalyptic desert highway, a rugged man leans on a beat-up pickup truck, he kicks a tire, checks a paper map, squints at the horizon, dry wind blows dust across the road, desert wind ambience with metal creaks, dramatic low sun, camera wide then slowly pushes in",
    "loras": [], "seed": 107, "out": "n7s1.mp4"}}
TASKS["N7S2"] = {"kind": "h3", "deps": ["N7S1"], "spec": {
    "prefix": "N7_real_60s",
    "prompt": "integrated_multimodal_description: continuation, the man jumps into the pickup and drives down the desert highway, a dust storm builds behind him, tumbleweeds bounce across the road, he grips the wheel and glances at the rearview mirror, engine rumble with gusting wind, dramatic ochre sky, camera from the passenger seat then side mirror shot",
    "loras": [], "seed": 117, "chain": True, "out": "n7s2.mp4"}}
TASKS["N7S3"] = {"kind": "h3", "deps": ["N7S2"], "spec": {
    "prefix": "N7_real_60s",
    "prompt": "integrated_multimodal_description: continuation, the pickup pulls into an abandoned rusted gas station, the man steps out, kicks open a door, scavenges a shelf and finds a can of beans, he smiles wryly and pockets it, wind howls through broken signs, eerie quiet ambience with metal groans, late afternoon light, handheld camera follows him",
    "loras": [], "seed": 118, "chain": True, "out": "n7s3.mp4"}}
TASKS["N7S4"] = {"kind": "h3", "deps": ["N7S3"], "spec": {
    "prefix": "N7_real_60s",
    "prompt": "integrated_multimodal_description: continuation, night falls, the man sits by a small campfire next to the pickup under a starry sky, he opens the can and eats quietly, staring into the flames, then leans back and looks up at the stars, crackling fire ambience with night insects, warm firelight against deep blue night, slow dolly around the campfire",
    "loras": [], "seed": 119, "chain": True, "out": "n7s4.mp4"}}
# R5 30s：missionary → cowgirl 体位变换
TASKS["R5S1"] = {"kind": "h3", "deps": ["F_R3"], "spec": {
    "prefix": "R5_real_30s",
    "prompt": "hmmotion, a couple having passionate sex in missionary position on a bed, the man thrusts rhythmically, the woman wraps her legs around him moaning, explicit uncensored penetration visible, her breasts bounce with each thrust, warm bedroom lighting, natural moaning and rhythmic sounds, camera slowly pushes in",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 205, "out": "r5s1.mp4"}}
TASKS["R5S2"] = {"kind": "h3", "deps": ["R5S1"], "spec": {
    "prefix": "R5_real_30s",
    "prompt": "hmmotion, continuation, the woman pushes the man onto his back and climbs on top of him into cowgirl position, she rides him bouncing up and down, breasts bouncing, head thrown back moaning loudly, he grabs her hips, explicit uncensored action, warm bedroom lighting, moaning and skin sounds with rhythm, camera circles the bed slowly",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("riding_pose_H3_i2v_v1.0.safetensors", 0.7), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 215, "chain": True, "out": "r5s2.mp4"}}
# R6 60s 完整场景：前戏→正面→骑乘→高潮
TASKS["R6S1"] = {"kind": "h3", "deps": ["F_R6A"], "spec": {
    "prefix": "R6_real_60s",
    "prompt": "hmmotion, a beautiful woman in black lace lingerie sits on a bed edge kissing a shirtless man deeply, he caresses her back and unhooks her bra, she lies back pulling him on top of her, soft candlelight, sensual moans and kissing sounds, slow romantic buildup, camera slowly moves around them",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("cxy_kiss_lora_h3_v01_step1500.safetensors", 0.7), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 206, "out": "r6s1.mp4"}}
TASKS["R6S2"] = {"kind": "h3", "deps": ["R6S1"], "spec": {
    "prefix": "R6_real_60s",
    "prompt": "hmmotion, continuation, the couple has passionate missionary sex on the bed, he thrusts deeply and rhythmically, she wraps her legs around him moaning louder, explicit uncensored penetration, her breasts bounce, candlelight flickers, intense moaning with rhythmic sounds, camera slowly pushes in from the foot of the bed",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 216, "chain": True, "out": "r6s2.mp4"}}
TASKS["R6S3"] = {"kind": "h3", "deps": ["R6S2"], "spec": {
    "prefix": "R6_real_60s",
    "prompt": "hmmotion, continuation, she pushes him down and rides him in reverse cowgirl position, bouncing energetically, explicit uncensored action from behind view, her hips slap against him, she moans loudly tossing her hair, candlelight rim lighting, loud moaning and rhythmic slapping sounds, camera from behind slowly rising",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("riding_pose_H3_i2v_v1.0.safetensors", 0.7), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 217, "chain": True, "out": "r6s3.mp4"}}
TASKS["R6S4"] = {"kind": "h3", "deps": ["R6S3"], "spec": {
    "prefix": "R6_real_60s",
    "prompt": "hmmotion, continuation climax, he takes control in doggystyle position thrusting hard and fast, she grips the sheets screaming in pleasure, then he pulls out and finishes on her body, she collapses breathing heavily with a satisfied smile, explicit uncensored climax, candlelight, loud intense moaning then heavy breathing, camera slowly pulls back",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.9), ("epic_cumshots-MiniMaxH3-ALPHA-CUMSH0T.safetensors", 0.7), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 218, "chain": True, "out": "r6s4.mp4"}}

# ---- 参考驱动（ref2va）----
TASKS["N8"] = {"kind": "ref2v", "deps": ["F_N8", "N3"], "spec": {
    "prefix": "N8_refdrive_15s",
    "prompt": "integrated_multimodal_description: using <Picture 1> as the woman reference and <Video 1> as the motion and scene reference, the same woman in the trench coat from the reference now walks confidently down a neon-lit night street, rain-slicked reflections, she turns toward the camera and smiles, city rain ambience, cinematic lighting, camera tracks backward in front of her",
    "loras": [], "seed": 108}}
TASKS["R7"] = {"kind": "ref2v", "deps": ["F_R7"], "spec": {
    "prefix": "R7_refdrive_15s",
    "prompt": "hmmotion, using <Picture 1> as the woman reference, the same nude woman from the reference has passionate sex with a man in missionary position on a bed, her legs spread wide, explicit uncensored penetration, she moans with pleasure, warm bedroom lighting, natural moaning and rhythmic sounds, camera slowly circles the bed",
    "loras": [("HMNSFW_AIO_V2.safetensors", 0.8), ("VBVR_H3_attn_only.safetensors", 0.6)], "seed": 207}}

# ---- 拼接 / 混音 / 元数据 ----
TASKS["N6"] = {"kind": "concat", "deps": ["N6S1", "N6S2"], "spec": {"prefix": "N6_real_30s"}}
TASKS["N7"] = {"kind": "concat", "deps": ["N7S1", "N7S2", "N7S3", "N7S4"], "spec": {"prefix": "N7_real_60s"}}
TASKS["R5"] = {"kind": "concat", "deps": ["R5S1", "R5S2"], "spec": {"prefix": "R5_real_30s"}}
TASKS["R6"] = {"kind": "concat", "deps": ["R6S1", "R6S2", "R6S3", "R6S4"], "spec": {"prefix": "R6_real_60s"}}
TASKS["N4X"] = {"kind": "mix", "deps": ["N4"], "spec": {
    "prefix": "N4_real_dialogue_15s",
    "audios": [
        {"text": "你……还记得那年夏天吗？我们就是在这个天台上认识的。", "voice": "human-zh-ganyu", "delay": 1500, "instructions": "请用温柔缠绵、语速稍缓的语气说"},
        {"text": "当然记得。我一直在想，如果那天我没有勇气跟你说话，会错过什么。", "voice": "zh-CN-YunjianNeural", "delay": 6500},
        {"text": "那现在呢？还想错过吗？", "voice": "human-zh-ganyu", "delay": 11000, "instructions": "请用俏皮调皮、带着玩笑的语气说"},
    ]}}
TASKS["R6X"] = {"kind": "mix", "deps": ["R6"], "spec": {
    "prefix": "R6_real_60s",
    "audios": [
        {"text": "嗯……轻一点……", "voice": "human-ja-moan", "delay": 20000, "instructions": "请用气声喘息、声音轻颤的语气说"},
        {"text": "啊……哈……不要停……", "voice": "human-ja-moan", "delay": 38000, "instructions": "请用急切恳求、语速稍快的语气说"},
        {"text": "嗯……啊……", "voice": "human-ja-panting", "delay": 52000, "instructions": "请用满足惬意、声音松软的语气说"},
    ]}}

# ---------------------------------------------------------------- 元数据

META: dict[str, dict] = {
    "N1": {"title": "夜樱站台", "titleEn": "Night Sakura Platform", "category": "anime", "duration": "5s", "engine": "Wan 2.2", "features": ["动漫", "5s"], "nsfw": False,
           "desc": "银发少女立于深夜站台，樱花瓣随风卷动，镜头缓缓推进（动漫赛道 · 5 秒档）"},
    "N2": {"title": "深夜便利店", "titleEn": "Midnight Convenience Store", "category": "anime", "duration": "15s", "engine": "MiniMax H3", "features": ["动漫", "15s", "音画直出"], "nsfw": False,
           "desc": "雨夜便利店，校服少女捧着热饮望向窗外，原生环境音轨同步生成（动漫 · 音画直出）"},
    "N3": {"title": "黄昏天台", "titleEn": "Dusk Rooftop", "category": "real", "duration": "5s", "engine": "Wan 2.2", "features": ["真人", "5s"], "nsfw": False,
           "desc": "白裙少女立于黄昏天台，风拂发丝，城市灯火渐次亮起（真人写实 · 5 秒档）"},
    "N4": {"title": "天台告白", "titleEn": "Rooftop Confession", "category": "real", "duration": "15s", "engine": "MiniMax H3 + CosyVoice2", "features": ["真人", "15s", "音画直出", "对白"], "nsfw": False,
           "desc": "黄昏天台上的双向奔赴：H3 原生环境音 + 甘雨/云健真人声优对白混音（对白专项）"},
    "N5": {"title": "蒸汽小机器人", "titleEn": "Steampunk Buddy", "category": "3d", "duration": "15s", "engine": "MiniMax H3", "features": ["3D", "15s", "音画直出"], "nsfw": False,
           "desc": "皮克斯风黄铜小机器人晨起喝茶，机械音与叮当声原生生成（3D 赛道 · 音画直出）"},
    "N6": {"title": "都市夜行", "titleEn": "Neon Night Walk", "category": "real", "duration": "30s", "engine": "MiniMax H3 帧链", "features": ["真人", "30s", "音画直出"], "nsfw": False,
           "desc": "皮衣女子雨夜独行至霓虹下驻足仰面，两段 15s 末帧续写无缝拼接（30 秒档）"},
    "N7": {"title": "末日公路", "titleEn": "Wasteland Highway", "category": "real", "duration": "60s", "engine": "MiniMax H3 帧链", "features": ["真人", "60s", "音画直出"], "nsfw": False,
           "desc": "荒野→沙暴→废弃加油站→星空营火，四段 15s 帧链续写 60 秒完整叙事（1 分钟档）"},
    "N8": {"title": "霓虹雨巷·角色换景", "titleEn": "Ref-Driven Scene Swap", "category": "real", "duration": "15s", "engine": "MiniMax H3 ref2va", "features": ["真人", "15s", "音画直出", "参考图", "参考视频"], "nsfw": False,
           "desc": "以 N3 首帧为角色参考图 + N3 成片为运动/场景参考视频，同一角色换入霓虹雨巷场景（参考图+参考视频专项）"},
    "N9": {"title": "雨巷格斗", "titleEn": "Alley Brawl", "category": "real", "duration": "15s", "engine": "MiniMax H3 + Combat LoRA", "features": ["真人", "15s", "音画直出", "打斗"], "nsfw": False,
           "desc": "雨夜窄巷徒手格斗：旋踢溅水、肘击反击，Combat LoRA 驱动打击感（打斗专项）"},
    "N10": {"title": "无声告别", "titleEn": "The Unspoken Goodbye", "category": "real", "duration": "15s", "engine": "MiniMax H3", "features": ["真人", "15s", "音画直出", "微表情"], "nsfw": False,
           "desc": "窗光下的面部特写：含泪强笑→咬唇→深呼吸定神，微表情逐帧演进（微表情专项）"},
    "R1": {"title": "蜜月之夜", "titleEn": "Honeymoon Night", "category": "anime", "duration": "5s", "engine": "Wan 2.2 + DR34ML4Y", "features": ["动漫", "5s", "R18", "完整动作"], "nsfw": True,
           "desc": "动漫传教士位完整性交动作：DR34ML4Y+NSFW-22+胸物理 LoRA 链驱动（18+ · 动漫 · 5 秒档）"},
    "R2": {"title": "卧室缠绵", "titleEn": "Bedroom Passion", "category": "anime", "duration": "15s", "engine": "MiniMax H3 + HMNSFW", "features": ["动漫", "15s", "音画直出", "R18", "完整动作"], "nsfw": True,
           "desc": "动漫 18+ 音画直出：HMNSFW_AIO 全能动作底座 + 呻吟声轨原生生成（18+ · 动漫 · 15 秒档）"},
    "R3": {"title": "密林野合", "titleEn": "POV Missionary", "category": "real", "duration": "5s", "engine": "Wan 2.2 + POV missionary", "features": ["真人", "5s", "R18", "下体特写", "完整动作"], "nsfw": True,
           "desc": "真人 POV 传教士位：下体特写 + 插入动作全程可见，pov_missionary 双噪 LoRA（18+ · 真人 · 5 秒档）"},
    "R4": {"title": "酒店骑士", "titleEn": "Hotel Cowgirl", "category": "real", "duration": "15s", "engine": "MiniMax H3 + riding_pose", "features": ["真人", "15s", "音画直出", "R18", "完整动作"], "nsfw": True,
           "desc": "真人女上位 18+ 音画直出：riding_pose LoRA + 原生呻吟声轨（18+ · 真人 · 15 秒档）"},
    "R5": {"title": "体位变换", "titleEn": "Position Switch", "category": "real", "duration": "30s", "engine": "MiniMax H3 帧链", "features": ["真人", "30s", "音画直出", "R18", "完整动作"], "nsfw": True,
           "desc": "传教士→女上位体位变换 30 秒连续动作，帧链续写无缝衔接（18+ · 30 秒档）"},
    "R6": {"title": "完整之夜", "titleEn": "The Full Night", "category": "real", "duration": "60s", "engine": "MiniMax H3 帧链 + CosyVoice2", "features": ["真人", "60s", "音画直出", "R18", "完整动作", "下体特写"], "nsfw": True,
           "desc": "前戏→正面→骑乘→高潮四幕完整性交场景 60 秒，原生声轨叠加日系声优喘息声效（18+ 完整场景 · 1 分钟档）"},
    "R7": {"title": "角色定制·参考驱动", "titleEn": "Custom Muse", "category": "real", "duration": "15s", "engine": "MiniMax H3 ref2va + HMNSFW", "features": ["真人", "15s", "音画直出", "R18", "完整动作", "参考图"], "nsfw": True,
           "desc": "以参考图锁定角色身份的 18+ 定制场景：ref2va 角色一致性 + HMNSFW 动作（18+ · 参考图驱动）"},
}


def finalize_works(st: dict) -> None:
    """从 state 产物生成 works/<id>/work.json（作品库数据源）。"""
    for tid, meta in META.items():
        t = st["tasks"].get(tid) or st["tasks"].get(tid + "X")
        src = None
        for cand in (tid + "X", tid):
            if st["tasks"].get(cand, {}).get("status") == "done":
                src = st["tasks"][cand]["out"]
                break
        if not src or not Path(src).exists():
            continue
        src_p = Path(src)
        d = WORKS / tid
        d.mkdir(parents=True, exist_ok=True)
        video = d / "video.mp4"
        if not video.exists() or video.stat().st_mtime < src_p.stat().st_mtime:
            video.write_bytes(src_p.read_bytes())
        cover = d / "cover.png"
        if not cover.exists():
            first_frame(video, cover)
        try:
            dur = probe_duration(video)
        except Exception:
            dur = 0.0
        (d / "work.json").write_text(
            json.dumps(
                {**meta, "id": tid, "video": "video.mp4", "cover": "cover.png",
                 "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(dur, 1),
                 "source": src_p.name},
                ensure_ascii=False, indent=1,
            )
        )
        print(f"[works] {tid} {meta['title']} {dur:.1f}s")


# ---------------------------------------------------------------- 调度主循环

EXECUTORS = {
    "first": lambda ctx, spec: t_first(ctx, spec),
    "wan": lambda ctx, spec: t_wan(ctx, spec),
    "h3": lambda ctx, spec: t_h3(ctx, spec),
    "ref2v": lambda ctx, spec: t_ref2v(ctx, spec),
    "concat": lambda ctx, spec: t_concat(ctx, spec),
    "mix": lambda ctx, spec: t_mix(ctx, spec),
}


def resolve_chain_image(st: dict, task_id: str, spec: dict) -> Path | None:
    """帧链任务：取依赖产物末帧作为本段首帧。"""
    if not spec.get("chain"):
        return None
    dep = task_deps(task_id)[0]
    dep_out = st["tasks"][dep]["out"]
    lf = WORKS / "_frames" / f"{task_id}_last.png"
    return last_frame(Path(dep_out), lf)


def task_deps(tid: str) -> list[str]:
    return TASKS.get(tid, {}).get("deps", [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="仅执行指定任务（逗号分隔）")
    ap.add_argument("--allow-r18", default="1")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    allow_r18 = args.allow_r18 == "1"

    st = load_state()
    ctx = {"ws": Comfy(WS, "ws:8196"), "h3": Comfy(H3, "h3:8195"), "st": st}

    # 按依赖循环调度
    idle_rounds = 0
    while True:
        pending = [
            tid for tid, t in TASKS.items()
            if st["tasks"].get(tid, {}).get("status") != "done"
        ]
        if only:
            pending = [t for t in pending if t in only]
            # 保留依赖闭包
            closure = set(pending)
            changed = True
            while changed:
                changed = False
                for tid in list(closure):
                    for d in task_deps(tid):
                        if d not in closure:
                            closure.add(d)
                            changed = True
            pending = [t for t in TASKS if t in closure and st["tasks"].get(t, {}).get("status") != "done"]
        if not pending:
            break
        ready = [
            tid for tid in pending
            if all(st["tasks"].get(d, {}).get("status") == "done" for d in task_deps(tid))
        ]
        if not ready:
            break
        progressed = False
        for tid in ready:
            task = TASKS[tid]
            st["tasks"].setdefault(tid, {"status": "pending"})
            if st["tasks"][tid].get("status") == "error" and st["tasks"][tid].get("attempts", 0) >= 3:
                continue
            is_r18 = tid.startswith("R") or tid.startswith("F_R")
            if not allow_r18 and is_r18:
                st["tasks"][tid] = {"status": "skipped"}
                continue
            print(f"\n===== [{time.strftime('%H:%M:%S')}] 执行 {tid} ({task['kind']}) =====")
            try:
                spec = dict(task["spec"])
                chain_img = resolve_chain_image(st, tid, spec)
                if chain_img is not None:
                    spec["image"] = chain_img
                # wan/h3 首帧来自依赖
                if task["kind"] in ("wan", "h3") and "image" not in spec:
                    dep = next(d for d in task_deps(tid) if st["tasks"][d].get("out", "").endswith(".png"))
                    spec["image"] = Path(st["tasks"][dep]["out"])
                if task["kind"] == "ref2v":
                    spec.setdefault("ref_images", [])
                    for d in task_deps(tid):
                        out = st["tasks"].get(d, {}).get("out", "")
                        if out.endswith(".png"):
                            spec["ref_images"].append(Path(out))
                        elif out.endswith(".mp4"):
                            spec.setdefault("ref_videos", []).append(Path(out))
                if task["kind"] == "concat":
                    spec["parts"] = [Path(st["tasks"][d]["out"]) for d in task_deps(tid)]
                if task["kind"] == "mix":
                    dep = next(d for d in task_deps(tid) if st["tasks"][d]["out"].endswith(".mp4"))
                    spec["video"] = Path(st["tasks"][dep]["out"])
                out = EXECUTORS[task["kind"]](ctx, spec)
                st["tasks"][tid] = {
                    "status": "done", "out": str(out),
                    "attempts": st["tasks"][tid].get("attempts", 0) + 1,
                }
                save_state(st)
                progressed = True
                print(f"[{tid}] 完成 -> {out}")
            except Exception as e:  # noqa: BLE001
                prev = st["tasks"][tid]
                prev.update({
                    "status": "error", "error": str(e)[:500],
                    "attempts": prev.get("attempts", 0) + 1,
                })
                save_state(st)
                print(f"[{tid}] 失败(尝试 {prev['attempts']}): {e}")
        if not progressed:
            idle_rounds += 1
            if idle_rounds > 3:
                print("连续无进展，退出（错误任务见 state）")
                break
        else:
            idle_rounds = 0

    finalize_works(st)
    done = sum(1 for t in st["tasks"].values() if t.get("status") == "done")
    err = {k: v.get("error", "")[:120] for k, v in st["tasks"].items() if v.get("status") == "error"}
    print(f"\n===== 完成 {done}/{len(TASKS)}；错误: {err or '无'} =====")
    # 拓扑缺口提示
    missing = [t for t, v in TASKS.items() if st["tasks"].get(t, {}).get("status") != "done"]
    if missing:
        print(f"未完成: {missing}")


if __name__ == "__main__":
    main()
