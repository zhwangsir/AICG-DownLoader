"""M22.2 路线 A/B 对比实验报告生成器。

输入（控制变量：同输入叙事、同评估指标、同 workstation 环境）：
- 路线 A：M21.2 漂移测试产出的 56s 视频 + drift_56s_*.json 报告
  （含逐块时长/生成耗时/漂移三指标）+ workstation GPU 采样日志
- 路线 B：LongCat-Video benchmark 产出的视频 + longcat_metrics.json
  （含逐段耗时/FPS/资源采样汇总）

评估：对两路视频分别运行 MosEvaluationService（VLM 四维度 1-5 分），
汇总 MOS / 生成效率（视频秒/壁钟秒）/ 资源消耗（GPU 显存峰值、利用率），
产出 Markdown 对比报告落盘 reports/M22.2/。

运行方式（MateBook，需 spark02 VLM 在线 + 两路产物已就位）：
    cd platform/backend
    .venv/bin/python scripts/run_route_ab_comparison.py \
        --route-a-video /path/to/routeA.mp4 \
        --route-a-report ../reports/M21.2/drift_56s_XXX.json \
        --route-a-gpulog /path/to/routeA_h3_gpu_samples.log \
        --route-b-video /path/to/refine_full.mp4 \
        --route-b-metrics /path/to/longcat_metrics.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.mos_evaluation_service import MosEvaluationService  # noqa: E402

REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "M22.2"

# 两路共同的输入叙事（同 M21.2 四幕 / benchmark_longcat BENCH_PROMPT）
SHARED_PROMPT = (
    "雨夜霓虹街头四幕叙事：红衣黑长直女子（平静静立→警觉回头→惊惧奔跑→对峙转身），"
    "写实电影感，竖屏。"
)


def _parse_gpu_samples(log_path: Path) -> dict:
    """解析路线 A 的 nvidia-smi 采样日志（`epoch util0, mem0 | util2, mem2`）。"""
    util0, mem0, util2, mem2 = [], [], [], []
    for line in log_path.read_text().splitlines():
        try:
            _ts, rest = line.split(" ", 1)
            g0, g2 = rest.split(" | ")
            u0, m0 = g0.split(",")
            u2, m2 = g2.split(",")
            util0.append(float(u0))
            mem0.append(float(m0))
            util2.append(float(u2))
            mem2.append(float(m2))
        except (ValueError, AttributeError):
            continue

    def _agg(vals: list[float]) -> dict:
        if not vals:
            return {}
        vals_sorted = sorted(vals)
        return {
            "peak": round(max(vals), 1),
            "mean": round(sum(vals) / len(vals), 1),
            "p95": round(vals_sorted[int(len(vals) * 0.95) - 1], 1) if len(vals) >= 2 else round(vals[0], 1),
        }

    return {
        "gpu0": {"util_pct": _agg(util0), "mem_mib": _agg(mem0)},
        "gpu2": {"util_pct": _agg(util2), "mem_mib": _agg(mem2)},
        "sample_count": len(util0),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-a-video", required=True)
    parser.add_argument("--route-a-report", required=True)
    parser.add_argument("--route-a-gpulog", required=True)
    parser.add_argument("--route-b-video", required=True)
    parser.add_argument("--route-b-metrics", required=True)
    parser.add_argument("--mos-frames", type=int, default=6)
    args = parser.parse_args()

    a_video = Path(args.route_a_video)
    b_video = Path(args.route_b_video)
    a_report = json.loads(Path(args.route_a_report).read_text(encoding="utf-8"))
    b_metrics = json.loads(Path(args.route_b_metrics).read_text(encoding="utf-8"))
    a_gpu = _parse_gpu_samples(Path(args.route_a_gpulog))

    # ---- MOS 双路评估（同一 VLM、同一 prompt、同抽帧数）----
    settings.visual_model_url = "http://192.168.71.84:8000/v1"
    settings.visual_model_name = "qwen3.6-uncensored"
    mos_svc = MosEvaluationService()
    mos_a, mos_b = await asyncio.gather(
        mos_svc.evaluate(a_video, prompt=SHARED_PROMPT, num_frames=args.mos_frames),
        mos_svc.evaluate(b_video, prompt=SHARED_PROMPT, num_frames=args.mos_frames),
    )

    # ---- 效率与资源汇总 ----
    a_meta = a_report.get("meta", {})
    a_duration = a_report.get("drift_summary", {}).get("total_duration")
    b_stage1 = b_metrics.get("stages", {}).get("stage1_480p", {})
    b_refine = b_metrics.get("stages", {}).get("refine_720p", {})

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    out = {
        "meta": {
            "测试时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "输入集": "相同四幕叙事 prompt（雨夜街头女子）",
            "评估指标": "MOS（VLM 四维度 1-5）/ 生成效率 / 资源消耗",
            "环境": "workstation（路线A: H3 GPU0+GPU2；路线B: LongCat GPU2）",
        },
        "route_a": {
            "video": str(a_video),
            "duration": a_meta.get("总时长", ""),
            "elapsed": a_meta.get("生成耗时", ""),
            "mos": mos_a.to_dict(),
            "gpu": a_gpu,
            "drift": {
                "fidelity_slope": a_report.get("fidelity_slope"),
                "emotion_slope": a_report.get("emotion_slope"),
                "anomalies": a_report.get("anomalies", []),
            },
        },
        "route_b": {
            "video": str(b_video),
            "config": b_metrics.get("config", {}),
            "stage1": b_stage1,
            "refine": b_refine,
            "wall_total_seconds": b_metrics.get("wall_total_seconds"),
            "resources": b_metrics.get("resources", {}),
            "mos": mos_b.to_dict(),
        },
    }
    json_path = REPORT_DIR / f"route_ab_comparison_{ts}.json"
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- Markdown 报告 ----
    def _mos_line(mos) -> str:
        dm = mos.dimension_means
        cells = " / ".join(f"{d}={dm.get(d)}" for d in (
            "visual_quality", "motion_naturalness", "temporal_consistency", "text_alignment"))
        return f"MOS={mos.mos}（{cells}，评分帧数 {mos.frames_scored}）"

    def _eff(seconds: float | None, duration: float | None) -> str:
        if not seconds or not duration:
            return "N/A"
        return f"{duration / seconds:.3f} 视频秒/壁钟秒（{duration:.1f}s 视频耗时 {seconds:.0f}s）"

    md = [
        "# M22.2 长视频路线 A/B 对比实验报告",
        "",
        f"- **测试时间**：{out['meta']['测试时间']}",
        f"- **输入集**：{out['meta']['输入集']}",
        f"- **评估指标**：{out['meta']['评估指标']}",
        f"- **测试环境**：{out['meta']['环境']}",
        "",
        "## 一、质量对比（MOS，VLM 四维度 1-5 分）",
        "",
        "| 路线 | MOS | 画质 | 运动 | 时序 | 文本对齐 |",
        "|------|-----|------|------|------|---------|",
    ]
    for name, mos in (("A（H3 帧链续写）", mos_a), ("B（LongCat 原生）", mos_b)):
        dm = mos.dimension_means
        md.append(
            f"| {name} | {mos.mos} | {dm.get('visual_quality')} | "
            f"{dm.get('motion_naturalness')} | {dm.get('temporal_consistency')} | "
            f"{dm.get('text_alignment')} |"
        )
    md += [
        "",
        "## 二、效率对比（生成速度）",
        "",
        f"- 路线 A：{a_meta.get('总时长', 'N/A')}，{a_meta.get('生成耗时', 'N/A')}",
        f"- 路线 B stage1(480p)：{b_stage1.get('duration_seconds', 'N/A')}s 视频 / "
        f"{b_stage1.get('total_seconds', 'N/A')}s 生成 → gen_fps={b_stage1.get('gen_fps', 'N/A')}",
    ]
    if b_refine:
        md.append(
            f"- 路线 B refine(720p)：{b_refine.get('duration_seconds', 'N/A')}s 视频 / "
            f"{b_refine.get('total_seconds', 'N/A')}s 生成 → gen_fps={b_refine.get('gen_fps', 'N/A')}"
        )
    md += [
        "",
        "## 三、资源消耗对比",
        "",
        "### 路线 A（H3，GPU0+GPU2 双卡分片）",
        "",
        f"- GPU0 显存峰值 {a_gpu.get('gpu0', {}).get('mem_mib', {}).get('peak', 'N/A')} MiB，"
        f"利用率均值 {a_gpu.get('gpu0', {}).get('util_pct', {}).get('mean', 'N/A')}%",
        f"- GPU2 显存峰值 {a_gpu.get('gpu2', {}).get('mem_mib', {}).get('peak', 'N/A')} MiB，"
        f"利用率均值 {a_gpu.get('gpu2', {}).get('util_pct', {}).get('mean', 'N/A')}%",
        "",
        "### 路线 B（LongCat，GPU2 单卡）",
        "",
    ]
    b_res = b_metrics.get("resources", {})
    md += [
        f"- GPU 显存峰值 {b_res.get('gpu_mem_mib', {}).get('peak', 'N/A')} MiB，"
        f"利用率均值 {b_res.get('gpu_util_pct', {}).get('mean', 'N/A')}%",
        f"- CPU 均值 {b_res.get('cpu_pct', {}).get('mean', 'N/A')}%，"
        f"内存峰值 {b_res.get('ram_mib', {}).get('peak', 'N/A')} MiB",
        "",
        "## 四、路线 A 漂移观测摘录（M21.2）",
        "",
        f"- 角色特征保持度斜率：{a_report.get('fidelity_slope')}",
        f"- 情感一致性斜率：{a_report.get('emotion_slope')}",
        f"- 异常点：{len(a_report.get('anomalies', []))} 项",
        "",
        "## 五、结论与建议",
        "",
        "（见下方人工/自动分析）",
        "",
    ]
    md_path = REPORT_DIR / f"route_ab_comparison_{ts}.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    print(f"[M22.2] JSON: {json_path}")
    print(f"[M22.2] MD:   {md_path}")
    print(f"[M22.2] 路线A {_mos_line(mos_a)}")
    print(f"[M22.2] 路线B {_mos_line(mos_b)}")


if __name__ == "__main__":
    asyncio.run(main())
