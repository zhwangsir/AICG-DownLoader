"""M21 长视频角色漂移量化评估服务（拉长验证测试核心观测层）。

三项量化指标（对应拉长验证的漂移累积观测方案）：

1. 角色特征保持度 character_fidelity（0-100，越高越好）
   VLM 将角色定妆参考图与每块抽帧逐帧对照，按 发色发型/服装/五官脸型
   三维度打分并聚合；同帧复用 M13/M16 漂移判定（drift_detected）作布尔佐证。
2. 情感一致性 emotion_consistency（0-100，越高越好）
   VLM 判定每块抽帧呈现的情绪基调与该块创作意图（chunk prompt）的匹配度。
3. 行为连贯性 behavior_coherence（两个子项）
   - seam_pixel_diff（0-255，越低越好，确定性）：块 i 末帧与块 i+1 生成首帧
     的平均像素差（PIL 直方图法，与 M20 接缝观测同口径）；
   - action_continuity（0-100，VLM）：接缝前后帧动作/场景连贯性打分。

漂移累积观测：逐块指标序列 + 最小二乘线性斜率 + 端点增量，阈值法标注
异常点，规则化生成改进建议。所有 VLM 调用 fail-open（异常返回 None），
不阻断指标采集主流程；确定性像素差失败时同样记 None。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import json_repair
import httpx
from openai import AsyncOpenAI

from app.agents.base import strip_think_tags
from app.config import settings
from app.services.long_video_service import _run_capture, _run_ffmpeg  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 阈值与观测常量
# ---------------------------------------------------------------------------
FIDELITY_ANOMALY_THRESHOLD = 60.0  # 角色特征保持度低于此值标注异常
EMOTION_ANOMALY_THRESHOLD = 60.0  # 情感一致性低于此值标注异常
SEAM_DIFF_ANOMALY_THRESHOLD = 30.0  # 接缝像素差高于此值标注异常（M20 实测 9.0）
ACTION_ANOMALY_THRESHOLD = 60.0  # 动作连贯性低于此值标注异常
DRIFT_SLOPE_ALERT = -5.0  # 每块跌幅超过此值判定漂移累积

_PIXEL_DIFF_SIZE = (192, 336)  # 与 M20 接缝观测同尺寸

# ---------------------------------------------------------------------------
# VLM 评分 prompt（独立简单 prompt，复用 M13.6 实测教训：不拼长画质 prompt）
# ---------------------------------------------------------------------------
FIDELITY_SCORE_PROMPT = """前 {ref_count} 张图是角色定妆参考图（三视图），最后 1 张是长视频第 {chunk_no} 块的抽帧。
请为「角色特征保持度」打分：帧中角色与参考图角色的外貌一致程度。
评分维度（各占权重）：发色发型、服装款式与颜色、五官脸型。
若参考角色未在帧中出镜（POV/空镜/仅背景路人），character_present 填 false 且 score 填 null。

只输出一个 JSON 对象：
- character_present：布尔值
- score：0-100 整数（100=完全一致；未出镜填 null）
- hair_match / outfit_match / face_match：各 0-100 整数（无法判断填 null）
- reason：一句话说明扣分点（无扣分填空字符串）
严禁照抄本说明文字。不要 markdown 代码块，不要解释。"""

EMOTION_SCORE_PROMPT = """这段视频抽帧来自长视频第 {chunk_no} 块，该块的创作意图是：
「{intent}」
请判断画面呈现的情绪基调与创作意图的匹配程度。

只输出一个 JSON 对象：
- observed_emotion：字符串，画面实际呈现的情绪（如 紧张/悲伤/平静/喜悦）
- score：0-100 整数（100=情绪与意图完全匹配）
- reason：一句话说明（无扣分填空字符串）
不要 markdown 代码块，不要解释。"""

ACTION_CONTINUITY_PROMPT = """两张图分别是长视频相邻两块接缝处的前块末帧（第 1 张）与后块首帧（第 2 张）。
请判断接缝处动作与场景的连贯程度：人物姿态/位置/朝向是否连续、场景光影是否突变、有无跳剪感。

