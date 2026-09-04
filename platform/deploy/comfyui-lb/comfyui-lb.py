"""
ComfyUI 智能负载均衡代理

架构：
  所有客户端 → 本代理:8188 → N 个 ComfyUI 后端实例
  （当前 3 后端：本地 gpu0 :8196 + pc01 :8188 + pc02 :8193）

后端清单（SoT 热更新）：
  - 优先读 JSON 文件（env COMFY_LB_BACKENDS_FILE，默认 /opt/comfyui-lb/backends.json）；
    文件缺失/解析失败时回退到源码内置列表 BUILTIN_BACKENDS。
  - JSON 与内置列表同构：list of {"id","url","gpu","weight"?,"remote"?}。
  - 健康检查循环每轮核对文件 mtime，变化即热重载：新增后端自动进入健康探测，
    消失的后端从 health 表清理（prompt_map/file_map 不动），无需重启 LB。
  - GET /admin/backends：只读端点，返回当前后端清单及健康状态，供下游消费方拉取。

调度逻辑：
  - /prompt POST：查询所有实例的 /queue，分发到加权队列最短且健康的实例，记录 prompt_id→实例映射
  - /history/{id} / /view / /api/upload：按映射路由到正确实例
  - 其他请求：轮询分发
  - 健康检查：后台每 5s 探活，掉线实例自动剔除，恢复后自动加回
"""

import asyncio
import json
import os
import time
import logging
import re
from collections import defaultdict
from itertools import cycle
from typing import Optional

import aiohttp
from aiohttp import web, ClientTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("comfyui-lb")

# ── 配置 ──────────────────────────────────────────────
# 内置兜底清单（文件缺失/损坏时使用；正常态 SoT 是 backends.json，见上方 docstring）
BUILTIN_BACKENDS = [
    {"id": "gpu0", "url": "http://127.0.0.1:8196", "gpu": 0, "weight": 1.5},
    # pc02 远程节点(RTX 5090,经内网 IPv4 访问)
    {"id": "pc02", "url": "http://192.168.71.114:8193", "gpu": 0, "remote": True},
    # pc01 远程节点(RTX 5090,经内网 IPv4 访问)
    {"id": "pc01", "url": "http://192.168.71.116:8188", "gpu": 0, "remote": True},
]
BACKENDS_FILE = os.environ.get("COMFY_LB_BACKENDS_FILE", "/opt/comfyui-lb/backends.json")
LISTEN_PORT = 8188
HEALTH_CHECK_INTERVAL = 5  # 秒
HEALTH_TIMEOUT = 3        # 秒
UPSTREAM_TIMEOUT = ClientTimeout(total=300, sock_read=120)  # ComfyUI 生图可能很慢


def _validate_backends(data) -> list[dict]:
    """校验后端清单结构，非法抛 ValueError。返回规范化副本。"""
    if not isinstance(data, list) or not data:
        raise ValueError("backends 必须是非空 list")
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("id") or not item.get("url"):
            raise ValueError(f"backends[{i}] 缺少 id/url: {item!r}")
        out.append({
            "id": str(item["id"]),
            "url": str(item["url"]).rstrip("/"),
            "gpu": item.get("gpu", 0),
            **({"weight": float(item["weight"])} if "weight" in item else {}),
            **({"remote": bool(item["remote"])} if "remote" in item else {}),
        })
    ids = [b["id"] for b in out]
    if len(set(ids)) != len(ids):
        raise ValueError(f"backend id 重复: {ids}")
    return out


def load_backends(path: Optional[str] = None) -> tuple[list[dict], str]:
    """加载后端清单，返回 (backends, source)。

    优先读 JSON 文件；文件缺失/解析失败/校验失败时回退内置列表。
    source ∈ {"file", "builtin"}。
    """
    path = path or BACKENDS_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _validate_backends(data), "file"
    except FileNotFoundError:
        logger.info(f"backends 文件 {path} 不存在，使用内置清单")
    except Exception as e:
        logger.error(f"backends 文件 {path} 加载失败({e})，回退内置清单")
    return _validate_backends(BUILTIN_BACKENDS), "builtin"


