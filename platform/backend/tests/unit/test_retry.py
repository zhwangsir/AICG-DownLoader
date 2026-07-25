"""重试装饰器单元测试。"""

from __future__ import annotations

import httpx
import pytest

from app.core.retry import is_retryable_exception, with_retry


class TestIsRetryableException:
    def test_timeout_error(self):
        assert is_retryable_exception(TimeoutError()) is True

    def test_connection_error(self):
        assert is_retryable_exception(ConnectionError()) is True

    def test_httpx_timeout(self):
        assert is_retryable_exception(httpx.TimeoutException("timeout")) is True

    def test_httpx_500(self):
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(500, request=request)
        exc = httpx.HTTPStatusError("server error", request=request, response=response)
        assert is_retryable_exception(exc) is True

    def test_httpx_404(self):
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(404, request=request)
        exc = httpx.HTTPStatusError("not found", request=request, response=response)
        assert is_retryable_exception(exc) is False

    def test_value_error(self):
        assert is_retryable_exception(ValueError("no")) is False


class TestWithRetry:
    async def test_success_no_retry(self):
        calls = []

        @with_retry(max_attempts=3, base_delay=0.01, jitter=False)
        async def fn():
            calls.append(1)
            return "ok"

        assert await fn() == "ok"
        assert len(calls) == 1

    async def test_retry_then_success(self):
        calls = []

        @with_retry(max_attempts=3, base_delay=0.01, jitter=False)
        async def fn():
            calls.append(1)
            if len(calls) < 2:
                raise TimeoutError("fail")
            return "ok"

        assert await fn() == "ok"
        assert len(calls) == 2

    async def test_max_attempts_exceeded(self):
        calls = []

        @with_retry(max_attempts=3, base_delay=0.01, jitter=False)
        async def fn():
            calls.append(1)
            raise TimeoutError("fail")

        with pytest.raises(TimeoutError):
            await fn()
        assert len(calls) == 3

    async def test_non_retryable_raises_immediately(self):
        calls = []

        @with_retry(max_attempts=3, base_delay=0.01, jitter=False)
        async def fn():
            calls.append(1)
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await fn()
        assert len(calls) == 1

    async def test_custom_retryable(self):
        calls = []

        @with_retry(max_attempts=3, base_delay=0.01, jitter=False, retryable=lambda e: isinstance(e, ValueError))
        async def fn():
            calls.append(1)
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await fn()
        assert len(calls) == 3

    async def test_on_retry_callback(self):
        retries = []

        def on_retry(exc, attempt):
            retries.append(attempt)

        @with_retry(max_attempts=3, base_delay=0.01, jitter=False, on_retry=on_retry)
        async def fn():
            raise TimeoutError("fail")

        with pytest.raises(TimeoutError):
            await fn()
        assert retries == [1, 2]
