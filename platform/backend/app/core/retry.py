"""通用重试装饰器 — 指数退避 + 可重试异常过滤。

使用场景：
- EXO LLM 调用偶发超时 / 空返回
- ComfyUI Worker 提交时网络抖动
- ComfyUI 历史记录查询暂时无结果
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_retryable_exception(exc: Exception) -> bool:
    """判断异常是否值得重试。"""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        # 5xx 或服务不可用可重试；4xx 通常不可重试
        return exc.response.status_code >= 500 or exc.response.status_code in (408, 429)
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return True
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, ConnectionError):
        return True
    return False


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable: Callable[[Exception], bool] | None = None,
    on_retry: Callable[[Exception, int], None] | None = None,
):
    """异步函数重试装饰器。

    Args:
        max_attempts: 最大尝试次数
        base_delay: 首次重试等待秒数
        max_delay: 最大等待秒数
        exponential_base: 指数退避底数
        jitter: 是否加入随机抖动，避免雪崩
        retryable: 自定义可重试异常判断函数
        on_retry: 每次重试时的回调
    """
    should_retry = retryable or is_retryable_exception

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt == max_attempts or not should_retry(e):
                        raise

                    delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random()

                    logger.warning(
                        "[%s] 第 %d/%d 次尝试失败: %s，%.2fs 后重试",
                        func.__name__,
                        attempt,
                        max_attempts,
                        e,
                        delay,
                    )
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception:
                            pass
                    await asyncio.sleep(delay)

            # 永远不会到达这里，但类型检查需要
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