# ── 状态 ──────────────────────────────────────────────
BACKENDS, _BACKENDS_SOURCE = load_backends()
backend_health: dict[str, bool] = {b["id"]: True for b in BACKENDS}  # id → healthy
prompt_map: dict[str, str] = {}  # prompt_id → backend_id（用于 history/view 路由）
file_map: dict[str, str] = {}    # filename → backend_id（从 history 响应学习，用于 /view 精确路由）
round_robin = cycle(b["id"] for b in BACKENDS)
map_lock = asyncio.Lock()
_backends_file_mtime: Optional[float] = None  # 上次热重载时看到的 backends 文件 mtime


def apply_backends(new: list[dict], source: str) -> None:
    """热替换后端清单（事件循环内调用，单线程无竞争）。

    新增后端默认 healthy=True 进入探测；消失的后端从 health 表清理；
    prompt_map/file_map 不动（历史作业的映射仍指向原 id，找不到实例时走盲试回退）。
    """
    global BACKENDS, round_robin
    old_ids = [b["id"] for b in BACKENDS]
    BACKENDS = new
    round_robin = cycle(b["id"] for b in BACKENDS)
    live = {b["id"] for b in new}
    for bid in list(backend_health):
        if bid not in live:
            del backend_health[bid]
    for b in new:
        backend_health.setdefault(b["id"], True)
    new_ids = [b["id"] for b in new]
    if new_ids != old_ids:
        logger.info(f"backends 热重载(source={source}): {old_ids} → {new_ids}")


def maybe_reload_backends() -> bool:
    """backends 文件 mtime 变化则热重载。返回是否执行了重载。失败回退内置并记日志。"""
    global _backends_file_mtime
    try:
        mtime: Optional[float] = os.path.getmtime(BACKENDS_FILE)
    except OSError:
        mtime = None
    if mtime == _backends_file_mtime:
        return False
    new, source = load_backends()
    _backends_file_mtime = mtime
    apply_backends(new, source)
    if source == "builtin" and mtime is not None:
        logger.error("backends 文件存在但不可用，已回退内置清单（修复文件后下轮自动重载）")
    return True


def _trim_map(m: dict, keep: int = 4000, cap: int = 5000) -> None:
    """map 超上限时裁到 keep 条，防内存泄漏（调用方需持锁）。"""
    if len(m) > cap:
        excess = len(m) - keep
        for key in list(m.keys())[:excess]:
            del m[key]


def _learn_file_mapping(data: dict, backend_id: str) -> int:
    """从 ComfyUI history 响应学习 filename → backend 映射。

    背景（M15.5）：/view 原先按 BACKENDS 顺序盲试，pc02 重启后 SaveImage 计数器
    归零生成的 character_xxx_00001_.png 与 gpu0 陈旧文件同名，gpu0 先命中返回
    错误参考图 → H3 ref2va 与漂移检测拿到跨风格图。history 是 /view 的必经前序
    （客户端先轮询 history 拿 filename 再请求 /view），在此学习映射可精确路由。
    """
    learned = 0
    try:
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            outputs = entry.get("outputs", {})
            if not isinstance(outputs, dict):
                continue
            for node_out in outputs.values():
                if not isinstance(node_out, dict):
                    continue
                for key in ("images", "gifs", "videos"):
                    for item in node_out.get(key) or []:
                        fname = item.get("filename") if isinstance(item, dict) else None
                        if fname:
                            file_map[fname] = backend_id
                            learned += 1
    except Exception:
        pass
    return learned

# 反向映射：某些客户端用 client_id 关联，也记录
client_backend: dict[str, str] = {}  # client_id → backend_id


