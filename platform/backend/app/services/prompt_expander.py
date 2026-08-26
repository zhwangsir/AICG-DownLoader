"""统一提示词扩写系统 — 短剧场景 IR → H3/LTX-2.5 双引擎编译器。

M21：把用户中文场景描述经 LLM（spark02，get_shared_llm_client）扩写为结构化
ShotSpec（镜头 IR），再按目标引擎编译为原生 prompt 契约：
- H3：官方 Context-IR 三字段格式（对齐指令 + integrated_multimodal_description
  + overall_soundscape + non_diegetic_music），台词 `<d>[语言] 原文</d> (Sx)`；
- LTX-2.5：单段现在时连贯散文（六要素：镜头规模→场景(灯光/色调)→动作→
  角色(肢体线索)→运镜→音频(引号内台词注明语言)），多镜用转场动词连接。

LLM 扩写失败/坏 JSON/开关关闭时回退确定性模板拼接（不阻断生产）。
另含 H3 prompt 机械校验器 validate_h3_prompt 与双引擎质量参数推荐
recommended_quality_params（模块C）。
"""

from __future__ import annotations

import logging
import re

import json_repair
from pydantic import BaseModel, Field

from app.agents.base import get_shared_llm_client, strip_think_tags
from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据模型：短剧场景镜头 IR
# ---------------------------------------------------------------------------


class DialogueSpec(BaseModel):
    """单条台词（speaker_id 从 1 开始连续编号，与 H3 `(Sx)` 契约一致）。"""

    speaker_id: int = Field(1, description="说话人编号（从 1 开始连续）")
    language: str = Field("zh", description="台词语言代码（zh/en/ja/ko...）")
    text: str = Field("", description="台词原文")


class ShotSpec(BaseModel):
    """短剧单镜头结构化 IR（双引擎编译器的共同输入）。"""

    duration_ms: int = Field(3000, description="镜头时长（毫秒）")
    shot_type: str = Field("medium shot", description="镜头规模/景别（英文）")
    setting: str = Field("", description="场景环境（灯光/色调，英文）")
    camera_movement: str = Field("static", description="运镜（static/pan/dolly...）")
    action: str = Field("", description="动作描述（英文现在时）")
    characters: list[str] = Field(
        default_factory=list, description="角色描述（用肢体线索表达情绪，英文）"
    )
    dialogue: list[DialogueSpec] = Field(default_factory=list, description="台词列表")
    ambience: str = Field("", description="环境声/声景（英文，空 → N/A）")
    music: str = Field("", description="非叙事配乐（英文，空 → N/A）")
    reference_assets: list[str] = Field(
        default_factory=list, description="参考资产 URL（定妆照/关键帧等）"
    )
    mode: str = Field(
        "t2va", description="H3 对齐模式：i2va（首帧）/ fl2va（首尾帧）/ t2va（纯文本）"
    )


# ---------------------------------------------------------------------------
# H3 Context-IR 编译器 + 机械校验器
# ---------------------------------------------------------------------------

# 台词语言代码 → 自然语言名（LTX 散文注明语言用；H3 侧保留语言代码）
_LANGUAGE_NAMES = {
    "zh": "Mandarin Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
}


def _camera_sentence(camera_movement: str) -> str:
    """运镜字段 → 现在时散文句（static 也会给出稳定画面声明）。"""
    movement = (camera_movement or "static").strip()
    if movement.lower() == "static":
        return "The camera holds a steady frame."
    return f"The camera performs a {movement}."


