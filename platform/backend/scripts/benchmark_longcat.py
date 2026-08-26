"""M22.2 路线B对比实验 —— LongCat-Video 原生长视频基准测试（workstation 实机运行）。

与路线 A（H3 帧链续写，M21.2）对照的控制变量设计：
- 相同输入集：同一四幕叙事 prompt（雨夜街头女子，平静→警觉→惊惧→对峙），
  语义内容与 M21.2 CHUNK_PROMPTS 一致；LongCat 为原生 T2V 长视频，单条连续
  prompt 输入（路线 A 的一致性机制=定妆照参考图+帧链，路线 B 的一致性机制=
  原生长上下文，机制差异在报告中如实标注）
- 相同评估指标：MOS（VLM 维度评分均值）、FPS（生成帧数/壁钟）、资源消耗
  （GPU 显存/利用率、CPU、内存，2s 采样）
- 相同测试环境：workstation 同机同卡（GPU2），独立进程无并发任务

时长对齐：15fps × 840 帧 = 56s（路线 A 目标 56-58s）。LongCat 每段净增
num_frames - num_cond_frames = 93 - 13 = 80 帧，9 段 = 813 帧 = 54.2s，
10 段 = 893 帧 = 59.5s；取 10 段（59.5s，与 56-58s 偏差 +2.6%，FPS/资源
按秒归一化消除时长差）。

运行方式（workstation，torchrun 单卡）：
    cd /home/merlin/longcat-video
    CUDA_VISIBLE_DEVICES=2 .venv/bin/torchrun --nproc_per_node=1 \
        /path/to/benchmark_longcat.py \
        --checkpoint_dir /home/merlin/models/LongCat-Video \
        --out_dir /home/merlin/longcat_bench \
        --num_segments 10 [--skip_refine]

产出：
- {out_dir}/stage1_segment_{i}.mp4 / refine_segment_{i}.mp4（视频产物）
- {out_dir}/longcat_metrics.json（逐段耗时/FPS/资源采样序列/峰值汇总）
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import PIL.Image
import psutil
import torch
import torch.distributed as dist
from torchvision.io import write_video
from transformers import AutoConfig, AutoTokenizer, UMT5EncoderModel

from longcat_video.context_parallel import context_parallel_util
from longcat_video.context_parallel.context_parallel_util import init_context_parallel
from longcat_video.modules.autoencoder_kl_wan import AutoencoderKLWan
from longcat_video.modules.longcat_video_dit import LongCatVideoTransformer3DModel
from longcat_video.modules.scheduling_flow_match_euler_discrete import (
    FlowMatchEulerDiscreteScheduler,
)
from longcat_video.pipeline_longcat_video import LongCatVideoPipeline

# ---------------------------------------------------------------------------
# 与 M21.2 路线 A 相同的四幕叙事（合并为单条连续 prompt：路线B 原生输入形态）
# ---------------------------------------------------------------------------
BENCH_PROMPT = (
    "Cinematic vertical shot, rainy neon street at night: a young woman with long "
    "straight black hair in a red trench coat stands still under a flickering sign, "
    "calm expression, rain falling softly, camera slowly pushes in, wet pavement "
    "reflections. She senses someone following her, glances back over her shoulder "
    "with alert tension in her eyes, neon light shifts across her face, rain "
    "intensifies. She breaks into a run down the narrow alley, fear on her face, "
    "hair and red coat flowing with motion, puddles splashing, camera tracking "
    "alongside, motion blur. She stops abruptly, turns to face her pursuer, "
    "breathing hard, resolute and defiant expression, dramatic rim light from neon "
    "sign behind her, camera slowly circles to her front, rain easing."
)
BENCH_NEGATIVE = (
    "Bright tones, overexposed, static, blurred details, subtitles, style, works, "
    "paintings, images, static, overall gray, worst quality, low quality, JPEG "
    "compression residue, ugly, incomplete, extra fingers, poorly drawn hands, "
    "poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, "
    "still picture, messy background, three legs, many people in the background, "
    "walking backwards"
)

NUM_FRAMES = 93  # 每段总帧数（含条件帧）
NUM_COND_FRAMES = 13  # 段间重叠条件帧
FPS_OUT = 15


class _TextEncoderStub:
    """UMT5 轻量配置桩（预编码缓存模式专用）。

    pipeline._cache_clean_latents 需读取 text_encoder.config.d_model 构造空 embeds
    （skip_crs_attn=True，数值不被实际使用）；真实前向已被 encode_prompt 缓存覆盖。
    其他任何属性访问/调用说明 prompt 缓存未命中，立即报清晰错误而非静默算错。
    """

    def __init__(self, config) -> None:
        object.__setattr__(self, "config", config)

    def __call__(self, *args, **kwargs):
        raise RuntimeError(
            "文本编码器为配置桩（预编码缓存模式），禁止真实前向；"
            "prompt 缓存未命中，请用 --preencode_only 重新生成缓存"
        )

    def __getattr__(self, name: str):  # config 命中实例字典，不会走到这里
        raise RuntimeError(
            f"文本编码器为配置桩（预编码缓存模式），禁止访问 .{name}；"
            "prompt 缓存未命中，请用 --preencode_only 重新生成缓存"
        )


# ---------------------------------------------------------------------------
# 资源采样器（2s 周期：GPU 显存/利用率 + CPU/内存）
# ---------------------------------------------------------------------------
class ResourceSampler(threading.Thread):
    def __init__(self, gpu_index: int, interval: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.gpu_index = gpu_index
        self.interval = interval
        self.samples: list[dict] = []
        self._stop_event = threading.Event()
        self._proc = psutil.Process(os.getpid())

    def run(self) -> None:
        children_base = sum(
            p.memory_info().rss for p in self._proc.children(recursive=True)
        ) if self._proc.children(recursive=True) else 0
        while not self._stop_event.is_set():
            sample: dict = {"t": time.time()}
            try:
                out = subprocess.run(
                    [
                        "nvidia-smi",
                        f"--id={self.gpu_index}",
                        "--query-gpu=utilization.gpu,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True, text=True, timeout=5,
                )
                util, mem = out.stdout.strip().split(",")
                sample["gpu_util_pct"] = float(util)
                sample["gpu_mem_mib"] = float(mem)
            except Exception:
                pass
            try:
                procs = [self._proc, *self._proc.children(recursive=True)]
                sample["cpu_pct"] = sum(p.cpu_percent() for p in procs)
                sample["ram_mib"] = (
                    sum(p.memory_info().rss for p in procs) - children_base
                ) / 1024 / 1024
            except Exception:
                pass
            self.samples.append(sample)
            self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)

    def summary(self) -> dict:
        def _stats(key: str) -> dict:
            vals = [s[key] for s in self.samples if key in s]
            if not vals:
                return {}
            return {
                "peak": round(max(vals), 1),
                "mean": round(statistics.fmean(vals), 1),
                "p95": round(sorted(vals)[int(len(vals) * 0.95) - 1], 1) if len(vals) >= 2 else round(vals[0], 1),
            }

        return {
            "gpu_util_pct": _stats("gpu_util_pct"),
            "gpu_mem_mib": _stats("gpu_mem_mib"),
            "cpu_pct": _stats("cpu_pct"),
            "ram_mib": _stats("ram_mib"),
            "sample_count": len(self.samples),
        }


def torch_gc() -> None:
    import gc

    gc.collect()  # 先断 Python 引用环（管线/编码器可能成环），再清 CUDA 缓存
    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()


def _gpu_mem_gb() -> float:
    return torch.cuda.memory_allocated() / 1024**3


def _preencode(ckpt: str, path: Path) -> None:
    """独立进程预编码基准 prompt → CPU 张量落盘。

    M22.1 冒烟实锤：UMT5-XXL 在主进程内"编码后卸载"无法真正释放（26.4GiB 残留，
    gc/empty_cache 均无效）；独立进程编码完毕退出，由 OS 彻底回收全部显存。
    """
    device = torch.device("cuda:0")
    tokenizer = AutoTokenizer.from_pretrained(ckpt, subfolder="tokenizer", torch_dtype=torch.bfloat16)
    text_encoder = UMT5EncoderModel.from_pretrained(
        ckpt, subfolder="text_encoder", torch_dtype=torch.bfloat16
    ).to(device)
    pipe = LongCatVideoPipeline(
        tokenizer=tokenizer, text_encoder=text_encoder, vae=None, scheduler=None, dit=None
    )
    pipe.device = device
    specs = [
        (BENCH_PROMPT, BENCH_NEGATIVE, True),  # t2v/vc 主 prompt（CFG）
        ("", None, False),                     # refine 空 prompt（无 CFG）
    ]
    cache: dict[tuple, tuple] = {}
    for prompt, neg, cfg in specs:
        key = (str(prompt), str(neg), bool(cfg), 1, 512, str(torch.bfloat16))
        out = LongCatVideoPipeline.encode_prompt(
            pipe, prompt=prompt, negative_prompt=neg, do_classifier_free_guidance=cfg,
            num_videos_per_prompt=1, max_sequence_length=512, device=device,
            dtype=torch.bfloat16,
        )
        cache[key] = tuple(t.detach().cpu() if torch.is_tensor(t) else t for t in out)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(cache, path)
    print(f"[preencode] 已保存 {len(cache)} 条缓存 -> {path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint_dir", required=True)
    parser.add_argument("--out_dir", required=True)
    parser.add_argument("--num_segments", type=int, default=10)
    parser.add_argument("--skip_refine", action="store_true")
    parser.add_argument("--preencode_only", action="store_true",
                        help="仅预编码 prompt 落盘后退出（编码器显存由进程退出彻底回收）")
    parser.add_argument("--embeds_path", default=None,
                        help="prompt embeds 缓存路径，默认 {out_dir}/prompt_embeds.pt")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    embeds_path = Path(args.embeds_path) if args.embeds_path else out_dir / "prompt_embeds.pt"

    if args.preencode_only:
        _preencode(args.checkpoint_dir, embeds_path)
        return

    # ---- 分布式环境（单卡 context_parallel_size=1）----
    rank = int(os.environ.get("RANK", "0"))
    num_gpus = torch.cuda.device_count()
    local_rank = rank % num_gpus
    torch.cuda.set_device(local_rank)
    if not dist.is_initialized():
        dist.init_process_group(
            backend="nccl", timeout=datetime.timedelta(seconds=3600 * 24)
        )
    global_rank = dist.get_rank()
    world_size = dist.get_world_size()
    init_context_parallel(
        context_parallel_size=1, global_rank=global_rank, world_size=world_size
    )
    cp_split_hw = context_parallel_util.get_optimal_split(1)

    physical_gpu = int(os.environ.get("CUDA_VISIBLE_DEVICES", "0").split(",")[0])
    sampler = ResourceSampler(gpu_index=physical_gpu)
    sampler.start()
    t_all_start = time.time()
    metrics: dict = {
        "config": {
            "model": "meituan-longcat/LongCat-Video",
            "route": "B (native long video, T2V + continuation)",
            "num_segments": args.num_segments,
            "num_frames_per_segment": NUM_FRAMES,
            "num_cond_frames": NUM_COND_FRAMES,
            "fps": FPS_OUT,
            "stage1_resolution": "480x832",
            "refine_resolution": "720p (spatial refine)" if not args.skip_refine else "skipped",
            "num_inference_steps": 50,
            "guidance_scale": 4.0,
            "seed": args.seed,
            "attention": "flash-attn2 (dit config default) + BSA (refine stage)",
            "skip_refine": args.skip_refine,
        },
        "stages": {},
    }

    # ---- 模型加载（显存受限适配：prompt embeds 预编码落盘，主进程不加载文本编码器）----
    # workstation 生产卡共存，GPU2 仅 ~36GB 空闲；UMT5-XXL 改由 --preencode_only 独立
    # 进程编码落盘（进程退出彻底回收显存），主进程仅 DiT(25.3GB bf16)+VAE(0.5GB) 常驻。
    t0 = time.time()
    ckpt = args.checkpoint_dir
    device = torch.device(f"cuda:{local_rank}")
    tokenizer = AutoTokenizer.from_pretrained(ckpt, subfolder="tokenizer", torch_dtype=torch.bfloat16)
    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(ckpt, subfolder="scheduler", torch_dtype=torch.bfloat16)

    _embed_cache: dict[tuple, tuple] = {}
    text_encoder = None
    # 配置桩：供 _cache_clean_latents 等读取 config.d_model（全量基准实锤崩溃点），
    # 无论缓存命中与否都挂到 pipeline，避免 text_encoder=None 触发 AttributeError
    te_stub = _TextEncoderStub(AutoConfig.from_pretrained(ckpt, subfolder="text_encoder"))
    if embeds_path.exists():
        for k, v in torch.load(embeds_path, map_location="cpu").items():
            _embed_cache[k] = tuple(t.to(device) if torch.is_tensor(t) else t for t in v)
        print(f"[mem] 预编码缓存已加载({len(_embed_cache)} 条)，GPU 占用: "
              f"{_gpu_mem_gb():.2f} GiB", flush=True)
    else:
        # 无缓存时回退主进程内编码（自行承担编码器显存开销，编码后卸载）
        text_encoder = UMT5EncoderModel.from_pretrained(
            ckpt, subfolder="text_encoder", torch_dtype=torch.bfloat16
        ).to(device)

    def _encode_and_cache(
        self, prompt=None, negative_prompt=None, do_classifier_free_guidance=True,
        num_videos_per_prompt=1, max_sequence_length=512, device=None, dtype=None,
    ):
        key = (
            str(prompt), str(negative_prompt), bool(do_classifier_free_guidance),
            int(num_videos_per_prompt), int(max_sequence_length), str(dtype),
        )
        if key not in _embed_cache:
            if self.text_encoder is None or isinstance(self.text_encoder, _TextEncoderStub):
                raise RuntimeError(f"文本编码器已卸载（配置桩）但缓存未命中: {key}")
            _embed_cache[key] = LongCatVideoPipeline.encode_prompt(
                self, prompt=prompt, negative_prompt=negative_prompt,
                do_classifier_free_guidance=do_classifier_free_guidance,
                num_videos_per_prompt=num_videos_per_prompt,
                max_sequence_length=max_sequence_length, device=device, dtype=dtype,
            )
        return _embed_cache[key]

    vae = AutoencoderKLWan.from_pretrained(ckpt, subfolder="vae", torch_dtype=torch.bfloat16)
    dit = LongCatVideoTransformer3DModel.from_pretrained(
        ckpt, subfolder="dit", cp_split_hw=cp_split_hw, torch_dtype=torch.bfloat16
    )
    # 防回归断言：权重为 fp32 分片（50.6GiB），无 accelerate 时 low_cpu_mem_usage=False
    # 路径不会应用 torch_dtype 转换（M22.1 冒烟 OOM 根因），此处显式校验 bf16 生效
    _dit_dtype = next(dit.parameters()).dtype
    assert _dit_dtype == torch.bfloat16, (
        f"DiT 未按 bf16 加载（实际 {_dit_dtype}），25.3GiB 预算将膨胀为 50.6GiB 导致 OOM；"
        "请确认 accelerate 已安装"
    )
    pipe = LongCatVideoPipeline(
        tokenizer=tokenizer, text_encoder=text_encoder if text_encoder is not None else te_stub,
        vae=vae, scheduler=scheduler, dit=None,
    )
    pipe.device = device
    import types
    pipe.encode_prompt = types.MethodType(_encode_and_cache, pipe)

    if text_encoder is not None:
        # 回退路径：主进程内预编码两组 prompt（t2v/vc 主 prompt + refine 空 prompt）
        pipe.encode_prompt(
            prompt=BENCH_PROMPT, negative_prompt=BENCH_NEGATIVE,
            do_classifier_free_guidance=True, max_sequence_length=512,
            dtype=torch.bfloat16, device=device,
        )
        pipe.encode_prompt(
            prompt="", do_classifier_free_guidance=False,
            max_sequence_length=512, dtype=torch.bfloat16, device=device,
        )
        pipe.text_encoder = te_stub  # 真实编码器卸载后挂配置桩，供 _cache_clean_latents 读 d_model
        del text_encoder
        torch_gc()
        print(f"[mem] 编码器卸载后 GPU 占用: {_gpu_mem_gb():.2f} GiB", flush=True)

    pipe.dit = dit.to(device, non_blocking=True)
    pipe.vae = vae.to(device, non_blocking=True)
    print(f"[mem] DiT+VAE 上卡后 GPU 占用: {_gpu_mem_gb():.2f} GiB", flush=True)
    metrics["model_load_seconds"] = round(time.time() - t0, 1)

    generator = torch.Generator(device=local_rank)
    generator.manual_seed(args.seed + global_rank)

    # ---- Stage 1: T2V 首段（480p）----
    seg_times: list[float] = []
    t0 = time.time()
    output = pipe.generate_t2v(
        prompt=BENCH_PROMPT,
        negative_prompt=BENCH_NEGATIVE,
        height=480,
        width=832,
        num_frames=NUM_FRAMES,
        num_inference_steps=50,
        guidance_scale=4.0,
        generator=generator,
    )[0]
    seg_times.append(time.time() - t0)

    if local_rank == 0:
        output_tensor = torch.from_numpy(np.array(output))
        output_tensor = (output_tensor * 255).clamp(0, 255).to(torch.uint8)
        write_video(
            str(out_dir / "stage1_segment_0.mp4"), output_tensor,
            fps=FPS_OUT, video_codec="libx264", options={"crf": "18"},
        )
    video = [(output[i] * 255).astype(np.uint8) for i in range(output.shape[0])]
    video = [PIL.Image.fromarray(img) for img in video]
    del output
    torch_gc()
    target_size = video[0].size

    # ---- Stage 1: 续写段 ----
    all_generated_frames = video
    for segment_idx in range(args.num_segments):
        t0 = time.time()
        output = pipe.generate_vc(
            video=video,  # 与官方 demo 一致：整段 93 帧传入，内部取 num_cond_frames
            prompt=BENCH_PROMPT,
            negative_prompt=BENCH_NEGATIVE,
            resolution="480p",
            num_frames=NUM_FRAMES,
            num_cond_frames=NUM_COND_FRAMES,
            num_inference_steps=50,
            guidance_scale=4.0,
            generator=generator,
            use_kv_cache=True,
            offload_kv_cache=True,  # 显存受限适配：条件帧 KV 卸载 CPU，换 OOM 安全边际
            enhance_hf=True,
        )[0]
        seg_times.append(time.time() - t0)

        new_video = [(output[i] * 255).astype(np.uint8) for i in range(output.shape[0])]
        new_video = [PIL.Image.fromarray(img) for img in new_video]
        new_video = [frame.resize(target_size, PIL.Image.BICUBIC) for frame in new_video]
        del output
        all_generated_frames.extend(new_video[NUM_COND_FRAMES:])
        video = new_video
        if local_rank == 0:
            print(f"[bench] stage1 segment {segment_idx + 1}/{args.num_segments} "
                  f"done in {seg_times[-1]:.1f}s", flush=True)

    stage1_frames = len(all_generated_frames)
    if local_rank == 0:
        output_tensor = torch.from_numpy(np.array(all_generated_frames))
        write_video(
            str(out_dir / "stage1_full.mp4"), output_tensor,
            fps=FPS_OUT, video_codec="libx264", options={"crf": "18"},
        )
        del output_tensor
    metrics["stages"]["stage1_480p"] = {
        "segments": len(seg_times),
        "segment_seconds": [round(t, 1) for t in seg_times],
        "total_seconds": round(sum(seg_times), 1),
        "frames": stage1_frames,
        "duration_seconds": round(stage1_frames / FPS_OUT, 1),
        "gen_fps": round(stage1_frames / sum(seg_times), 3),
    }
    if local_rank == 0:
        # 增量落盘：refine 阶段若异常退出，stage1 指标与产物不丢
        (out_dir / "longcat_metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2))
    torch_gc()

    # ---- Stage 2: 720p refine（BSA + refinement LoRA）----
    if not args.skip_refine:
        refinement_lora_path = os.path.join(ckpt, "lora/refinement_lora.safetensors")
        pipe.dit.load_lora(refinement_lora_path, "refinement_lora")
        pipe.dit.enable_loras(["refinement_lora"])
        pipe.dit.enable_bsa()

        refine_times: list[float] = []
        cur_condition_video = None
        cur_num_cond_frames = 0
        start_id = 0
        all_refine_frames: list = []
        for segment_idx in range(args.num_segments + 1):
            t0 = time.time()
            output_refine = pipe.generate_refine(
                video=cur_condition_video,
                prompt="",
                stage1_video=all_generated_frames[start_id:start_id + NUM_FRAMES],
                num_cond_frames=cur_num_cond_frames,
                num_inference_steps=50,
                generator=generator,
                spatial_refine_only=False,
            )[0]
            refine_times.append(time.time() - t0)
            new_video = [(output_refine[i] * 255).astype(np.uint8) for i in range(output_refine.shape[0])]
            new_video = [PIL.Image.fromarray(img) for img in new_video]
            del output_refine
            all_refine_frames.extend(new_video[cur_num_cond_frames:])
            cur_condition_video = new_video
            cur_num_cond_frames = NUM_COND_FRAMES * 2
            start_id = start_id + NUM_FRAMES - NUM_COND_FRAMES
            if local_rank == 0:
                print(f"[bench] refine segment {segment_idx + 1}/{args.num_segments + 1} "
                      f"done in {refine_times[-1]:.1f}s", flush=True)

        refine_frames = len(all_refine_frames)
        if local_rank == 0:
            output_tensor = torch.from_numpy(np.array(all_refine_frames))
            write_video(
                str(out_dir / "refine_full.mp4"), output_tensor,
                fps=30, video_codec="libx264", options={"crf": "10"},
            )
            del output_tensor
        metrics["stages"]["refine_720p"] = {
            "segments": len(refine_times),
            "segment_seconds": [round(t, 1) for t in refine_times],
            "total_seconds": round(sum(refine_times), 1),
            "frames": refine_frames,
            "duration_seconds": round(refine_frames / 30, 1),
            "gen_fps": round(refine_frames / sum(refine_times), 3),
        }

    metrics["wall_total_seconds"] = round(time.time() - t_all_start, 1)
    sampler.stop()
    metrics["resources"] = sampler.summary()
    metrics["resource_samples"] = sampler.samples

    if local_rank == 0:
        mpath = out_dir / "longcat_metrics.json"
        mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
        print(f"[bench] metrics -> {mpath}", flush=True)

    dist.barrier()
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