def get_healthy_backends() -> list[dict]:
    return [b for b in BACKENDS if backend_health.get(b["id"], False)]


async def get_queue_length(session: aiohttp.ClientSession, backend_url: str) -> int:
    """查询实例队列长度（运行中 + 排队中）"""
    try:
        async with session.get(
            f"{backend_url}/queue",
            timeout=ClientTimeout(total=HEALTH_TIMEOUT),
        ) as resp:
            data = await resp.json()
            return len(data.get("queue_running", [])) + len(data.get("queue_pending", []))
    except Exception:
        return 999  # 不可达返回大值，不会被选中


async def select_backend_for_prompt(session: aiohttp.ClientSession) -> Optional[dict]:
    """为新的 /prompt 请求选择队列最短的健康实例"""
    healthy = get_healthy_backends()
    if not healthy:
        return None

    # 并发查询所有健康实例的队列长度
    tasks = [get_queue_length(session, b["url"]) for b in healthy]
    queue_lengths = await asyncio.gather(*tasks)

    # 选加权队列最短的(weight>1 降权,反映共卡显存压力而非纯队列)
    best_idx = 0
    best_score = queue_lengths[0] * healthy[0].get("weight", 1.0)
    for i, ql in enumerate(queue_lengths[1:], 1):
        score = ql * healthy[i].get("weight", 1.0)
        if score < best_score:
            best_score = score
            best_idx = i

    return healthy[best_idx]


def select_backend_round_robin() -> Optional[dict]:
    """轮询选择健康实例（用于无状态请求）"""
    healthy_ids = {b["id"] for b in get_healthy_backends()}
    if not healthy_ids:
        return None
    for _ in range(len(BACKENDS)):
        bid = next(round_robin)
        if bid in healthy_ids:
            return next(b for b in BACKENDS if b["id"] == bid)
    return None


def find_backend_for_id(resource_id: str) -> Optional[dict]:
    """根据 prompt_id / filename 查找对应实例"""
    # 直接映射
    if resource_id in prompt_map:
        bid = prompt_map[resource_id]
        return next((b for b in BACKENDS if b["id"] == bid), None)
    # 没找到，可能 history 已经被清理，尝试所有健康实例
    return None


# ── 健康检查 ──────────────────────────────────────────
async def health_check_loop(app: web.Application):
    session = app["session"]
    while True:
        maybe_reload_backends()  # backends 文件热更新（mtime 变化才重载）
        for backend in list(BACKENDS):
            try:
                async with session.get(
                    f"{backend['url']}/system_stats",
                    timeout=ClientTimeout(total=HEALTH_TIMEOUT),
                ) as resp:
                    healthy = resp.status == 200
            except Exception:
                healthy = False

            old = backend_health.get(backend["id"])
            if old != healthy:
                logger.info(f"Backend {backend['id']} ({backend['url']}) → {'HEALTHY' if healthy else 'UNHEALTHY'}")
            backend_health[backend["id"]] = healthy

        await asyncio.sleep(HEALTH_CHECK_INTERVAL)


# ── 代理处理 ──────────────────────────────────────────
async def handle_prompt(request: web.Request) -> web.Response:
    """POST /prompt — 智能分发到队列最短的实例"""
    session = request.app["session"]
    body = await request.read()

    # 解析 prompt_id 和 client_id
    try:
        payload = json.loads(body) if body else {}
        client_id = payload.get("client_id", "")
    except Exception:
        client_id = ""

    backend = await select_backend_for_prompt(session)
    if not backend:
        return web.json_response({"error": "No healthy backends"}, status=503)

    # 转发请求
    try:
        async with session.post(
            f"{backend['url']}/prompt",
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=UPSTREAM_TIMEOUT,
        ) as resp:
            resp_body = await resp.read()
            # 记录 prompt_id → backend 映射
            if resp.status == 200:
                try:
                    result = json.loads(resp_body)
                    prompt_id = result.get("prompt_id", "")
                    if prompt_id:
                        async with map_lock:
                            prompt_map[prompt_id] = backend["id"]
                            _trim_map(prompt_map)
                        logger.info(f"prompt {prompt_id[:8]}... → {backend['id']}")
                except Exception:
                    pass
            return web.Response(
                body=resp_body,
                status=resp.status,
                content_type=resp.content_type,
            )
    except asyncio.TimeoutError:
        return web.json_response({"error": "Upstream timeout"}, status=504)
    except Exception as e:
        logger.error(f"Proxy /prompt to {backend['id']} failed: {e}")
        return web.json_response({"error": str(e)}, status=502)