class H3ContextIRCompiler:
    """把 ShotSpec 编译为 H3 官方 Context-IR 三字段 prompt。

    输出结构：
      首行：对齐指令（按 i2va/fl2va/t2va 模式；t2va 无图片锚定省略该行）
      integrated_multimodal_description: [Shot 1] ...（台词 `<d>[语言] 原文</d> (Sx)`）
      overall_soundscape: ...（空 → N/A）
      non_diegetic_music: ...（空 → N/A，位于最末——官方 prompt 结构约定）
    """

    def compile(self, shot: ShotSpec) -> str:
        duration_s = shot.duration_ms / 1000.0
        lines: list[str] = []

        # 首行对齐指令（官方固定句式：Picture N → S.SSs）
        mode = (shot.mode or "t2va").strip().lower()
        if mode == "i2va":
            lines.append(
                "How the reference pictures align with the target video — "
                "Picture 1 (from Shot 1) aligns with the 0.00-second mark "
                "of the target video."
            )
        elif mode == "fl2va":
            lines.append(
                "How the reference pictures align with the target video — "
                "Picture 1 (from Shot 1) aligns with the 0.00-second mark "
                "of the target video; "
                f"Picture 2 (from Shot 1) aligns with the {duration_s:.2f}-second "
                "mark of the target video."
            )

        # integrated_multimodal_description：单镜 [Shot 1] 不带时间戳
        segments: list[str] = [f"[Shot 1] A {shot.shot_type}."]
        if shot.setting.strip():
            segments.append(shot.setting.strip().rstrip(".") + ".")
        if shot.action.strip():
            segments.append(shot.action.strip().rstrip(".") + ".")
        for char in shot.characters:
            if char.strip():
                segments.append(char.strip().rstrip(".") + ".")
        for line in shot.dialogue:
            if line.text.strip():
                segments.append(
                    f"<d>[{line.language}] {line.text.strip()}</d> (S{line.speaker_id})"
                )
        if (shot.camera_movement or "").strip().lower() not in ("", "static"):
            segments.append(_camera_sentence(shot.camera_movement))
        lines.append("integrated_multimodal_description: " + " ".join(segments))

        lines.append(f"overall_soundscape: {shot.ambience.strip() or 'N/A'}")
        lines.append(f"non_diegetic_music: {shot.music.strip() or 'N/A'}")
        return "\n".join(lines)


# H3 prompt 机械校验正则
_H3_TS_RE = re.compile(r"\bAt (\d{2}):(\d{2})\.(\d{3})\b")
_H3_SPEAKER_RE = re.compile(r"\(S(\d+)\)")
_H3_DIALOGUE_TAG_RE = re.compile(r"<d>|</d>")


def validate_h3_prompt(text: str, total_duration_ms: int) -> list[str]:
    """H3 Context-IR prompt 机械校验器，返回错误列表（空列表 = 合法）。

    校验规则：
    1. `At MM:SS.mmm` 切镜时间戳严格递增且不超过总时长；
    2. `<d>` / `</d>` 台词标签配对且嵌套顺序正确；
    3. `(Sx)` 说话人编号按首次出现顺序从 S1 开始连续。
    """
    errors: list[str] = []

    # 1. 时间戳递增 + 上限
    last_ms = -1
    for m in _H3_TS_RE.finditer(text):
        ts_ms = int(m.group(1)) * 60000 + int(m.group(2)) * 1000 + int(m.group(3))
        if ts_ms <= last_ms:
            errors.append(f"时间戳未严格递增: {m.group(0)}")
        if ts_ms > total_duration_ms:
            errors.append(
                f"时间戳超出总时长: {m.group(0)} > {total_duration_ms}ms"
            )
        last_ms = ts_ms

    # 2. <d> 标签配对（数量相等且顺序正确闭合）
    depth = 0
    pairing_ok = True
    for tok in _H3_DIALOGUE_TAG_RE.findall(text):
        if tok == "<d>":
            depth += 1
        else:
            depth -= 1
            if depth < 0:
                pairing_ok = False
                break
    if depth != 0 or not pairing_ok:
        errors.append("<d> 台词标签未正确配对")

    # 3. (Sx) 说话人编号连续（按首次出现顺序 1..k）
    first_seen: list[int] = []
    for raw in _H3_SPEAKER_RE.findall(text):
        sid = int(raw)
        if sid not in first_seen:
            first_seen.append(sid)
    if first_seen != list(range(1, len(first_seen) + 1)):
        errors.append(f"说话人编号未从 S1 开始连续编号: {first_seen}")

    return errors


# ---------------------------------------------------------------------------
# LTX-2.5 散文式编译器
# ---------------------------------------------------------------------------


