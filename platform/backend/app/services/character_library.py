"""角色资产库 — 本地 JSON 持久化存储角色外观锁定卡。

解决短剧跨集/跨镜角色一致性崩坏问题：
- 角色定妆照生成后自动登记（外观描述 + 三视图参考图 + 定妆 prompt）
- 分镜/视频生成时强制注入「外观锁定卡」，确保同一角色外观关键词逐字一致
- 支持手动锁定/解锁、外观锁定描述编辑（用户可控自由度）

存储位置：output/character/library/{character_id}.json
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.schemas import Character, CharacterAsset

logger = logging.getLogger(__name__)

LIBRARY_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "character" / "library"

# 外观锁定卡注入分镜 prompt 时的最大长度（避免 prompt 过载）
APPEARANCE_LOCK_MAX_CHARS = 400


class CharacterLibrary:
    """角色资产库：JSON 文件持久化 + 内存缓存。"""

    def __init__(self, library_dir: Path | None = None):
        self._dir = library_dir or LIBRARY_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, CharacterAsset] | None = None

    # ------------------------------------------------------------------
    # 基础 IO
    # ------------------------------------------------------------------

    def _path(self, character_id: str) -> Path:
        safe = "".join(c for c in character_id if c.isalnum() or c in "-_")
        if not safe or safe != character_id:
            raise ValueError(f"非法 character_id: {character_id!r}")
        return self._dir / f"{safe}.json"

    def _load_all(self) -> dict[str, CharacterAsset]:
        if self._cache is not None:
            return self._cache
        assets: dict[str, CharacterAsset] = {}
        for fp in self._dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                asset = CharacterAsset(**data)
                assets[asset.character_id] = asset
            except Exception as e:
                logger.warning("角色资产文件损坏，跳过 %s: %s", fp.name, e)
        self._cache = assets
        return assets

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, asset: CharacterAsset) -> CharacterAsset:
        asset.updated_at = int(time.time())
        # M18.7 资产血缘：同步写入 ISO 8601 人类可读时间戳（updated_at 仍为 epoch 秒，供排序兼容）
        asset.updated_at_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        if not asset.created_at:
            asset.created_at = asset.updated_at
        with self._lock:
            self._path(asset.character_id).write_text(
                json.dumps(asset.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if self._cache is not None:
                self._cache[asset.character_id] = asset
        logger.info("角色资产已登记: %s (%s) locked=%s", asset.character_id, asset.name, asset.locked)
        return asset

    def get(self, character_id: str) -> CharacterAsset | None:
        return self._load_all().get(character_id)

    def list(self) -> list[CharacterAsset]:
        return sorted(self._load_all().values(), key=lambda a: a.updated_at, reverse=True)

    def delete(self, character_id: str) -> bool:
        with self._lock:
            fp = self._path(character_id)
            existed = fp.exists()
            if existed:
                fp.unlink()
            if self._cache is not None:
                self._cache.pop(character_id, None)
        return existed

    def update(self, character_id: str, **fields: Any) -> CharacterAsset | None:
        """局部更新（仅允许白名单字段）。"""
        asset = self.get(character_id)
        if asset is None:
            return None
        allowed = {
            "name", "role", "age", "description", "personality", "appearance_lock",
            "locked", "consistency_level", "face_still", "voice_sample",
        }
        data = asset.model_dump()
        for k, v in fields.items():
            if k in allowed and v is not None:
                data[k] = v
        return self.save(CharacterAsset(**data))

    # ------------------------------------------------------------------
    # 业务方法
    # ------------------------------------------------------------------

    def register_from_card(
        self,
        character: Character,
        reference_images: dict[str, str],
        used_prompts: dict[str, str],
        consistency_level: str = "L3",
        source_script_id: str = "",
    ) -> CharacterAsset:
        """角色定妆照生成后自动登记资产库（默认锁定，强制跨集引用）。

        外观锁定卡默认取定妆正面 prompt（含精确外观关键词），
        已存在资产时保留用户手动编辑过的 appearance_lock。

        M18.7 血缘：source_script_id 传入剧本 project_id；空串表示旧资产兼容
        或画布单角色重生成（不覆盖既有血缘）。
        """
        existing = self.get(character.character_id)
        appearance_lock = (
            existing.appearance_lock
            if existing and existing.appearance_lock
            else used_prompts.get("positive_prompt", "")[:APPEARANCE_LOCK_MAX_CHARS]
        )
        # 血缘标记：空串且旧资产已有血缘 → 保留旧血缘（画布单角色重生成不丢上下文）
        lineage_id = source_script_id or (existing.source_script_id if existing else "")
        asset = CharacterAsset(
            character_id=character.character_id,
            name=character.name,
            role=character.role,
            age=character.age,
            description=character.description,
            personality=character.personality,
            reference_images=reference_images,
            used_prompts=used_prompts,
            appearance_lock=appearance_lock,
            locked=existing.locked if existing else True,
            consistency_level=consistency_level,
            source_script_id=lineage_id,
            created_at=existing.created_at if existing else 0,
            # P2: 重登记定妆照时保留已有面部静帧/音色，避免圣经被冲掉
            face_still=existing.face_still if existing else "",
            voice_sample=existing.voice_sample if existing else "",
        )
        return self.save(asset)

    def get_appearance_lock(self, character_id: str) -> str:
        """获取角色的外观锁定描述（仅锁定状态下返回，供分镜注入）。"""
        asset = self.get(character_id)
        if asset is None or not asset.locked:
            return ""
        return asset.appearance_lock[:APPEARANCE_LOCK_MAX_CHARS]

    def resolve_characters(self, characters: list[Character]) -> list[dict[str, str]]:
        """分镜/视频生成前解析角色外观：返回带外观锁定卡的角色信息。

        锁定角色以资产库的外观描述为准（用户可能在资产库中精修过），
        未登记角色回退到请求中的 description。
        """
        resolved: list[dict[str, str]] = []
        for c in characters:
            asset = self.get(c.character_id)
            lock = self.get_appearance_lock(c.character_id)
            resolved.append({
                "character_id": c.character_id,
                "name": c.name,
                "role": c.role,
                # 锁定角色优先使用资产库描述（用户精修版），否则用请求描述
                "description": (asset.description if asset and asset.locked and asset.description else c.description),
                "appearance_lock": lock,
                "reference_front": (asset.reference_images.get("front", "") if asset else ""),
            })
        return resolved


# 全局单例
character_library = CharacterLibrary()