只输出一个 JSON 对象：
- score：0-100 整数（100=完全连贯无跳变）
- motion_jump：布尔值，是否存在明显动作/场景跳变
- reason：一句话说明（连贯填空字符串）
不要 markdown 代码块，不要解释。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ChunkMetrics:
    """单块量化指标。VLM 失败字段为 None（fail-open）。"""

    chunk_index: int
    character_fidelity: float | None = None
    emotion_consistency: float | None = None
    drift_detected: bool = False
    character_present: bool = True
    fidelity_detail: str = ""
    emotion_detail: str = ""


@dataclass
class SeamMetrics:
    """相邻块接缝连贯性指标（seam_index=i 表示块 i 与块 i+1 之间）。"""

    seam_index: int
    pixel_diff: float | None = None  # 0-255，越低越好
    action_continuity: float | None = None  # 0-100，越高越好
    motion_jump: bool = False
    detail: str = ""


@dataclass
class DriftMetricsReport:
    """漂移累积观测报告：逐块序列 + 接缝序列 + 趋势分析 + 异常与建议。"""

    chunks: list[ChunkMetrics] = field(default_factory=list)
    seams: list[SeamMetrics] = field(default_factory=list)
    fidelity_slope: float | None = None  # 每块变化量（最小二乘）
    emotion_slope: float | None = None
    fidelity_endpoint_delta: float | None = None  # 末块 - 首块
    emotion_endpoint_delta: float | None = None
    anomalies: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunks": [vars(c) for c in self.chunks],
            "seams": [vars(s) for s in self.seams],
            "fidelity_slope": self.fidelity_slope,
            "emotion_slope": self.emotion_slope,
            "fidelity_endpoint_delta": self.fidelity_endpoint_delta,
            "emotion_endpoint_delta": self.emotion_endpoint_delta,
            "anomalies": self.anomalies,
            "suggestions": self.suggestions,
        }


