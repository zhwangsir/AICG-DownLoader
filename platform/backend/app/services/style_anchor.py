"""画风锚定：统一解析画风字符串为英文风格关键词与写实性分类。

剧本 / 角色 / 分镜 Agent 共用，替代各处硬编码的 photorealistic 写实关键词，
保证「剧本场景 prompt → 角色定妆照 → 分镜关键帧 → H3 视频」全链路画风一致。

数据源：knowledge_base/styles.json（与 RAG 知识库同源，但仅做 JSON 解析，
不依赖嵌入模型，RAG 未初始化 / 外网不可达时同样可用）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from app.knowledge_base import KB_DIR

logger = logging.getLogger(__name__)

# 未匹配到任何画风时的安全兜底（与 schemas 各处 style 默认值一致）
DEFAULT_STYLE_TITLE = "写实电影感"

# 写实性分类规则（由 KB negative_terms 推导）：
# 出现「写实冲突词」→ 该风格排斥写实，判定为非写实（动漫/插画/风格化渲染向）；
# 出现「渲染冲突词」→ 该风格排斥动漫渲染，判定为写实（真人摄影向）；
# 两者都没有默认写实（短剧以真人写实为主）。
_NON_REALISTIC_TERMS = {"realistic", "photorealistic", "live action"}
_REALISTIC_TERMS = {"anime", "cartoon", "illustration", "3d render"}


@dataclass(frozen=True)
class StyleAnchor:
    """画风解析结果。"""

    key: str  # KB 条目 id（合成兜底时为 "fallback"）
    title: str  # 中文画风名
    keywords_en: str  # 英文风格锚定关键词（KB content 的英文部分）
    style_name_en: str  # keywords_en 首个逗号段（风格名本身，如 "Chinese anime guoman style"）
    negative_en: str  # 冲突风格负面词（逗号连接）
    is_realistic: bool  # True=写实/真人摄影向；False=动漫/插画/风格化渲染向

    @property
    def realism_tail_en(self) -> str:
        """写实风格专用画质尾巴（非写实风格为空串）。"""
        return "photorealistic, professional photography" if self.is_realistic else ""


def _normalize(text: object) -> str:
    """归一化用于匹配：小写 + 去除全部空白（兼容 '卡通 3D' / '卡通3D'）。"""
    return "".join(str(text or "").lower().split())


def _extract_english(content: str) -> str:
    """从 '国漫风格：Chinese anime guoman style, ...' 提取英文关键词部分。"""
    text = content.strip()
    if "：" in text:
        text = text.split("：", 1)[1]
    return text.strip().rstrip(".").strip()


def _entry_to_anchor(raw: dict) -> StyleAnchor:
    keywords = _extract_english(str(raw.get("content", "")))
    negatives = [str(t).strip() for t in raw.get("negative_terms", []) if str(t).strip()]
    neg_lower = {t.lower() for t in negatives}
    if neg_lower & _NON_REALISTIC_TERMS:
        is_realistic = False
    else:
        # 命中渲染冲突词或无任何信号，均按写实处理（默认写实）
        is_realistic = True
    return StyleAnchor(
        key=str(raw.get("id", "")),
        title=str(raw.get("title", "")),
        keywords_en=keywords,
        style_name_en=keywords.split(",", 1)[0].strip() if keywords else "",
        negative_en=", ".join(negatives),
        is_realistic=is_realistic,
    )


@lru_cache(maxsize=1)
def _load_entries() -> tuple[dict, ...]:
    """加载 styles.json 条目（进程内缓存一次）。"""
    path = KB_DIR / "styles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return tuple(data.get("entries", []))
    except Exception as e:
        logger.warning("画风知识库加载失败，使用合成兜底: %s", e)
        return ()


def _fallback_anchor() -> StyleAnchor:
    """KB 不可用时的合成兜底（等价于写实电影感的核心关键词）。"""
    return StyleAnchor(
        key="fallback",
        title=DEFAULT_STYLE_TITLE,
        keywords_en="cinematic realistic, film grain, natural lighting, shallow depth of field",
        style_name_en="cinematic realistic",
        negative_en="anime, cartoon, 3d render, illustration",
        is_realistic=True,
    )


def style_positive_tail(anchor: StyleAnchor) -> str:
    """追加到正向提示词末尾的画风锚定尾巴（风格名 + 写实画质尾）。

    用于 LLM 生成 prompt 后的强制锚定：即使 LLM 漏写风格关键词，
    也能保证下游图像/视频模型收到与剧本层一致的画风信号。
    返回串以 ", " 开头；无可用风格名时可能仅剩写实画质尾或为空串。
    """
    tail = f", {anchor.style_name_en}" if anchor.style_name_en else ""
    if anchor.realism_tail_en:
        tail += f", {anchor.realism_tail_en}"
    return tail


def style_negative_tail(anchor: StyleAnchor) -> str:
    """追加到反向提示词末尾的冲突画风负面词（如动漫风排斥 photorealistic）。

    返回串以 ", " 开头；KB 未提供负面词时为空串。
    """
    return f", {anchor.negative_en}" if anchor.negative_en else ""


def style_prompt_clause(anchor: StyleAnchor, *, target: str) -> str:
    """M16.1 风格词与外貌词权重分离的 LLM 画风子句。

    背景：旧版子句要求提示词「显式包含」KB 整串风格关键词（国漫条目含
    vibrant colors / fantasy elements / elaborate costumes / particle effects
    等内容词），LLM 依令注入后与角色外貌描述争权重 —— core E2E
    （pipeline-7470e3e104d9）定妆照产出银灰发，与剧本「黑色齐肩短发」相悖。

    分离规则：
    - 必填：仅风格名 style_name_en（如 "Chinese anime guoman style"）约束渲染风格
    - 可选：KB 完整关键词降为氛围参考，由 LLM 自行取舍，不再强制全量注入
    - 优先级：外貌描述（发色/发型/五官/服装款式与颜色）> 风格氛围词，冲突时舍弃氛围词

    首行无 bullet 前缀（跟随模板 "- {style_clause}"），后续行自带 "- " 前缀。
    """
    clause = (
        f"画风统一：{target}风格必须严格统一为「{anchor.title}」，"
        f"每个提示词必须显式包含风格关键词 \"{anchor.style_name_en}\""
    )
    if anchor.keywords_en and anchor.keywords_en != anchor.style_name_en:
        clause += f"\n- 风格氛围参考（可选，不必全部使用）：{anchor.keywords_en}"
    clause += (
        "\n- 权重分离规则：角色外貌描述（发色、发型、五官、服装款式与颜色）权重高于风格氛围词，"
        "与外貌冲突的氛围词（如 elaborate costumes 改变指定服装、"
        "vibrant hair colors 改变指定发色）必须舍弃"
    )
    return clause


# M15.7 SDXL checkpoint 选型：majicMIX 为真人摄影特化模型，即使用动漫锚定尾
# 也只会产出写实图（core E2E pipeline-1a92d5f7a966 实测：国漫提示词 + majicMIX
# → 写实定妆照）。两个 checkpoint 均存在于 workstation 本地与 NAS 共享模型库
# （gpu0 / pc01 / pc02 三后端可用）。
SDXL_CHECKPOINT_REALISTIC = "majicMIX realistic 麦橘写实_v7.safetensors"
SDXL_CHECKPOINT_ANIME = "animagineXL40.safetensors"


def sdxl_checkpoint_for_anchor(anchor: StyleAnchor | None) -> str:
    """按画风写实性选择 SDXL checkpoint。

    写实/真人摄影向 → majicMIX；动漫/插画/风格化渲染向 → animagineXL40。
    anchor 为 None 时按写实兜底（与 DEFAULT_STYLE_TITLE 一致）。
    """
    if anchor is not None and not anchor.is_realistic:
        return SDXL_CHECKPOINT_ANIME
    return SDXL_CHECKPOINT_REALISTIC


# M15.4 冲突风格词清洗：LLM 产出正文里常自带与目标画风互斥的风格词
# （如请求国漫却写出 hyperrealistic、负面词反向排斥 anime），
# 仅在末尾追加锚定尾无法抵消正文冲突信号，必须在追加前清洗正文。
# 写实摄影家族 / 动漫渲染家族 互斥。
_REALISM_FAMILY_TERMS = (
    "photorealistic",
    "hyperrealistic",
    "hyper-realistic",
    "professional photography",
    "film still",
    "live action",
    "realistic photo",
    # M15.5：core E2E（pipeline-3ba8b3b3e304）实测残留 — 短语优先于裸词，
    # 正则交替按声明顺序匹配，长词在前避免被裸词截断；词边界保护 unrealistic/surrealism
    "cinematic realism",
    "realistic",
    "realism",
)
_ANIME_FAMILY_TERMS = (
    "anime",
    "cartoon",
    "cel shading",
    "illustration",
    "painting",
    "3d render",
    "cgi",
)


def _conflict_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    # 词边界匹配，避免误伤 unrealistic / photorealism 等派生词
    return re.compile(
        r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b",
        re.IGNORECASE,
    )


def sanitize_style_conflicts(text: str, anchor: StyleAnchor, *, negative: bool = False) -> str:
    """清除 LLM 产出中与目标画风互斥的风格词。

    规则：
    - 正向提示词：删除对立画风家族词（目标非写实 → 删写实词；目标写实 → 删动漫词）
    - 反向提示词：删除目标画风家族词（反向词不得排斥目标画风本身）

    非风格词（blurry / low quality / bad anatomy 等质量词）一律保留。
    空串原样返回。
    """
    if not text or not text.strip():
        return text
    if negative:
        drop = _ANIME_FAMILY_TERMS if not anchor.is_realistic else _REALISM_FAMILY_TERMS
    else:
        drop = _REALISM_FAMILY_TERMS if not anchor.is_realistic else _ANIME_FAMILY_TERMS
    cleaned = _conflict_pattern(drop).sub("", text)
    # 收口残留标点：折叠空白 → 规范逗号间隔 → 去连续/首尾逗号
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"(?:,\s*){2,}", ", ", cleaned)
    return cleaned.strip(" ,")


def strip_kb_atmosphere(text: str, anchor: StyleAnchor) -> str:
    """M16.2 短 prompt 重构：剥离 KB 氛围填充词，保留风格名与实质内容。

    背景：core E2E（pipeline-87d6d5791120）实测 — 分镜 prompt 中角色外貌
    描述完全正确（black straight long hair reaching waist / white shirt /
    dark gray pleated skirt），但 animagineXL40 产出仍为模型先验校服
    （侧马尾 + 深色西装 + 红领结）。多角色长 prompt 下 KB 氛围词
    （elaborate costumes / fantasy elements / particle effects / dynamic
    poses / dramatic expressions 等）稀释 CLIP 注意力，且与锁定外貌直接冲突
    （elaborate costumes ↔ 白衬衫百褶裙）。M16.1 已将整串降为「可选」，
    但 LLM 仍习惯性全量注入，需程序确定性剥离。

    规则：
    - 仅非写实画风：剥离 keywords_en 中除 style_name_en 外的全部分段
      （风格由 style_name_en + checkpoint 双保险，氛围词可安全移除）
    - 写实画风不剥离：KB 词为摄影技术词（film grain / natural lighting），
      不与外貌争权重，保留有益
    - 空串原样返回
    """
    if not text or not text.strip() or anchor.is_realistic:
        return text
    segments = [s.strip() for s in anchor.keywords_en.split(",") if s.strip()]
    filler = [s for s in segments if s and s != anchor.style_name_en]
    if not filler:
        return text
    cleaned = _conflict_pattern(tuple(filler)).sub("", text)
    # 收口残留标点（与 sanitize_style_conflicts 同一套收口规则）
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"(?:,\s*){2,}", ", ", cleaned)
    return cleaned.strip(" ,")


def resolve_style_anchor(style: str | None) -> StyleAnchor:
    """将画风字符串解析为 StyleAnchor。

    匹配优先级：title 精确 → id 精确 → title 归一化互包含 → tags 命中。
    全部未命中时回退默认画风「写实电影感」（KB 缺失则用合成兜底）。
    """
    entries = _load_entries()
    query = _normalize(style)
    if query:
        for e in entries:
            if query == _normalize(e.get("title")):
                return _entry_to_anchor(e)
        for e in entries:
            if query == _normalize(e.get("id")):
                return _entry_to_anchor(e)
        for e in entries:
            title_norm = _normalize(e.get("title"))
            if title_norm and (query in title_norm or title_norm in query):
                return _entry_to_anchor(e)
        for e in entries:
            if query in {_normalize(t) for t in e.get("tags", [])}:
                return _entry_to_anchor(e)
        logger.info("画风 %r 未命中知识库，回退默认画风 %s", style, DEFAULT_STYLE_TITLE)
    for e in entries:
        if _normalize(e.get("title")) == _normalize(DEFAULT_STYLE_TITLE):
            return _entry_to_anchor(e)
    return _fallback_anchor()


# ---------------------------------------------------------------------------
# P4 漫剧 style pack: anime/comic keyframes + same-lane H3 Ref2VA.
# Video engine stays MiniMax H3 FL2VA/Ref2VA — never a second video model.
# ---------------------------------------------------------------------------
MANJU_STYLE_ALIASES = frozenset(
    {
        "漫剧",
        "漫画剧",
        "manju",
        "style_manju",
        "comic-drama",
        "comic drama",
        "manhua-drama",
        "manhua drama",
    }
)
MANJU_KEYFRAME_BIAS_EN = (
    "Chinese manhua comic-drama style, clean ink line art, cel shading, "
    "comic panel composition, expressive faces, graphic novel coloring"
)
MANJU_IPADAPTER_WEIGHT = 0.85
MANJU_VIDEO_ENGINE = "h3"
MANJU_VIDEO_MODES = ("fl2va", "ref2va")


def is_manju_style_pack(style: str | StyleAnchor | None) -> bool:
    """True when the request is the P4 漫剧 pack (or KB 漫剧 entry)."""
    if style is None:
        return False
    if isinstance(style, StyleAnchor):
        if style.key == "style_manju" or _normalize(style.title) == _normalize("漫剧"):
            return True
        style = style.title or style.key
    raw = str(style or "").strip()
    if not raw:
        return False
    if _normalize(raw) in {_normalize(a) for a in MANJU_STYLE_ALIASES}:
        return True
    anchor = resolve_style_anchor(raw)
    return anchor.key == "style_manju" or _normalize(anchor.title) == _normalize("漫剧")


def manju_style_pack(style: str | StyleAnchor | None = None) -> dict[str, object]:
    """Keyframe/IPAdapter/ref bias for 漫剧. Video engine is always H3."""
    anchor = style if isinstance(style, StyleAnchor) else resolve_style_anchor(style or "漫剧")
    return {
        "key": "style_manju",
        "title": "漫剧",
        "keyframe_prompt_bias": MANJU_KEYFRAME_BIAS_EN,
        "ipadapter_weight": MANJU_IPADAPTER_WEIGHT,
        "sdxl_checkpoint": sdxl_checkpoint_for_anchor(anchor) if not anchor.is_realistic else SDXL_CHECKPOINT_ANIME,
        "video_engine": MANJU_VIDEO_ENGINE,
        "video_modes": MANJU_VIDEO_MODES,
        "same_lane_ref2va": True,
    }


def ipadapter_weight_for_anchor(
    anchor: StyleAnchor | None,
    default: float = 0.6,
    style: str | None = None,
) -> float:
    """漫剧 pack uses a stronger IPAdapter lock; other styles keep the default."""
    if is_manju_style_pack(anchor or style):
        return MANJU_IPADAPTER_WEIGHT
    return float(default)


def video_engine_for_style(style: str | StyleAnchor | None) -> str:
    """漫剧 stays on H3 FL2VA/Ref2VA. Other styles do not force a second engine."""
    if is_manju_style_pack(style):
        return MANJU_VIDEO_ENGINE
    return MANJU_VIDEO_ENGINE  # product is H3-only; pack still must not leave H3

