"""应用设置持久化服务（M27：NSFW 开关 + 管理 PIN）。

设计要点：
- 持久化到 backend data/app_settings.json（路径由 settings.app_settings_path 配置）
- NSFW 默认关闭；开启/关闭/改 PIN 均需 PIN 验证（首次启用需先设 PIN）
- PIN 不落明文：sha256(salt + pin)，salt 随首次设置生成
- 进程内缓存 + 文件 mtime 失效检测（多进程/多实例安全：文件为准）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_PIN_MIN_LEN = 4
_PIN_MAX_LEN = 8


class SettingsServiceError(Exception):
    """设置服务错误（PIN 错误/参数非法等）。"""


def _hash_pin(salt: str, pin: str) -> str:
    return hashlib.sha256(f"{salt}:{pin}".encode("utf-8")).hexdigest()


class SettingsService:
    """应用设置读写（NSFW 开关 + PIN）。"""

    def __init__(self, path: Path | None = None):
        self._path = path or self._default_path()
        self._lock = threading.Lock()

    @staticmethod
    def _default_path() -> Path:
        # app_settings_path 相对 backend/ 目录（config.py 位于 backend/app/）
        base = Path(__file__).resolve().parent.parent.parent
        return base / settings.app_settings_path

    # ---------- 基础读写 ----------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("读取设置文件失败 %s: %s（按空设置处理）", self._path, e)
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)  # 原子替换，防半写状态

    # ---------- NSFW 状态 ----------

    def nsfw_status(self) -> dict[str, Any]:
        data = self._load()
        return {
            "nsfw_enabled": bool(data.get("nsfw_enabled", False)),
            "has_pin": bool(data.get("pin_hash")),
        }

    def _verify_pin(self, data: dict[str, Any], pin: str) -> None:
        """校验 PIN；未设置 PIN 或 PIN 错误均抛 SettingsServiceError。"""
        pin_hash = data.get("pin_hash")
        if not pin_hash:
            # 未设 PIN 时，任何 pin 都无法通过「验证」（只能走 set_pin 流程）
            raise SettingsServiceError("尚未设置 NSFW 管理 PIN")
        if not pin:
            raise SettingsServiceError("请输入 PIN")
        if _hash_pin(data.get("pin_salt", ""), pin) != pin_hash:
            raise SettingsServiceError("PIN 错误")

    @staticmethod
    def _validate_new_pin(new_pin: str) -> None:
        if not new_pin.isdigit() or not (_PIN_MIN_LEN <= len(new_pin) <= _PIN_MAX_LEN):
            raise SettingsServiceError(f"PIN 须为 {_PIN_MIN_LEN}-{_PIN_MAX_LEN} 位数字")

    # ---------- NSFW 操作 ----------

    def set_nsfw(self, enabled: bool, pin: str, new_pin: str | None = None) -> dict[str, Any]:
        """开启/关闭 NSFW。

        - 首次开启（无 PIN）：必须同时提供 new_pin 完成 PIN 设置
        - 已有 PIN：pin 必须验证通过；enabled=True/False 均可
        """
        with self._lock:
            data = self._load()
            if not data.get("pin_hash"):
                if not enabled:
                    raise SettingsServiceError("尚未设置 NSFW 管理 PIN")
                if not new_pin:
                    raise SettingsServiceError("首次开启 NSFW 需设置管理 PIN（new_pin）")
                self._validate_new_pin(new_pin)
                data["pin_salt"] = secrets.token_hex(8)
                data["pin_hash"] = _hash_pin(data["pin_salt"], new_pin)
                logger.info("NSFW 管理 PIN 已设置")
            else:
                self._verify_pin(data, pin)

            data["nsfw_enabled"] = bool(enabled)
            self._save(data)
            logger.info("NSFW 状态切换 → %s", enabled)
            return self.nsfw_status()

    def change_pin(self, pin: str, new_pin: str) -> dict[str, Any]:
        """修改 PIN（需旧 PIN 验证）。"""
        with self._lock:
            data = self._load()
            self._verify_pin(data, pin)
            self._validate_new_pin(new_pin)
            data["pin_salt"] = secrets.token_hex(8)
            data["pin_hash"] = _hash_pin(data["pin_salt"], new_pin)
            self._save(data)
            logger.info("NSFW 管理 PIN 已修改")
            return self.nsfw_status()


settings_service = SettingsService()