class LTXProseCompiler:
    """把 ShotSpec 编译为 LTX-2.5 散文式 prompt（单段、现在时、4-8 句/单镜）。

    六要素固定顺序：镜头规模 → 场景(灯光/色调) → 动作 → 角色(肢体线索表达情绪)
    → 运镜 → 音频(环境声 + 引号内台词注明语言 + 配乐)。
    """

    def compile(self, shot: ShotSpec) -> str:
        sentences: list[str] = []
        # 1. 镜头规模
        sentences.append(f"A {shot.shot_type}.")
        # 2. 场景（灯光/色调），缺省给中性打光保证句数下限
        setting = shot.setting.strip() or "Natural, even lighting with a neutral color tone"
        sentences.append(setting.rstrip(".") + ".")
        # 3. 动作（现在时）
        if shot.action.strip():
            sentences.append(shot.action.strip().rstrip(".") + ".")
        # 4. 角色（肢体线索表达情绪）
        for char in shot.characters:
            if char.strip():
                sentences.append(char.strip().rstrip(".") + ".")
        # 5. 运镜
        sentences.append(_camera_sentence(shot.camera_movement))
        # 6. 音频：环境声 + 引号内台词（注明语言）+ 配乐
        if shot.ambience.strip():
            sentences.append(f"The air carries {shot.ambience.strip().rstrip('.')}.")
        for line in shot.dialogue:
            if line.text.strip():
                language = _LANGUAGE_NAMES.get(line.language, line.language)
                sentences.append(
                    f'Speaker {line.speaker_id} says in {language}, '
                    f'"{line.text.strip()}"'
                )
        if shot.music.strip():
            sentences.append(f"{shot.music.strip().rstrip('.')} plays underneath.")
        return " ".join(sentences)

    def compile_sequence(self, shots: list[ShotSpec]) -> str:
        """多镜散文：转场动词连接各镜，末尾声明音频连续性。"""
        if not shots:
            return ""
        parts: list[str] = []
        for idx, shot in enumerate(shots):
            text = self.compile(shot)
            if idx > 0:
                text = f"The camera cuts to the next shot. {text}"
            parts.append(text)
        parts.append(
            "The soundtrack and ambience continue seamlessly across the cuts."
        )
        return " ".join(parts)


# ---------------------------------------------------------------------------
# PromptExpander：LLM 扩写 → 引擎分发（失败回退确定性模板）
# ---------------------------------------------------------------------------

# LLM 扩写允许覆盖的 ShotSpec 字段白名单（时长/模式/参考资产由调用方控制）
_EXPANDABLE_FIELDS = (
    "shot_type",
    "setting",
    "action",
    "characters",
    "camera_movement",
    "dialogue",
    "ambience",
    "music",
)

_EXPAND_SYSTEM_PROMPT = (
    "你是短剧视频提示词工程师。把用户的中文场景描述扩写为结构化镜头 JSON。\n"
    "只输出 JSON（不要 markdown 代码块），字段：\n"
    '- "shot_type": 镜头规模（英文，如 "medium close-up"）\n'
    '- "setting": 场景环境与灯光/色调（英文一句）\n'
    '- "action": 动作（英文现在时一句）\n'
    '- "characters": 角色描述列表（英文，用肢体线索表达情绪）\n'
    '- "camera_movement": 运镜（英文短语，如 "slow dolly in"，静止为 "static"）\n'
    '- "dialogue": 台词列表 [{"speaker_id": 1, "language": "zh", "text": "原文"}]\n'
    '- "ambience": 环境声（英文一句）\n'
    '- "music": 配乐（英文一句，无则空串）\n'
    "示例输入：雨夜，外卖员站在便利店门口看着手机发呆\n"
    '示例输出：{"shot_type": "medium shot", "setting": "Cold fluorescent light '
    'spills from a convenience store into the rainy night", "action": "A delivery '
    'rider stands at the store entrance, staring at his phone in a daze", '
    '"characters": ["A young rider in a soaked yellow jacket, shoulders slumped"], '
    '"camera_movement": "static", "dialogue": [], "ambience": "rain patter on the '
    'awning and distant traffic", "music": ""}'
)


