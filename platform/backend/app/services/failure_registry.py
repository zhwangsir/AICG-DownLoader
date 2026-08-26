"""失败模式注册表（DramaClaw failure_registry 的本地化对等实现，M25.9 C2）。

把生成失败的经验资产化：每种失败模式登记四元组——
- detection：VLM 门禁判定问句（「这张图是否呈现该失败？」）
- prevention_rule：提示词编译层的预防规则
- correction_template：失真后的修正指令模板
- negative_prompt_clause：自动汇入生成负面提示词的子句

与 DramaClaw 的差异（本地化适配）：
- 无 SQLite 双库：JSON 单库存储（与 character_library 同一持久化风格）
- 种子模式来自我方 M15/M16/M18 实测失败史，非 DramaClaw 火柴人线稿模式
- VLM 门禁用本地 spark02/studio04（DramaClaw 用远程 Gemini Flash）
- 判定哲学沿用 unsure=pass（门禁误杀成本 > 放行成本）

存储：output/verification/failure_modes.json（defs + hits 同文件分区）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.models.schemas import FailureMode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 种子失败模式（我方实测失败史提炼，详见各条 note）
# ---------------------------------------------------------------------------
SEED_FAILURE_MODES: list[dict[str, Any]] = [
    {
        "code": "collage_mismatch",
        "layer": "correction",
        "gate_enabled": True,
        "detection": (
            "Does the image show a character whose appearance (hair color/style, "
            "outfit style/color) clearly mismatches the given character description — "
            "e.g. a collage of different outfits or a character that looks like a "
            "different person?"
        ),
        "prevention_rule": (
            "分镜提示词必须把出场角色的核心外貌（发色/发型/服装款式与颜色）"
            "从角色描述原样翻译前置，禁止氛围词稀释 CLIP 注意力（M16.2 实测）"
        ),
        "correction_template": (
            "以镜头类型开头，紧随其后立即写出每个出场角色的核心外貌，"
            "之后只保留最核心动作与环境（≤2 个），总长 ≤80 词，禁氛围填充词"
        ),
        "negative_prompt_clause": (
            "Do not render characters with hair color, hairstyle, or outfit that "
            "contradicts the given character description."
        ),
        "note": "M16.2 拼贴失真：多角色长 prompt 下外貌被模型先验覆盖",
    },
    {
        "code": "legible_text_leak",
        "layer": "generator",
        "gate_enabled": True,
        "detection": (
            "Is there any readable text, letters, numbers, labels, signage, or "
            "watermarks baked into the image (other than text intrinsically part "
            "of the scene such as a wall clock's numerals)?"
        ),
        "prevention_rule": (
            "场景可能出现招牌/屏幕文字时，正向写 blurred illegible signage, "
            "no readable text；负向必带 legible text, letters, alphabet"
        ),
        "correction_template": "删除画面内所有可读文字/标签/水印/标牌",
        "negative_prompt_clause": (
            "legible text, letters, alphabet, signage with text, watermark, captions"
        ),
        "note": "全链路 negative 已内建；升级为注册表统一管理",
    },
    {
        "code": "black_and_white_drift",
        "layer": "correction",
        "gate_enabled": True,
        "detection": (
            "Has the image drifted to black-and-white, monochrome, grayscale, or "
            "single-color treatment when full color was required?"
        ),
        "prevention_rule": (
            "默认彩色：正向明确写入 full color, vivid color grading，"
            "负向带 black and white, monochrome, grayscale"
        ),
        "correction_template": "改回全彩画面，恢复色彩分级",
        "negative_prompt_clause": "black and white, monochrome, grayscale, single color",
        "note": "PROMPT_SYSTEM 彩色硬性要求的上溯注册",
    },
    {
        "code": "style_conflict",
        "layer": "generator",
        "gate_enabled": False,
        "detection": (
            "Does the image mix mutually exclusive art styles (e.g. photorealistic "
            "human with anime cel-shading, or ink-wash texture with cyberpunk neon)?"
        ),
        "prevention_rule": (
            "LLM 重写结果先经 sanitize_style_conflicts 清洗互斥风格词，"
            "再追加统一画风尾（M15.4）"
        ),
        "correction_template": "移除与目标画风互斥的风格词，统一为单一画风",
        "negative_prompt_clause": "",
        "note": "M15.4 画风锚定 sanitize 场景；detection 置信度不足以进门禁",
    },
    {
        "code": "kb_atmosphere_dilution",
        "layer": "correction",
        "gate_enabled": False,
        "detection": (
            "N/A（非视觉判定，提示词层问题）：提示词中含 vibrant colors / "
            "elaborate costumes / particle effects 等 KB 氛围填充词堆叠"
        ),
        "prevention_rule": (
            "strip_kb_atmosphere 确定性剥离 KB 氛围填充词（M16.2 core E2E 实测 "
            "多角色长 prompt 下稀释 CLIP 注意力且与锁定外貌冲突）"
        ),
        "correction_template": "剥离氛围填充词，外貌描述前置",
        "negative_prompt_clause": "",
        "note": "M16.2 提示词层治理；无视觉门禁价值（gate_enabled=0）",
    },
    {
        "code": "character_appearance_drift",
        "layer": "director",
        "gate_enabled": False,
        "detection": (
            "Compared to the character reference sheet (front view), has the "
            "character's face structure, hairstyle, or signature outfit drifted "
            "to a visibly different identity across shots?"
        ),
        "prevention_rule": (
            "关键帧定妆照 IPAdapter 锚定（M18.3）+ 外观锁定卡注入（M18）；"
            "跨镜一致性靠资产库定妆照而非文本复述"
        ),
        "correction_template": "参照定妆照还原角色面部结构/发型/标志性服装",
        "negative_prompt_clause": (
            "different person, changed face, different hairstyle, inconsistent outfit"
        ),
        "note": "M18 系列核心战场；需参考图对比，VLM 单图门禁不适用（gate=0）",
    },
    {
        "code": "three_view_fail",
        "layer": "generator",
        "gate_enabled": False,
        "detection": (
            "Is this character reference sheet invalid: multiple people, a collage "
            "of reference photos, watermark, or the character not facing front?"
        ),
        "prevention_rule": (
            "三视图生成后入库前 VLM 质检（M18.2），不合格换 seed 重生成，"
            "耗尽判失败废品不入库"
        ),
        "correction_template": "重生成单角色正面定妆照，去参考表拼贴/水印",
        "negative_prompt_clause": "",
        "note": "M18.2 三视图质检场景；由 character_agent 专用链路判定",
    },
]

_LAYERS = ("generator", "correction", "director")


class FailureModeRegistry:
    """失败模式注册表（JSON 单库，线程安全）。"""

    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path or (
            Path(__file__).resolve().parents[3] / "output" / "verification" / "failure_modes.json"
        )
        self._lock = threading.Lock()
        self._cache: dict[str, FailureMode] | None = None
        self._hits: dict[str, int] | None = None

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> tuple[dict[str, FailureMode], dict[str, int]]:
        if self._cache is not None and self._hits is not None:
            return self._cache, self._hits
        defs: dict[str, FailureMode] = {}
        hits: dict[str, int] = {}
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for code, d in (raw.get("defs") or {}).items():
                    defs[code] = FailureMode(**d)
                hits = {k: int(v) for k, v in (raw.get("hits") or {}).items()}
            except Exception as e:
                logger.warning("失败模式注册表读取损坏，重建种子: %s", e)
        self._cache, self._hits = defs, hits
        self.ensure_seeded()
        return self._cache, self._hits

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "defs": {k: v.model_dump() for k, v in (self._cache or {}).items()},
            "hits": self._hits or {},
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def ensure_seeded(self) -> None:
        """幂等播种：种子模式 UPSERT 进库（不覆盖用户改写的文案）。"""
        assert self._cache is not None and self._hits is not None
        changed = False
        for seed in SEED_FAILURE_MODES:
            code = seed["code"]
            if code not in self._cache:
                self._cache[code] = FailureMode(
                    code=code,
                    layer=seed["layer"],
                    detection=seed["detection"],
                    prevention_rule=seed["prevention_rule"],
                    correction_template=seed["correction_template"],
                    negative_prompt_clause=seed["negative_prompt_clause"],
                    gate_enabled=bool(seed["gate_enabled"]),
                    hit_count=0,
                    created_at=int(time.time()),
                    updated_at=int(time.time()),
                )
                changed = True
        if changed:
            self._save()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_active(self, layer: str | None = None, gate_only: bool = False) -> list[FailureMode]:
        defs, _ = self._load()
        modes = sorted(defs.values(), key=lambda m: m.code)
        if layer is not None:
            modes = [m for m in modes if m.layer == layer]
        if gate_only:
            modes = [m for m in modes if m.gate_enabled]
        return modes

    def get(self, code: str) -> FailureMode | None:
        defs, _ = self._load()
        return defs.get(code)

    def hits(self) -> dict[str, int]:
        _, hits = self._load()
        return dict(hits)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def upsert(self, code: str, **fields: Any) -> FailureMode:
        """新增/更新失败模式（白名单字段）。"""
        defs, _ = self._load()
        with self._lock:
            existing = defs.get(code)
            allowed = {
                "layer", "detection", "prevention_rule", "correction_template",
                "negative_prompt_clause", "gate_enabled",
            }
            updates = {k: v for k, v in fields.items() if k in allowed}
            if existing is None:
                if not fields.get("layer") or not fields.get("detection"):
                    raise ValueError(f"新失败模式 {code} 必须至少指定 layer 与 detection")
                existing = FailureMode(
                    code=code,
                    created_at=int(time.time()),
                    updated_at=int(time.time()),
                    **updates,
                )
                defs[code] = existing
            else:
                for k, v in updates.items():
                    setattr(existing, k, v)
                existing.updated_at = int(time.time())
            self._save()
            return existing

    def bump_hit(self, code: str) -> int:
        """命中计数 +1（重复犯错者上浮），返回新计数。"""
        defs, hits = self._load()
        with self._lock:
            hits[code] = hits.get(code, 0) + 1
            mode = defs.get(code)
            if mode is not None:
                mode.hit_count = hits[code]
                mode.updated_at = int(time.time())
            self._save()
            return hits[code]

    # ------------------------------------------------------------------
    # 提示词子句构建
    # ------------------------------------------------------------------

    def build_negative_prompt_clause(self, layer: str) -> str:
        """按层构建反向提示词子句（多 bullet 拼接，供生成负面提示词注入）。"""
        modes = self.list_active(layer=layer)
        bullets = [f"- {m.negative_prompt_clause}" for m in modes if m.negative_prompt_clause]
        if not bullets:
            return ""
        return "\n".join([f"NEGATIVE CONSTRAINTS ({layer} layer, registry-driven):", *bullets])

    def gate_modes(self) -> list[FailureMode]:
        """门禁启用的模式（VLM 判定用）。"""
        return self.list_active(gate_only=True)


# 全局单例
failure_registry = FailureModeRegistry()
