"""settings_service 单元测试（M27：NSFW 开关 + PIN 管理）。"""

from __future__ import annotations

import json

import pytest

from app.services.settings_service import (
    SettingsService,
    SettingsServiceError,
    _hash_pin,
)


@pytest.fixture()
def svc(tmp_path):
    return SettingsService(path=tmp_path / "app_settings.json")


class TestNsfwStatus:
    def test_default_status(self, svc):
        s = svc.nsfw_status()
        assert s == {"nsfw_enabled": False, "has_pin": False}

    def test_load_failure_returns_empty(self, svc):
        svc._path.write_text("{invalid json", encoding="utf-8")
        assert svc.nsfw_status()["nsfw_enabled"] is False

    def test_roundtrip(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        # 新实例读同一文件（验证落盘）
        svc2 = SettingsService(path=svc._path)
        s = svc2.nsfw_status()
        assert s == {"nsfw_enabled": True, "has_pin": True}


class TestSetNsfw:
    def test_first_enable_requires_new_pin(self, svc):
        with pytest.raises(SettingsServiceError, match="首次开启 NSFW 需设置管理 PIN"):
            svc.set_nsfw(True, pin="")

    def test_first_enable_sets_pin_and_enables(self, svc):
        s = svc.set_nsfw(True, pin="", new_pin="1234")
        assert s == {"nsfw_enabled": True, "has_pin": True}

    def test_disable_without_pin_set_raises(self, svc):
        with pytest.raises(SettingsServiceError, match="尚未设置"):
            svc.set_nsfw(False, pin="")

    def test_new_pin_must_be_digits(self, svc):
        with pytest.raises(SettingsServiceError, match="位数字"):
            svc.set_nsfw(True, pin="", new_pin="abcd")

    def test_new_pin_length_bounds(self, svc):
        with pytest.raises(SettingsServiceError, match="位数字"):
            svc.set_nsfw(True, pin="", new_pin="123")
        with pytest.raises(SettingsServiceError, match="位数字"):
            svc.set_nsfw(True, pin="", new_pin="123456789")

    def test_enable_with_correct_pin(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        svc.set_nsfw(False, pin="1234")
        assert svc.nsfw_status()["nsfw_enabled"] is False
        s = svc.set_nsfw(True, pin="1234")
        assert s["nsfw_enabled"] is True

    def test_enable_with_wrong_pin(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        with pytest.raises(SettingsServiceError, match="PIN 错误"):
            svc.set_nsfw(False, pin="9999")

    def test_enable_with_empty_pin(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        with pytest.raises(SettingsServiceError, match="请输入 PIN"):
            svc.set_nsfw(False, pin="")


class TestChangePin:
    def test_change_pin_success(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        svc.change_pin("1234", "5678")
        with pytest.raises(SettingsServiceError, match="PIN 错误"):
            svc.set_nsfw(False, pin="1234")
        svc.set_nsfw(False, pin="5678")
        assert svc.nsfw_status()["nsfw_enabled"] is False

    def test_change_pin_wrong_old_pin(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        with pytest.raises(SettingsServiceError, match="PIN 错误"):
            svc.change_pin("0000", "5678")

    def test_change_pin_no_pin_set(self, svc):
        with pytest.raises(SettingsServiceError, match="尚未设置"):
            svc.change_pin("1234", "5678")

    def test_change_pin_invalid_new_pin(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        with pytest.raises(SettingsServiceError, match="位数字"):
            svc.change_pin("1234", "12")


class TestInternals:
    def test_hash_pin_deterministic(self):
        assert _hash_pin("salt", "1234") == _hash_pin("salt", "1234")
        assert _hash_pin("salt", "1234") != _hash_pin("salt2", "1234")

    def test_save_is_atomic_and_valid_json(self, svc):
        svc.set_nsfw(True, pin="", new_pin="1234")
        data = json.loads(svc._path.read_text(encoding="utf-8"))
        assert data["nsfw_enabled"] is True
        assert "1234" not in data["pin_hash"]  # 明文不落盘