class PromptExpander:
    """统一提示词扩写入口：中文场景描述 → ShotSpec → 引擎原生 prompt。"""

    def __init__(self) -> None:
        self.h3_compiler = H3ContextIRCompiler()
        self.ltx_compiler = LTXProseCompiler()

    async def expand(
        self, scene_description: str, engine: str, shot: ShotSpec
    ) -> str:
        """扩写并编译。LLM 失败/坏 JSON/开关关闭时回退确定性模板（不阻断）。"""
        engine_key = (engine or "").strip().lower()
        effective = shot
        if settings.prompt_expander_enabled and (scene_description or "").strip():
            try:
                expanded = await self._expand_via_llm(scene_description, shot)
                if expanded is not None:
                    effective = expanded
            except Exception as e:
                logger.warning("LLM 提示词扩写失败，回退确定性模板: %s", e)

        if engine_key == "h3":
            return self.h3_compiler.compile(effective)
        if engine_key in ("ltx", "ltx25", "ltx-2.5"):
            return self.ltx_compiler.compile(effective)
        raise ValueError(f"未知视频引擎: {engine}")

    async def _expand_via_llm(
        self, scene_description: str, shot: ShotSpec
    ) -> ShotSpec | None:
        """调共享 LLM 把场景描述扩写进 ShotSpec 字段；解析失败返回 None。"""
        client = get_shared_llm_client()
        user_msg = (
            f"场景描述：{scene_description}\n"
            f"已有镜头字段（如无更好判断请保留）："
            f"{shot.model_dump_json(include=set(_EXPANDABLE_FIELDS))}"
        )
        resp = await client.chat.completions.create(
            model=settings.exo_model_glm52,
            messages=[
                {"role": "system", "content": _EXPAND_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        raw = strip_think_tags(resp.choices[0].message.content or "")
        data = json_repair.loads(raw)
        if not isinstance(data, dict):
            return None
        merged = shot.model_dump()
        for key in _EXPANDABLE_FIELDS:
            if key in data and data[key] is not None:
                merged[key] = data[key]
        return ShotSpec.model_validate(merged)


# ---------------------------------------------------------------------------
# 模块C：H3/LTX 质量参数推荐
# ---------------------------------------------------------------------------

_H3_TIER_PARAMS: dict[str, dict] = {
    # 预览：Turbo LoRA 4 步（最快速度，构图/运动确认用）
    "preview": {"quality_tier": "preview", "turbo": True, "steps": 4},
    # 交付：Turbo LoRA 6-8 步（速度/画质平衡，默认 8 步上限）
    "delivery": {
        "quality_tier": "delivery",
        "turbo": True,
        "steps": 8,
        "steps_range": [6, 8],
    },
    # 基线：原生 20 步（最高画质基准）
    "baseline": {"quality_tier": "baseline", "turbo": False, "steps": 20},
}

_LTX_TIER_PARAMS: dict[str, dict] = {
    # 预览：distilled 单阶段 8 步
    "preview": {
        "quality_tier": "preview",
        "mode": "distilled",
        "stages": 1,
        "steps": 8,
        "cfg": 1.0,
        "sampler": "euler_ancestral_cfg_pp",
    },
    # 交付：distilled 两阶段 8+3 步（半分辨率 8 步 → ×2 upscale → 全分辨率 3 步）
    "delivery": {
        "quality_tier": "delivery",
        "mode": "distilled",
        "stages": 2,
        "stage_steps": [8, 3],
        "steps": 11,
        "cfg": 1.0,
        "sampler": "euler_ancestral_cfg_pp",
    },
    # 拉满：dev 模型 15-40 步 + distilled LoRA(0.2-0.5) + MultimodalGuider CFG 3.0-3.5
    "max": {
        "quality_tier": "max",
        "mode": "dev",
        "steps_range": [15, 40],
        "cfg": 3.0,
        "cfg_range": [3.0, 3.5],
        "guider": "MultimodalGuider",
        "distilled_lora_strength_range": [0.2, 0.5],
    },
}


def recommended_quality_params(engine: str, quality_tier: str) -> dict:
    """按引擎与质量档返回推荐采样参数（调研结论的确定性常量表）。

    H3 公共：sampler res_multistep / scheduler simple / shift video=12 audio=3 /
    帧数 17k+5 网格 / 短边≥384 且 32 倍数。
    LTX-2.5 公共：25fps / 帧数 %8==1 / 宽高 32 倍数。
    未知引擎或质量档抛 ValueError。
    """
    engine_key = (engine or "").strip().lower()
    tier_key = (quality_tier or "").strip().lower()

    if engine_key == "h3":
        base: dict = {
            "engine": "h3",
            "sampler": "res_multistep",
            "scheduler": "simple",
            "shift_video": 12.0,
            "shift_audio": 3.0,
            "frame_grid": "17k+5",
            "fps": 24,
            "min_short_side": 384,
            "dim_multiple": 32,
        }
        tier = _H3_TIER_PARAMS.get(tier_key)
    elif engine_key in ("ltx", "ltx25", "ltx-2.5"):
        base = {
            "engine": "ltx",
            "fps": 25,
            "frame_rule": "%8==1",
            "dim_multiple": 32,
        }
        tier = _LTX_TIER_PARAMS.get(tier_key)
    else:
        raise ValueError(f"未知视频引擎: {engine}")

    if tier is None:
        raise ValueError(f"引擎 {engine} 未知质量档: {quality_tier}")
    return {**base, **tier}
