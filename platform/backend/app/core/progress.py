"""任务进度跟踪 — 内存存储 + SSE 推送。

设计约束：
- 单实例 / 小规模使用，内存 dict 足够
- 任务完成后保留 30 分钟，便于客户端重连拉取最终结果
- 进度更新线程安全（asyncio 单线程协程安全）
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ProgressRecord:
    """单个任务的进度记录。"""

    task_id: str
    agent: str
    status: str  # pending / running / completed / failed
    percent: int = 0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    listeners: list[Callable[["ProgressRecord"], None]] = field(default_factory=list)


class ProgressTracker:
    """内存任务进度跟踪器。"""

    def __init__(self, ttl_seconds: float = 1800.0):
        self._tasks: dict[str, ProgressRecord] = {}
        self._ttl = ttl_seconds

    def create(self, agent: str, message: str = "任务已创建") -> str:
        """创建新任务，返回 task_id。"""
        task_id = f"{agent}-{uuid.uuid4().hex[:12]}"
        self._tasks[task_id] = ProgressRecord(
            task_id=task_id,
            agent=agent,
            status="pending",
            message=message,
        )
        return task_id

    def get(self, task_id: str) -> ProgressRecord | None:
        """获取任务记录。"""
        self._cleanup()
        return self._tasks.get(task_id)

    def update(
        self,
        task_id: str,
        status: str | None = None,
        percent: int | None = None,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ProgressRecord | None:
        """更新任务进度，并通知所有监听器。"""
        record = self._tasks.get(task_id)
        if record is None:
            return None

        if status is not None:
            record.status = status
        if percent is not None:
            record.percent = max(0, min(100, percent))
        if message is not None:
            record.message = message
        if result is not None:
            record.result = result
        if error is not None:
            record.error = error
        record.updated_at = time.time()

        for listener in record.listeners:
            try:
                listener(record)
            except Exception:
                # 监听器失败不应影响主流程
                pass
        return record

    def subscribe(self, task_id: str, listener: Callable[[ProgressRecord], None]) -> bool:
        """订阅任务进度更新。"""
        record = self._tasks.get(task_id)
        if record is None:
            return False
        record.listeners.append(listener)
        return True

    def unsubscribe(self, task_id: str, listener: Callable[[ProgressRecord], None]) -> None:
        """取消订阅。"""
        record = self._tasks.get(task_id)
        if record is None:
            return
        try:
            record.listeners.remove(listener)
        except ValueError:
            pass

    def _cleanup(self) -> None:
        """清理过期任务。"""
        now = time.time()
        expired = [
            tid
            for tid, rec in self._tasks.items()
            if rec.status in ("completed", "failed") and now - rec.updated_at > self._ttl
        ]
        for tid in expired:
            self._tasks.pop(tid, None)


def progress_event(record: ProgressRecord) -> str:
    """将进度记录序列化为 SSE 事件。"""
    data = {
        "task_id": record.task_id,
        "agent": record.agent,
        "status": record.status,
        "percent": record.percent,
        "message": record.message,
        "result": record.result,
        "error": record.error,
        "updated_at": record.updated_at,
    }
    import json

    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# 全局进度跟踪器实例
progress_tracker = ProgressTracker()
