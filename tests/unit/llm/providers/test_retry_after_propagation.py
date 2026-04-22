"""When HTTP retries are exhausted on 429, the raised LLMRateLimitError carries retry_after_ms."""

from __future__ import annotations

import httpx
import pytest

from openagents.errors.exceptions import LLMConnectionError, LLMRateLimitError, LLMResponseError
from openagents.llm.providers._http_base import (
    HTTPProviderClient,
    _make_error_for_status,
    _RetryPolicy,
)
from openagents.llm.providers.litellm_client import _map_litellm_exception


def test_make_error_for_status_threads_retry_after_ms():
    exc = _make_error_for_status(
        url="https://example/api",
        status=429,
        body_excerpt="Too Many Requests",
        retryable_status=_RetryPolicy().retryable_status,
        retry_after_ms=5000,
    )
    assert isinstance(exc, LLMRateLimitError)
    assert exc.retry_after_ms == 5000


def test_make_error_for_status_retry_after_none_when_absent():
    exc = _make_error_for_status(
        url="https://example/api",
        status=429,
        body_excerpt="",
        retryable_status=_RetryPolicy().retryable_status,
        retry_after_ms=None,
    )
    assert isinstance(exc, LLMRateLimitError)
    assert exc.retry_after_ms is None


def test_make_error_for_status_retry_after_not_on_non_rate_limit():
    # Connection / response errors don't carry retry_after_ms — it's rate-limit specific
    exc = _make_error_for_status(
        url="https://example/api",
        status=502,
        body_excerpt="",
        retryable_status=_RetryPolicy().retryable_status,
        retry_after_ms=5000,  # Passed but should NOT propagate to connection errors
    )
    assert isinstance(exc, LLMConnectionError)
    # LLMConnectionError has no retry_after_ms field — verify we didn't accidentally leak it
    assert not hasattr(exc, "retry_after_ms") or getattr(exc, "retry_after_ms", None) is None


@pytest.mark.asyncio
async def test_request_exhausted_retries_raises_with_retry_after_ms(monkeypatch):
    call_count = {"n": 0}

    def _handler(request):
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "5"}, text="slow")

    transport = httpx.MockTransport(_handler)

    client = HTTPProviderClient.__new__(HTTPProviderClient)
    HTTPProviderClient.__init__(
        client,
        timeout_ms=1000,
        retry_policy=_RetryPolicy(max_attempts=2, initial_backoff_ms=1, max_backoff_ms=1),
    )
    # Inject mock transport
    client._http_client = httpx.AsyncClient(transport=transport)

    async def _noop_sleep(_):
        pass

    monkeypatch.setattr("openagents.llm.providers._http_base.asyncio.sleep", _noop_sleep)

    with pytest.raises(LLMRateLimitError) as ei:
        await client._request("POST", "https://api.example/messages", json_body={})
    assert ei.value.retry_after_ms == 5000
    await client.aclose()


# --- LiteLLM path ---


def test_map_litellm_rate_limit_reads_retry_after():
    class _FakeRateLimitError(Exception):
        pass

    _FakeRateLimitError.__module__ = "litellm.exceptions"
    _FakeRateLimitError.__name__ = "RateLimitError"

    exc = _FakeRateLimitError("slow down")
    exc.retry_after = 7  # seconds
    mapped = _map_litellm_exception(exc)
    assert isinstance(mapped, LLMRateLimitError)
    assert mapped.retry_after_ms == 7000


def test_map_litellm_rate_limit_retry_after_none_when_absent():
    class _FakeRateLimitError(Exception):
        pass

    _FakeRateLimitError.__module__ = "litellm.exceptions"
    _FakeRateLimitError.__name__ = "RateLimitError"
    mapped = _map_litellm_exception(_FakeRateLimitError("slow"))
    assert isinstance(mapped, LLMRateLimitError)
    assert mapped.retry_after_ms is None


def test_map_litellm_non_rate_limit_unchanged():
    class _FakeApiError(Exception):
        pass

    _FakeApiError.__module__ = "litellm.exceptions"
    _FakeApiError.__name__ = "APIError"
    mapped = _map_litellm_exception(_FakeApiError("boom"))
    assert isinstance(mapped, LLMResponseError)