# ---------------------------------------------------------------------------
# 确定性工具：抽帧 / 像素差 / 线性趋势
# ---------------------------------------------------------------------------
async def extract_frame_at(video_path: Path, t_seconds: float, out_path: Path) -> Path:
    """ffmpeg 抽取指定时间点帧 → PNG。"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{t_seconds:.2f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]
    await _run_ffmpeg(cmd)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"抽帧失败: {video_path} @ {t_seconds:.2f}s")
    return out_path


def compute_pixel_diff(frame_a: Path, frame_b: Path) -> float:
    """两帧平均像素差（0-255，灰度直方图加权，与 M20 接缝观测同口径）。"""
    from PIL import Image, ImageChops

    a = Image.open(frame_a).convert("L").resize(_PIXEL_DIFF_SIZE)
    b = Image.open(frame_b).convert("L").resize(_PIXEL_DIFF_SIZE)
    diff = ImageChops.difference(a, b)
    hist = diff.histogram()
    pixels = _PIXEL_DIFF_SIZE[0] * _PIXEL_DIFF_SIZE[1]
    return sum(i * v for i, v in enumerate(hist)) / pixels


def linear_slope(values: list[float]) -> float | None:
    """最小二乘拟合斜率（x=0..n-1）；<2 个点返回 None。"""
    n = len(values)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None  # pragma: no cover — n≥2 时连续整数离差平方和恒 > 0，数学不可达
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denom


# ---------------------------------------------------------------------------
# 漂移量化评估服务
# ---------------------------------------------------------------------------
# VLM 调用签名：messages(多模态 content) -> 解析后的 JSON dict（失败返回 None）
VlmJsonCaller = Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any] | None]]


class DriftMetricsService:
    """长视频漂移量化评估：对 LongVideoService 产出的块序列计算三项指标。

    vlm_caller 可注入（单元测试 mock）；默认实现走 settings.visual_model_url
    （spark02 qwen3.6-uncensored，实测支持视觉输入）。
    """

    def __init__(self, vlm_caller: VlmJsonCaller | None = None):
        self._vlm_caller = vlm_caller or self._default_vlm_caller

    # ------------------------------------------------------------------
    # 默认 VLM 调用（OpenAI 兼容多模态，enable_thinking=False）
    # ------------------------------------------------------------------
    @staticmethod
    async def _default_vlm_caller(
        content: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not settings.visual_model_url:
            logger.warning("visual_model_url 未配置，VLM 指标记 None")
            return None
        http = httpx.AsyncClient(timeout=300.0, trust_env=False)
        try:
            client = AsyncOpenAI(
                base_url=settings.visual_model_url,
                api_key="not-needed",
                http_client=http,
            )
            resp = await client.chat.completions.create(
                model=settings.visual_model_name,
                messages=[{"role": "user", "content": content}],
                temperature=0.1,
                max_tokens=500,
                stream=False,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            raw = resp.choices[0].message.content or ""
            if not raw:
                raw = getattr(resp.choices[0].message, "reasoning_content", "") or ""
            raw = strip_think_tags(raw).strip()
            if raw.startswith("```"):
                lines = raw.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = json_repair.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception as e:  # noqa: BLE001 —— VLM 失败 fail-open
            logger.warning("漂移指标 VLM 调用失败，记 None: %s", e)
            return None
        finally:
            await http.aclose()

    @staticmethod
    def _image_part(path: Path) -> dict[str, Any]:
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"},
        }

    @staticmethod
    def _score(data: dict[str, Any] | None, key: str) -> float | None:
        """从 VLM JSON 安全取 0-100 分数（含 null/越界/非数值容错）。"""
        if not data:
            return None
        v = data.get(key)
        if v is None or isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if math.isnan(f) or not 0 <= f <= 100:
            return None
        return f

    # ------------------------------------------------------------------
    # 指标 1：角色特征保持度（参考图 + 单帧 → VLM 打分）
    # ------------------------------------------------------------------
    async def score_character_fidelity(
        self,
        ref_paths: list[Path],
        frame_path: Path,
        chunk_no: int,
    ) -> tuple[float | None, bool, bool, str]:
        """返回 (fidelity, drift_detected, character_present, detail)。"""
        if not ref_paths:
            return None, False, True, ""
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": FIDELITY_SCORE_PROMPT.format(
                ref_count=len(ref_paths), chunk_no=chunk_no,
            ),
        }]
        content.extend(self._image_part(p) for p in ref_paths)
        content.append(self._image_part(frame_path))
        data = await self._vlm_caller(content)
        if data is None:
            return None, False, True, ""
        present = data.get("character_present") is not False
        score = self._score(data, "score") if present else None
        detail = str(data.get("reason", "") or "")
        # 保持度 <60 视为漂移佐证（与 M13 布尔判定同向的量化口径）
        drift = present and score is not None and score < FIDELITY_ANOMALY_THRESHOLD
        return score, drift, present, detail

    # ------------------------------------------------------------------
    # 指标 2：情感一致性（单帧 + 块创作意图 → VLM 打分）
    # ------------------------------------------------------------------
    async def score_emotion_consistency(
        self,
        frame_path: Path,
        intent: str,
        chunk_no: int,
    ) -> tuple[float | None, str]:
        """返回 (emotion_consistency, observed_emotion)。"""
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": EMOTION_SCORE_PROMPT.format(intent=intent or "未指定", chunk_no=chunk_no),
        }, self._image_part(frame_path)]
        data = await self._vlm_caller(content)
        if data is None:
            return None, ""
        return self._score(data, "score"), str(data.get("observed_emotion", "") or "")

    # ------------------------------------------------------------------
    # 指标 3：行为连贯性（接缝像素差 + VLM 动作连贯性）
    # ------------------------------------------------------------------
    async def score_action_continuity(
        self,
        frame_pre: Path,
        frame_post: Path,
    ) -> tuple[float | None, bool, str]:
        """返回 (action_continuity, motion_jump, detail)。"""
        content: list[dict[str, Any]] = [
            {"type": "text", "text": ACTION_CONTINUITY_PROMPT},
            self._image_part(frame_pre),
            self._image_part(frame_post),
        ]
        data = await self._vlm_caller(content)
        if data is None:
            return None, False, ""
        return (
            self._score(data, "score"),
            bool(data.get("motion_jump", False)),
            str(data.get("reason", "") or ""),
        )

    # ------------------------------------------------------------------
    # 主编排：逐块 + 逐缝采集，趋势分析与异常标注
    # ------------------------------------------------------------------
    async def evaluate(
        self,
        chunk_paths: list[Path],
        reference_image_paths: list[Path] | None = None,
        chunk_intents: list[str] | None = None,
        frames_per_chunk: int = 3,
        work_dir: Path | None = None,
    ) -> DriftMetricsReport:
        """对块序列计算三项指标并生成累积观测报告。

        chunk_intents：每块创作意图（情感判定基准，通常为 chunk prompt）。
        frames_per_chunk：每块抽帧数（首/中/尾均匀取点），逐帧 fidelity/emotion
        打分后取均值作为块级指标。
        """
        if not chunk_paths:
            raise ValueError("chunk_paths 为空")
        ref_paths = list(reference_image_paths or [])
        intents = list(chunk_intents or [""] * len(chunk_paths))
        work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="drift_metrics_"))
        work.mkdir(parents=True, exist_ok=True)

        report = DriftMetricsReport()
        chunk_frame_sets: list[list[Path]] = []

        # ---- 逐块抽帧 + 指标 1/2 ----
        for i, chunk_path in enumerate(chunk_paths):
            duration = await self._safe_duration(chunk_path)
            times = self._sample_times(duration, frames_per_chunk)
            frames: list[Path] = []
            for j, t in enumerate(times):
                fp = work / f"chunk{i:02d}_frame{j}.png"
                try:
                    await extract_frame_at(chunk_path, t, fp)
                    frames.append(fp)
                except Exception as e:  # noqa: BLE001 —— 单帧失败跳过
                    logger.warning("块 %d 抽帧失败 @%.2fs: %s", i, t, e)
            chunk_frame_sets.append(frames)

            fidelity_vals: list[float] = []
            emotion_vals: list[float] = []
            drift_any = False
            present_any = False
            fidelity_notes: list[str] = []
            emotion_notes: list[str] = []

            async def _score_frame(frame: Path) -> None:
                nonlocal drift_any, present_any
                fid, drift, present, fdetail = await self.score_character_fidelity(
                    ref_paths, frame, i + 1,
                )
                if fid is not None:
                    fidelity_vals.append(fid)
                drift_any = drift_any or drift
                present_any = present_any or present
                if fdetail:
                    fidelity_notes.append(fdetail)
                emo, observed = await self.score_emotion_consistency(
                    frame, intents[i] if i < len(intents) else "", i + 1,
                )
                if emo is not None:
                    emotion_vals.append(emo)
                if observed:
                    emotion_notes.append(observed)

            await asyncio.gather(*(_score_frame(f) for f in frames))

            report.chunks.append(ChunkMetrics(
                chunk_index=i,
                character_fidelity=(
                    sum(fidelity_vals) / len(fidelity_vals) if fidelity_vals else None
                ),
                emotion_consistency=(
                    sum(emotion_vals) / len(emotion_vals) if emotion_vals else None
                ),
                drift_detected=drift_any,
                character_present=present_any or not ref_paths,
                fidelity_detail="；".join(dict.fromkeys(fidelity_notes))[:200],
                emotion_detail="；".join(dict.fromkeys(emotion_notes))[:200],
            ))

        # ---- 逐缝指标 3 ----
        for i in range(len(chunk_paths) - 1):
            pre = work / f"seam{i:02d}_pre.png"
            post = work / f"seam{i:02d}_post.png"
            pixel_diff: float | None = None
            action: float | None = None
            jump = False
            detail = ""
            try:
                # 前块末帧（-sseof 尾部定位）+ 后块首帧
                from app.services.long_video_service import extract_last_frame

                await extract_last_frame(chunk_paths[i], pre)
                await extract_frame_at(chunk_paths[i + 1], 0.0, post)
                try:
                    pixel_diff = compute_pixel_diff(pre, post)
                except Exception as e:  # noqa: BLE001
                    logger.warning("缝 %d 像素差计算失败: %s", i, e)
                action, jump, detail = await self.score_action_continuity(pre, post)
            except Exception as e:  # noqa: BLE001 —— 单缝失败不阻断
                logger.warning("缝 %d 帧抽取失败: %s", i, e)
            report.seams.append(SeamMetrics(
                seam_index=i,
                pixel_diff=pixel_diff,
                action_continuity=action,
                motion_jump=jump,
                detail=detail[:200],
            ))

        self._analyze_trends(report)
        return report

    # ------------------------------------------------------------------
    # 趋势分析 + 异常标注 + 规则化建议
    # ------------------------------------------------------------------
    def _analyze_trends(self, report: DriftMetricsReport) -> None:
        fidelity = [c.character_fidelity for c in report.chunks if c.character_fidelity is not None]
        emotion = [c.emotion_consistency for c in report.chunks if c.emotion_consistency is not None]

        report.fidelity_slope = linear_slope(fidelity)
        report.emotion_slope = linear_slope(emotion)
        if len(fidelity) >= 2:
            report.fidelity_endpoint_delta = fidelity[-1] - fidelity[0]
        if len(emotion) >= 2:
            report.emotion_endpoint_delta = emotion[-1] - emotion[0]

        anomalies: list[str] = []
        suggestions: list[str] = []

        for c in report.chunks:
            if c.character_fidelity is not None and c.character_fidelity < FIDELITY_ANOMALY_THRESHOLD:
                anomalies.append(
                    f"块 {c.chunk_index + 1}: 角色特征保持度 {c.character_fidelity:.0f} "
                    f"< {FIDELITY_ANOMALY_THRESHOLD:.0f}（{c.fidelity_detail or '外貌与参考图偏差'}）"
                )
            if c.emotion_consistency is not None and c.emotion_consistency < EMOTION_ANOMALY_THRESHOLD:
                anomalies.append(
                    f"块 {c.chunk_index + 1}: 情感一致性 {c.emotion_consistency:.0f} "
                    f"< {EMOTION_ANOMALY_THRESHOLD:.0f}（实测情绪: {c.emotion_detail or '未知'}）"
                )
            if c.drift_detected:
                anomalies.append(f"块 {c.chunk_index + 1}: 漂移判定为真（量化保持度低于阈值）")
        for s in report.seams:
            if s.pixel_diff is not None and s.pixel_diff > SEAM_DIFF_ANOMALY_THRESHOLD:
                anomalies.append(
                    f"缝 {s.seam_index + 1}: 像素差 {s.pixel_diff:.1f}/255 "
                    f"> {SEAM_DIFF_ANOMALY_THRESHOLD:.0f}（接缝跳变）"
                )
            if s.action_continuity is not None and s.action_continuity < ACTION_ANOMALY_THRESHOLD:
                anomalies.append(
                    f"缝 {s.seam_index + 1}: 动作连贯性 {s.action_continuity:.0f} "
                    f"< {ACTION_ANOMALY_THRESHOLD:.0f}（{s.detail or '动作断裂'}）"
                )
            if s.motion_jump:
                anomalies.append(f"缝 {s.seam_index + 1}: VLM 判定存在明显跳剪")

        if report.fidelity_slope is not None and report.fidelity_slope < DRIFT_SLOPE_ALERT:
            suggestions.append(
                f"角色特征保持度逐块下滑（斜率 {report.fidelity_slope:.1f}/块）："
                "存在漂移累积，建议增强 ref2va 参考图注入权重、缩短块长或每 N 块回锚定妆照关键帧"
            )
        if fidelity and fidelity[0] < FIDELITY_ANOMALY_THRESHOLD:
            suggestions.append(
                "首块保持度即低于阈值：漂移源于首块而非累积，建议优先检查参考图质量与画风锚定链"
            )
        seam_bad = [s for s in report.seams if s.pixel_diff is not None and s.pixel_diff > SEAM_DIFF_ANOMALY_THRESHOLD]
        if seam_bad:
            suggestions.append(
                f"{len(seam_bad)} 处接缝像素差超阈值：建议引入重叠帧交叉淡化或接缝处重生成"
            )
        if report.emotion_slope is not None and report.emotion_slope < DRIFT_SLOPE_ALERT:
            suggestions.append(
                "情感一致性逐块下滑：建议在块 prompt 中显式重申情绪基调（emotion anchor）"
            )
        if not suggestions:
            suggestions.append("三项指标均在健康区间：当前帧链续写策略可支撑更长块数，建议下次测试加长验证")

        report.anomalies = anomalies
        report.suggestions = suggestions

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    async def _safe_duration(video_path: Path) -> float:
        from app.services.long_video_service import probe_video_duration

        try:
            return await probe_video_duration(video_path)
        except Exception:  # noqa: BLE001
            return 0.0

    @staticmethod
    def _sample_times(duration: float, n: int) -> list[float]:
        """首/中/尾均匀取 n 个抽帧时间点（避开首尾 0.1s 编码边界）。"""
        if duration <= 0.2 or n <= 1:
            return [max(0.0, duration / 2)]
        lo, hi = 0.1, max(0.1, duration - 0.1)
        if n == 2:
            return [lo, hi]
        step = (hi - lo) / (n - 1)
        return [lo + step * i for i in range(n)]


def render_markdown_report(report: DriftMetricsReport, meta: dict[str, Any]) -> str:
    """把量化报告渲染为详细 Markdown（原始数据 + 趋势 + 异常 + 建议）。"""
    lines: list[str] = []
    lines.append("# 长视频拉长验证·角色漂移累积观测报告")
    lines.append("")
    for k, v in meta.items():
        lines.append(f"- **{k}**：{v}")
    lines.append("")
    lines.append("## 一、逐块原始数据")
    lines.append("")
    lines.append("| 块 | 角色特征保持度 | 情感一致性 | 漂移判定 | 细节 |")
    lines.append("|----|--------------|-----------|---------|------|")
    for c in report.chunks:
        fid = f"{c.character_fidelity:.0f}" if c.character_fidelity is not None else "N/A"
        emo = f"{c.emotion_consistency:.0f}" if c.emotion_consistency is not None else "N/A"
        detail = c.fidelity_detail or c.emotion_detail or "-"
        lines.append(f"| {c.chunk_index + 1} | {fid} | {emo} | {'是' if c.drift_detected else '否'} | {detail} |")
    lines.append("")
    lines.append("## 二、接缝原始数据")
    lines.append("")
    lines.append("| 接缝 | 像素差(/255) | 动作连贯性 | 跳剪 | 细节 |")
    lines.append("|------|------------|-----------|------|------|")
    for s in report.seams:
        diff = f"{s.pixel_diff:.1f}" if s.pixel_diff is not None else "N/A"
        act = f"{s.action_continuity:.0f}" if s.action_continuity is not None else "N/A"
        lines.append(
            f"| {s.seam_index + 1} | {diff} | {act} | {'是' if s.motion_jump else '否'} | {s.detail or '-'} |"
        )
    lines.append("")
    lines.append("## 三、趋势分析")
    lines.append("")
    if report.fidelity_slope is not None:
        lines.append(f"- 角色特征保持度斜率：**{report.fidelity_slope:.2f}/块**"
                     + (f"，端点增量 {report.fidelity_endpoint_delta:+.0f}" if report.fidelity_endpoint_delta is not None else ""))
    if report.emotion_slope is not None:
        lines.append(f"- 情感一致性斜率：**{report.emotion_slope:.2f}/块**"
                     + (f"，端点增量 {report.emotion_endpoint_delta:+.0f}" if report.emotion_endpoint_delta is not None else ""))
    lines.append("")
    lines.append("## 四、异常点标注")
    lines.append("")
    if report.anomalies:
        lines.extend(f"- {a}" for a in report.anomalies)
    else:
        lines.append("- 无异常（全部指标在健康阈值内）")
    lines.append("")
    lines.append("## 五、改进建议")
    lines.append("")
    lines.extend(f"- {s}" for s in report.suggestions)
    lines.append("")
    return "\n".join(lines)