async def handle_history(request: web.Request) -> web.Response:
    """GET /history/{prompt_id} — 路由到对应实例"""
    session = request.app["session"]
    prompt_id = request.match_info.get("prompt_id", "")

    backend = find_backend_for_id(prompt_id)

    # 如果没找到映射，尝试所有健康实例
    if not backend:
        for b in get_healthy_backends():
            try:
                async with session.get(
                    f"{b['url']}/history/{prompt_id}",
                    timeout=ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data:  # 非空说明找到了
                            async with map_lock:
                                prompt_map[prompt_id] = b["id"]
                                learned = _learn_file_mapping(data, b["id"])
                                _trim_map(prompt_map)
                                _trim_map(file_map)
                            if learned:
                                logger.info(f"learned {learned} file mappings from history {prompt_id[:8]}... → {b['id']}")
                            return web.json_response(data)
            except Exception:
                continue
        return web.json_response({})

    try:
        async with session.get(
            f"{backend['url']}/history/{prompt_id}",
            timeout=ClientTimeout(total=10),
        ) as resp:
            data = await resp.text()
            # 已完成的历史包含 outputs → 学习 filename→backend 映射供 /view 精确路由
            if resp.status == 200:
                try:
                    parsed = json.loads(data)
                    if parsed:
                        async with map_lock:
                            learned = _learn_file_mapping(parsed, backend["id"])
                            _trim_map(file_map)
                        if learned:
                            logger.info(f"learned {learned} file mappings from history {prompt_id[:8]}... → {backend['id']}")
                except Exception:
                    pass
            return web.Response(text=data, status=resp.status, content_type=resp.content_type)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


async def handle_view(request: web.Request) -> web.Response:
    """GET /view — 优先按 filename→backend 映射精确路由，未命中才按序盲试。

    M15.5 加固：盲试会被他端同名陈旧文件截胡（返回错误参考图），
    映射命中时只问生成该文件的后端；映射未命中/后端异常时回退原盲试逻辑。
    """
    session = request.app["session"]
    query = request.query_string
    path = f"/view?{query}" if query else "/view"

    # 精确路由：filename 有学习到的映射 → 只问该后端
    filename = request.query.get("filename", "")
    mapped_bid = file_map.get(filename)
    if filename and mapped_bid:
        backend = next(
            (b for b in BACKENDS if b["id"] == mapped_bid and backend_health.get(b["id"])),
            None,
        )
        if backend:
            try:
                async with session.get(
                    f"{backend['url']}{path}",
                    timeout=ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.read()
                        return web.Response(body=body, status=200, content_type=resp.content_type)
                    logger.warning(
                        f"/view mapped backend {backend['id']} returned {resp.status} for {filename}, falling back"
                    )
            except Exception as e:
                logger.warning(f"/view mapped backend {backend['id']} failed for {filename}: {e}, falling back")

    # 回退：尝试所有健康实例
    for backend in get_healthy_backends():
        try:
            async with session.get(
                f"{backend['url']}{path}",
                timeout=ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    body = await resp.read()
                    return web.Response(
                        body=body,
                        status=200,
                        content_type=resp.content_type,
                    )
        except Exception:
            continue
    return web.Response(status=404, text="File not found on any backend")


async def handle_upload(request: web.Request) -> web.Response:
    """POST /upload/image — 上传到轮询选择的实例"""
    session = request.app["session"]
    backend = select_backend_round_robin()
    if not backend:
        return web.json_response({"error": "No healthy backends"}, status=503)

    body = await request.read()
    content_type = request.headers.get("Content-Type", "multipart/form-data")

    try:
        async with session.post(
            f"{backend['url']}/upload/image",
            data=body,
            headers={"Content-Type": content_type},
            timeout=UPSTREAM_TIMEOUT,
        ) as resp:
            resp_body = await resp.read()
            return web.Response(
                body=resp_body,
                status=resp.status,
                content_type=resp.content_type,
            )
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


async def handle_history_list(request: web.Request) -> web.Response:
    """GET /history — 合并所有实例的 history（可选，可能很大）"""
    session = request.app["session"]
    merged = {}
    for backend in get_healthy_backends():
        try:
            async with session.get(
                f"{backend['url']}/history",
                timeout=ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    merged.update(data)
        except Exception:
            continue
    return web.json_response(merged)


async def handle_queue(request: web.Request) -> web.Response:
    """GET /queue — 合并所有实例的队列状态"""
    session = request.app["session"]
    merged = {"queue_running": [], "queue_pending": []}
    for backend in get_healthy_backends():
        try:
            async with session.get(
                f"{backend['url']}/queue",
                timeout=ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("queue_running", []):
                        item_with_backend = list(item) + [backend["id"]] if isinstance(item, list) else item
                        merged["queue_running"].append(item)
                    merged["queue_pending"].extend(data.get("queue_pending", []))
        except Exception:
            continue
    return web.json_response(merged)


async def handle_system_stats(request: web.Request) -> web.Response:
    """GET /system_stats — 合并所有实例的系统状态"""
    session = request.app["session"]
    stats = {"backends": [], "total_gpu_memory": 0}
    for backend in get_healthy_backends():
        try:
            async with session.get(
                f"{backend['url']}/system_stats",
                timeout=ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    data["backend_id"] = backend["id"]
                    data["gpu"] = backend["gpu"]
                    stats["backends"].append(data)
                    # 累加显存
                    sys_info = data.get("system", {})
                    for dev in sys_info.get("devices", []):
                        stats["total_gpu_memory"] += dev.get("vram_total", 0)
        except Exception:
            continue
    stats["healthy_count"] = len(stats["backends"])
    stats["total_count"] = len(BACKENDS)
    return web.json_response(stats)


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """WS /ws — ComfyUI 的 WebSocket，连接到随机健康实例"""
    session = request.app["session"]
    backend = select_backend_round_robin()
    if not backend:
        return web.Response(status=503, text="No healthy backends")

    ws_client = web.WebSocketResponse()
    await ws_client.prepare(request)

    try:
        async with session.ws_connect(f"{backend['url']}/ws") as ws_upstream:
            async def client_to_upstream():
                async for msg in ws_client:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await ws_upstream.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws_upstream.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.CLOSE:
                        await ws_upstream.close()
                        break

            async def upstream_to_client():
                async for msg in ws_upstream:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.CLOSE:
                        await ws_client.close()
                        break

            await asyncio.gather(
                client_to_upstream(),
                upstream_to_client(),
                return_exceptions=True,
            )
    except Exception as e:
        logger.error(f"WS proxy error: {e}")

    return ws_client


# 转发给后端时剥离浏览器安全元数据头：ComfyUI 的 origin_only_middleware
# 对 Sec-Fetch-Site: cross-site 一律 403，且在 loopback Host 下校验 Origin!=Host 也 403。
# 浏览器跨站 iframe(如 ToIV localhost:3100 嵌 :8188)会带这些头，导致 / 和 /assets/* 全 403。
# LB 是内网可信边界，剥离后后端视为服务器间调用。
_PROXY_DROP_HEADERS = frozenset({
    "host", "origin",
    "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user",
})


def _proxy_headers(request):
    return {k: v for k, v in request.headers.items() if k.lower() not in _PROXY_DROP_HEADERS}


def is_static_file(path: str) -> bool:
    """判断是否是静态文件请求"""
    return path.lower().endswith((
        ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
        ".woff", ".woff2", ".ttf", ".map", ".json", ".webp", ".mp4", ".webm",
    ))
def get_primary_backend() -> Optional[dict]:
    """获取主后端（第一个健康的本地实例，确保版本一致）"""
    for b in get_healthy_backends():
        if not b.get("remote"):
            return b
    # 没有本地实例，用任意健康实例
    return get_healthy_backends()[0] if get_healthy_backends() else None


async def handle_admin_backends(request: web.Request) -> web.Response:
    """GET /admin/backends — 只读后端清单+健康状态，供下游消费方拉取（上传扇出等）"""
    return web.json_response({
        "backends": [
            {
                "id": b["id"],
                "url": b["url"],
                "gpu": b.get("gpu", 0),
                "weight": b.get("weight", 1.0),
                "remote": b.get("remote", False),
                "healthy": backend_health.get(b["id"], False),
            }
            for b in BACKENDS
        ],
    })


async def handle_generic_proxy(request: web.Request) -> web.Response:
    """其他请求 — 非 API 请求走主后端，API 请求轮询分发"""
    session = request.app["session"]
    path = request.path

    # 所有非 API 请求（HTML 页面、静态文件等）始终走主后端，确保版本一致
    # 只有明确的 API 请求才轮询分发
    api_prefixes = ("/api/", "/prompt", "/history", "/view", "/upload", "/queue", "/system_stats", "/ws", "/object_info")
    if not path.startswith(api_prefixes):
        backend = get_primary_backend()
    else:
        backend = select_backend_round_robin()

    if not backend:
        return web.json_response({"error": "No healthy backends"}, status=503)

    method = request.method
    path = request.path_qs
    body = await request.read() if method in ("POST", "PUT", "PATCH") else None

    try:
        async with session.request(
            method,
            f"{backend['url']}{path}",
            data=body,
            headers=_proxy_headers(request),
            timeout=UPSTREAM_TIMEOUT,
        ) as resp:
            resp_body = await resp.read()
            return web.Response(
                body=resp_body,
                status=resp.status,
                content_type=resp.content_type,
            )
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


# ── 应用 ──────────────────────────────────────────────
async def on_startup(app: web.Application):
    app["session"] = aiohttp.ClientSession()
    app["health_task"] = asyncio.create_task(health_check_loop(app))
    logger.info(
        f"ComfyUI LB started on :{LISTEN_PORT}, backends: {[b['id'] for b in BACKENDS]}"
        f" (source={_BACKENDS_SOURCE}, file={BACKENDS_FILE})"
    )


async def on_cleanup(app: web.Application):
    app["health_task"].cancel()
    await app["session"].close()
    logger.info("ComfyUI LB stopped")


def create_app() -> web.Application:
    app = web.Application(client_max_size=100 * 1024 * 1024)  # 100MB 上传限制
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    # 特殊路由
    app.router.add_post("/prompt", handle_prompt)
    app.router.add_get("/history", handle_history_list)
    app.router.add_get("/history/{prompt_id}", handle_history)
    app.router.add_get("/view", handle_view)
    app.router.add_post("/upload/image", handle_upload)
    app.router.add_post("/upload/mask", handle_upload)
    app.router.add_get("/queue", handle_queue)
    app.router.add_get("/system_stats", handle_system_stats)
    app.router.add_get("/admin/backends", handle_admin_backends)
    app.router.add_get("/ws", handle_websocket)

    # 其他所有请求走通用代理
    app.router.add_route("*", "/{tail:.*}", handle_generic_proxy)

    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=LISTEN_PORT, access_log=None)
