"""comfyui-lb 后端热更新与 /admin/backends 端点测试。

LB 是独立 aiohttp 脚本（文件名含连字符，不可正常 import），用 importlib 按路径加载。
测试全部为同步函数（aiohttp 端点测试内部 asyncio.run），不依赖 pytest-asyncio 配置。
运行：platform/backend/.venv/bin/python -m pytest platform/deploy/comfyui-lb/test_comfyui_lb.py
"""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

LB_PATH = Path(__file__).parent / "comfyui-lb.py"


def _load_lb():
    spec = importlib.util.spec_from_file_location("comfyui_lb", LB_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["comfyui_lb"] = mod
    spec.loader.exec_module(mod)
    return mod


lb = _load_lb()


@pytest.fixture()
def lb_state(monkeypatch, tmp_path):
    """隔离 LB 模块全局状态：backends 文件指向 tmp，结束恢复原始清单。"""
    backends_file = tmp_path / "backends.json"
    monkeypatch.setattr(lb, "BACKENDS_FILE", str(backends_file))
    monkeypatch.setattr(lb, "_backends_file_mtime", None)
    yield backends_file
    lb.apply_backends(lb._validate_backends(lb.BUILTIN_BACKENDS), "builtin")


# ── load_backends：文件优先 / 兜底 / 坏 JSON 回退 ──────────────────────────

def test_load_backends_file_priority(tmp_path):
    p = tmp_path / "backends.json"
    p.write_text(json.dumps([
        {"id": "gpu0", "url": "http://127.0.0.1:8196/", "gpu": 0, "weight": 1.5},
        {"id": "pc02", "url": "http://192.168.71.114:8193", "gpu": 0, "remote": True},
    ]), encoding="utf-8")
    backends, source = lb.load_backends(str(p))
    assert source == "file"
    assert [b["id"] for b in backends] == ["gpu0", "pc02"]
    assert backends[0]["url"] == "http://127.0.0.1:8196"  # 尾斜杠规范化
    assert backends[0]["weight"] == 1.5
    assert backends[1]["remote"] is True


def test_load_backends_missing_file_falls_back_to_builtin(tmp_path):
    backends, source = lb.load_backends(str(tmp_path / "nope.json"))
    assert source == "builtin"
    assert [b["id"] for b in backends] == ["gpu0", "pc02", "pc01"]
    gpu0 = backends[0]
    assert gpu0["url"] == "http://127.0.0.1:8196"
    assert gpu0["weight"] == 1.5


def test_load_backends_bad_json_falls_back(tmp_path):
    p = tmp_path / "backends.json"
    p.write_text("{not json", encoding="utf-8")
    backends, source = lb.load_backends(str(p))
    assert source == "builtin"
    assert [b["id"] for b in backends] == ["gpu0", "pc02", "pc01"]


def test_load_backends_invalid_structure_falls_back(tmp_path):
    p = tmp_path / "backends.json"
    p.write_text(json.dumps([{"id": "gpu0"}]), encoding="utf-8")  # 缺 url
    backends, source = lb.load_backends(str(p))
    assert source == "builtin"


# ── mtime 热重载 ─────────────────────────────────────────────────────────

def _write(path: Path, backends, mtime: float):
    path.write_text(json.dumps(backends), encoding="utf-8")
    os.utime(path, (mtime, mtime))  # 显式钉 mtime，规避文件系统精度问题


def test_hot_reload_on_mtime_change(lb_state):
    backends_file = lb_state
    _write(backends_file, [{"id": "gpu0", "url": "http://127.0.0.1:8196", "gpu": 0}], 1000.0)

    assert lb.maybe_reload_backends() is True
    assert [b["id"] for b in lb.BACKENDS] == ["gpu0"]
    assert set(lb.backend_health) == {"gpu0"}

    # mtime 不变 → 不重载
    assert lb.maybe_reload_backends() is False

    # mtime 变化：pc02 加入、gpu0 移除 → health 表同步增删
    _write(backends_file, [{"id": "pc02", "url": "http://192.168.71.114:8193", "gpu": 0}], 2000.0)
    assert lb.maybe_reload_backends() is True
    assert [b["id"] for b in lb.BACKENDS] == ["pc02"]
    assert "gpu0" not in lb.backend_health
    assert lb.backend_health["pc02"] is True  # 新后端默认健康，进入探测


def test_hot_reload_bad_file_keeps_running_on_builtin(lb_state):
    backends_file = lb_state
    backends_file.write_text("{broken", encoding="utf-8")
    os.utime(backends_file, (1000.0, 1000.0))
    assert lb.maybe_reload_backends() is True
    assert [b["id"] for b in lb.BACKENDS] == ["gpu0", "pc02", "pc01"]  # 回退内置不崩


def test_prompt_file_maps_untouched_by_reload(lb_state):
    lb.prompt_map["pid-1"] = "gpu0"
    lb.file_map["a.png"] = "gpu0"
    _write(lb_state, [{"id": "pc02", "url": "http://192.168.71.114:8193", "gpu": 0}], 1000.0)
    lb.maybe_reload_backends()
    assert lb.prompt_map["pid-1"] == "gpu0"
    assert lb.file_map["a.png"] == "gpu0"


# ── GET /admin/backends ─────────────────────────────────────────────────

def test_admin_backends_endpoint():
    from aiohttp import test_utils

    async def _run():
        app = lb.create_app()
        app.on_startup.clear()  # 不起健康检查协程，避免触网
        app.on_cleanup.clear()
        async with test_utils.TestClient(test_utils.TestServer(app)) as client:
            resp = await client.get("/admin/backends")
            assert resp.status == 200
            return await resp.json()

    data = asyncio.run(_run())
    backends = data["backends"]
    assert {b["id"] for b in backends} == {"gpu0", "pc02", "pc01"}
    gpu0 = next(b for b in backends if b["id"] == "gpu0")
    assert gpu0["url"] == "http://127.0.0.1:8196"
    assert gpu0["weight"] == 1.5
    assert gpu0["remote"] is False
    assert gpu0["healthy"] is True
    pc01 = next(b for b in backends if b["id"] == "pc01")
    assert pc01["url"] == "http://192.168.71.116:8188"
    assert pc01["remote"] is True
