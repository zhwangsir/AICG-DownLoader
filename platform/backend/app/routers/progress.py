"""SSE 进度推送路由。"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.progress import ProgressRecord, progress_event, progress_tracker

router = APIRouter(prefix="/api/progress", tags=["progress"])


@router.get("/{task_id}/stream")
async def progress_stream(task_id: str) -> StreamingResponse:
    """SSE 流式推送任务进度。

    客户端断开连接时自动清理监听器；重连后从当前状态继续推送。
    """
    record = progress_tracker.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在或已过期")

    async def event_generator() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[ProgressRecord | None] = asyncio.Queue()

        def listener(r: ProgressRecord) -> None:
            try:
                queue.put_nowait(r)
            except asyncio.QueueFull:
                pass

        progress_tracker.subscribe(task_id, listener)

        # 先推送一次当前状态
        yield progress_event(record)

        try:
            while True:
                try:
                    update = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield ":heartbeat\n\n"
                    continue

                if update is None:
                    break

                yield progress_event(update)

                # 任务结束后再推一条，确保客户端收到最终结果
                if update.status in ("completed", "failed"):
                    await asyncio.sleep(0.1)
                    break
        finally:
            progress_tracker.unsubscribe(task_id, listener)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}")
async def progress_get(task_id: str) -> dict:
    """查询任务当前状态（非流式，便于一次性获取）。"""
    record = progress_tracker.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在或已过期")
    return {
        "task_id": record.task_id,
        "agent": record.agent,
        "status": record.status,
        "percent": record.percent,
        "message": record.message,
        "result": record.result,
        "error": record.error,
        "updated_at": record.updated_at,
    }
