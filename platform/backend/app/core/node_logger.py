"""核心链路节点日志埋点（DramaClaw 重构 T3）。

统一结构化节点日志，覆盖：时间戳（logging 默认前缀）、节点标识、关键参数值、
处理状态（start/ok/error）、异常信息。支撑连接超时、数据异常等问题的排查分析。

输出示例：
    node=pipeline.script status=start task_id=abc premise=末日便利店...
    node=llm.chat status=ok elapsed_ms=1234.5 model=qwen3.6-uncensored
    node=gateway.route status=ok capability=asr endpoint=http://192.168.71.127:9210
    node=comfyui.submit status=error worker_url=http://192.168.71.127:8188 error=...

使用方式：
    from app.core.node_logger import node_log, node_span

    # 单点埋点
    node_log("gateway.route", "ok", capability="asr", endpoint=ep)

    # 区间埋点（自动计时 + 异常捕获）
    async with node_span("pipeline.script", task_id=task_id, premise=p[:50]):
        script = await self._step_script(...)
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

# 独立命名 logger：继承 root 配置（main.py 统一初始化），可按需单独调级别
logger = logging.getLogger("aicg.node")

# 单参数值最大长度（防止长提示词/长 JSON 刷爆日志）
_MAX_VALUE_LEN = 200


def node_log(
    node: str,
    status: str,
    *,
    task_id: str = "",
    elapsed_ms: float | None = None,
    error: str = "",
    **params: Any,
) -> None:
    """输出一条结构化节点日志。

    Args:
        node: 节点标识（如 pipeline.script / llm.chat / gateway.route / comfyui.submit）
        status: 处理状态（start / ok / error / skip）
        task_id: 关联任务 ID（全链路任务/分镜/视频等）
        elapsed_ms: 节点耗时（毫秒）；None 表示非计时点
        error: 异常信息（status=error 时必填）
        **params: 关键参数值（key=value 形式输出，超长截断 200 字符）
    """
    parts = [f"node={node}", f"status={status}"]
    if task_id:
        parts.append(f"task_id={task_id}")
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={elapsed_ms:.1f}")
    for key, value in params.items():
        text = str(value).replace("\n", " ").replace("\r", " ")
        if len(text) > _MAX_VALUE_LEN:
            text = text[:_MAX_VALUE_LEN] + "…"
        parts.append(f"{key}={text}")
    if error:
        text = error.replace("\n", " ")
        if len(text) > _MAX_VALUE_LEN:
            text = text[:_MAX_VALUE_LEN] + "…"
        parts.append(f"error={text}")
    message = " ".join(parts)
    if status == "error" or error:
        logger.error(message)
    else:
        logger.info(message)


@asynccontextmanager
async def node_span(
    node: str, *, task_id: str = "", **params: Any
) -> AsyncIterator[None]:
    """异步区间埋点：进入记 start，正常退出记 ok（含耗时），异常记 error 后原样抛出。"""
    start = time.time()
    node_log(node, "start", task_id=task_id, **params)
    try:
        yield
    except Exception as exc:
        node_log(
            node,
            "error",
            task_id=task_id,
            elapsed_ms=(time.time() - start) * 1000,
            error=str(exc),
        )
        raise
    node_log(node, "ok", task_id=task_id, elapsed_ms=(time.time() - start) * 1000)
