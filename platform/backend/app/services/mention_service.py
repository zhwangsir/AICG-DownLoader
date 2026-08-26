"""@角色提及解析服务（M24.1 主体库 @引用可视化）。

分镜/视频提示词框支持 `@角色名` 语法：
- extract_mentions：从文本提取全部 @提及（去重、保持首次出现顺序）
- resolve_mentions：提及名 → 角色资产库映射（精确 → 大小写不敏感 → 模糊包含），
  返回角色ID / 角色名 / 定妆照 URL / 外观锁定卡，并生成展开文本
  （锁定角色的 appearance_lock 拼入前缀段，定妆照 front 归入 reference_images）。

对标 LibTV 主体库 AutoLink 的显式子集：只做「显式 @ + 可视化」，不做全自动匹配。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.schemas import CharacterAsset, MentionResolveData
from app.services.character_library import CharacterLibrary, character_library

logger = logging.getLogger(__name__)

# @名称：中日文/字母/数字/下划线/连字符，遇空白或标点终止
_MENTION_RE = re.compile(r"@([\w\u4e00-\u9fff\-]+)")

# 单次解析的提及数量上限（防滥用/防误粘贴大段文本卡死匹配）
MAX_MENTIONS = 20


def extract_mentions(text: str) -> list[str]:
    """从文本提取全部 @角色名（去重，保持首次出现顺序）。

    >>> extract_mentions("@云曦 和 @林远 对话，@云曦 转身")
    ['云曦', '林远']
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for m in _MENTION_RE.finditer(text):
        seen.setdefault(m.group(1))
    return list(seen.keys())


def _match_asset(name: str, assets: list) -> tuple[Any | None, str]:
    """按 精确 → 大小写不敏感 → 模糊包含 三级匹配角色资产。

    返回 (asset, match_type)；未匹配返回 (None, "")。
    模糊匹配取名称最长者（更具体的资产优先），并列时保持资产库顺序（更新时间倒序）。
    """
    for a in assets:
        if a.name == name:
            return a, "exact"
    lowered = name.lower()
    for a in assets:
        if a.name and a.name.lower() == lowered:
            return a, "ci"
    candidates = [a for a in assets if a.name and (name in a.name or a.name in name)]
    if candidates:
        # 与提及文本长度差最小者优先（"@云" → "云曦" 而非 "小云曦"），
        # 并列时取名称更长者（更具体的资产）
        candidates.sort(key=lambda a: (abs(len(a.name) - len(name)), -len(a.name)))
        return candidates[0], "fuzzy"
    return None, ""


def resolve_mentions(text: str, library: CharacterLibrary | None = None) -> dict[str, Any]:
    """解析文本中的全部 @角色提及，映射角色资产库定妆照/外观锁定卡。

    Args:
        text: 含 @角色名 的提示词文本（调用方需保证非空，API 层由 Pydantic 校验）。
        library: 角色资产库实例（测试可注入隔离实例；None 用全局单例）。

    Returns:
        MentionResolveData.model_dump()：mentions / unmatched / reference_images / expanded_text。

    Raises:
        ValueError: 提及数量超过 MAX_MENTIONS。
    """
    lib = library or character_library
    names = extract_mentions(text)
    if len(names) > MAX_MENTIONS:
        raise ValueError(f"@提及数量超限：{len(names)} > {MAX_MENTIONS}")

    assets = lib.list()
    mentions: list[dict[str, Any]] = []
    unmatched: list[str] = []
    reference_images: list[str] = []
    lock_clauses: list[str] = []

    for name in names:
        asset, match_type = _match_asset(name, assets)
        if asset is None:
            unmatched.append(name)
            mentions.append({"mention": name, "matched": False})
            continue
        reference_front = asset.reference_images.get("front", "")
        appearance_lock = lib.get_appearance_lock(asset.character_id)  # 仅锁定角色非空
        mentions.append({
            "mention": name,
            "matched": True,
            "match_type": match_type,
            "character_id": asset.character_id,
            "name": asset.name,
            "reference_front": reference_front,
            "appearance_lock": appearance_lock,
            "locked": asset.locked,
        })
        if reference_front and reference_front not in reference_images:
            reference_images.append(reference_front)
        if appearance_lock:
            lock_clauses.append(f"外观锁定（@{asset.name}）: {appearance_lock}")

    expanded_text = ("\n".join(lock_clauses) + "\n\n" + text) if lock_clauses else text
    data = MentionResolveData(
        text=text,
        mentions=mentions,
        unmatched=unmatched,
        reference_images=reference_images,
        expanded_text=expanded_text,
    )
    logger.info(
        "@提及解析: %d 提及 / %d 匹配 / %d 未匹配",
        len(names), len(names) - len(unmatched), len(unmatched),
    )
    return data.model_dump()


def auto_link_characters(text: str, library: CharacterLibrary | None = None) -> list[CharacterAsset]:
    """AutoLink 自动资产匹配（M25.2）：扫描文本中提及的资产库角色名。

    与 resolve_mentions 的显式 @语法 不同，本函数面向自然语言场景文本
    （scene.description/character_actions/dialogue）：角色名在文本中
    出现即命中（精确包含 → 大小写不敏感包含兜底）。

    防误挂策略：
    - 不做 fuzzy 匹配（自动挂接宁缺毋滥）
    - 同一起始位置命中多个名字时保留最长者（「林远」优先于「林」）

    Args:
        text: 场景自然语言文本。
        library: 角色资产库实例（测试可注入隔离实例；None 用全局单例）。

    Returns:
        命中的角色资产列表，按文中首次出现位置排序。
    """
    if not text or not text.strip():
        return []
    lib = library or character_library
    lowered = text.lower()
    hits: list[tuple[int, CharacterAsset]] = []
    for asset in lib.list():
        name = (asset.name or "").strip()
        if not name:
            continue
        idx = text.find(name)
        if idx < 0:
            idx = lowered.find(name.lower())
        if idx >= 0:
            hits.append((idx, asset))
    # 同起始位置去重：保留最长角色名
    best: dict[int, CharacterAsset] = {}
    for idx, asset in hits:
        cur = best.get(idx)
        if cur is None or len(asset.name) > len(cur.name):
            best[idx] = asset
    return [best[k] for k in sorted(best)]
