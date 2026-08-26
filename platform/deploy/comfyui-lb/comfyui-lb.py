"""
ComfyUI 智能负载均衡代理

架构：
  所有客户端 → 本代理:8188 → 4 个 ComfyUI 实例 (8189-8192, GPU0-3)

调度逻辑：
  - /prompt POST：查询所有实例的 /queue，分发到队列最短且健康的实例，记录 prompt_id→实例映射
  - /history/{id} / /view / /api/upload：按映射路由到正确实例
  - 其他请求：轮询分发
  - 健康检查：后台每 5s 探活，掉线实例自动剔除，恢复后自动加回
"""

import asyncio
import json
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
BACKENDS = [
    {"id": "gpu0", "url": "http://127.0.0.1:8189", "gpu": 0},
    # pc02 远程节点(RTX 5090,经内网 IPv4 访问)
    {"id": "pc02", "url": "http://192.168.71.114:8193", "gpu": 0, "remote": True},
    # pc01 远程节点(RTX 5090,经内网 IPv4 访问)
    {"id": "pc01", "url": "http://192.168.71.115:8188", "gpu": 0, "remote": True},
]
LISTEN_PORT = 8188
HEALTH_CHECK_INTERVAL = 5  # 秒
HEALTH_TIMEOUT = 3        # 秒
UPSTREAM_TIMEOUT = ClientTimeout(total=300, sock_read=120)  # ComfyUI 生图可能很慢

# ── 状态 ──────────────────────────────────────────────
backend_health: dict[str, bool] = {b["id"]: True for b in BACKENDS}  # id → healthy
prompt_map: dict[str, str] = {}  # prompt_id → backend_id（用于 history/view 路由）
file_map: dict[str, str] = {}    # filename → backend_id（从 history 响应学习，用于 /view 精确路由）
round_robin = cycle(b["id"] for b in BACKENDS)
map_lock = asyncio.Lock()


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

    # 选队列最短的
    best_idx = 0
    best_len = queue_lengths[0]
    for i, ql in enumerate(queue_lengths[1:], 1):
        if ql < best_len:
            best_len = ql
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
        for backend in BACKENDS:
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
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
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
    logger.info(f"ComfyUI LB started on :{LISTEN_PORT}, backends: {[b['id'] for b in BACKENDS]}")


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
    app.router.add_get("/ws", handle_websocket)

    # 其他所有请求走通用代理
    app.router.add_route("*", "/{tail:.*}", handle_generic_proxy)

    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=LISTEN_PORT, access_log=None)
