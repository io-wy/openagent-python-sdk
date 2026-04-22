"""Targeted coverage tests for _http_base.py hot-spot lines.

Covers the following previously-uncovered lines:
  98   – _parse_retry_after_seconds: whitespace-only string returns None
  111  – _parse_retry_after_seconds: dt is None branch (shouldn't normally trigger,
         but we patch parsedate_to_datetime to return None to exercise it)
  122-123 – _response_headers: dict(headers) raises → return {}
  129  – _body_excerpt: response.text is not a string → return ""
  236  – _sleep early-return for delay_s <= 0
  280  – _request: total_budget_ms exhausted on first iteration (remaining_ms <= 0)
  296  – _request: connection error + remaining_ms clamp
  342  – _request: budget-exhausted-before-first-attempt raise
  407  – _open_stream: total_budget_ms exhausted on first iteration
  424  – _open_stream: connection error + remaining_ms clamp
  447-448 – _open_stream: best-effort cm.__aexit__ that raises
  479-480 – _open_stream: aread() raises → body_excerpt stays ""
  483-484 – _open_stream: decode raises → body_excerpt falls back to ""
  487-488 – _open_stream: resp_cm.__aexit__ raises (swallowed)
  496  – _open_stream: budget-exhausted-before-first-attempt raise
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from openagents.errors.exceptions import LLMConnectionError, LLMRateLimitError
from openagents.llm.providers import _http_base as http_base_module
from openagents.llm.providers._http_base import (
    HTTPProviderClient,
    _body_excerpt,
    _parse_retry_after_seconds,
    _response_headers,
    _RetryPolicy,
)

# ---------------------------------------------------------------------------
# Helpers shared with test_transport_and_helpers.py
# ---------------------------------------------------------------------------


class _RetryResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict | None = None,
        headers: dict | None = None,
        records: list[bytes] | None = None,
    ):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}
        self._records = records or []
        import json

        self.text = json.dumps(self._json_data)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._json_data

    async def aread(self) -> bytes:
        return self.content

    async def aiter_bytes(self):
        for record in self._records:
            yield record

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _RetryFakeClient:
    def __init__(self, *, responses=None, stream_responses=None):
        self._responses = list(responses or [])
        self._stream_responses = list(stream_responses or [])
        self.request_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs):
        self.request_calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise RuntimeError("no scripted responses left for request()")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def stream(self, method: str, url: str, **kwargs):
        self.stream_calls.append({"method": method, "url": url, **kwargs})
        if not self._stream_responses:
            raise RuntimeError("no scripted responses left for stream()")
        item = self._stream_responses.pop(0)
        if isinstance(item, BaseException):

            class _Raising:
                async def __aenter__(self_inner):
                    raise item

                async def __aexit__(self_inner, *a):
                    return False

            return _Raising()
        return item

    async def aclose(self) -> None:
        self.closed = True


def _install_retry_fake(
    monkeypatch,
    *,
    responses=None,
    stream_responses=None,
    connection_exc_types: tuple | None = None,
) -> _RetryFakeClient:
    fake_client = _RetryFakeClient(
        responses=responses or [],
        stream_responses=stream_responses or [],
    )
    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
        Limits=lambda **kwargs: kwargs,
        AsyncClient=lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)
    if connection_exc_types is not None:
        monkeypatch.setattr(http_base_module, "_CONNECTION_EXC_TYPES", connection_exc_types)
    return fake_client


@pytest.fixture
def record_sleep(monkeypatch):
    recorded: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        recorded.append(float(delay))

    monkeypatch.setattr(http_base_module.asyncio, "sleep", _fake_sleep)
    return recorded


class _TransportHarness(HTTPProviderClient):
    def __init__(self, *, retry_policy=None, extra_headers=None):
        super().__init__(timeout_ms=2500, retry_policy=retry_policy, extra_headers=extra_headers)


# ===========================================================================
# _parse_retry_after_seconds – uncovered branches
# ===========================================================================


def test_parse_retry_after_whitespace_only_returns_none():
    """Line 98: `if not text: return None` — after strip() empties the string."""
    assert _parse_retry_after_seconds("   ") is None
    assert _parse_retry_after_seconds("\t\n") is None


def test_parse_retry_after_dt_is_none_branch():
    """Line 111: parsedate_to_datetime returns None (patched) → return None."""
    with patch("openagents.llm.providers._http_base.parsedate_to_datetime", return_value=None):
        # Use a non-numeric string so we fall into the HTTP-date branch
        result = _parse_retry_after_seconds("Mon, 01 Jan 2099 00:00:00 GMT")
    assert result is None


# ===========================================================================
# _response_headers and _body_excerpt – exception paths
# ===========================================================================


def test_response_headers_dict_conversion_raises_returns_empty():
    """Lines 122-123: dict(headers) raises an exception → return {}."""

    class _BrokenHeaders:
        def items(self):
            raise RuntimeError("cannot iterate")

        def __iter__(self):
            raise RuntimeError("cannot iterate")

    class _BrokenResponse:
        headers = _BrokenHeaders()

    result = _response_headers(_BrokenResponse())
    assert result == {}


def test_body_excerpt_non_string_text_returns_empty():
    """Line 129: response.text is not a str → return ""."""

    class _NonStringTextResponse:
        text = 42  # not a string

    assert _body_excerpt(_NonStringTextResponse()) == ""

    class _NoneTextResponse:
        text = None

    assert _body_excerpt(_NoneTextResponse()) == ""

    class _NoTextResponse:
        pass

    assert _body_excerpt(_NoTextResponse()) == ""


# ===========================================================================
# _sleep – early return for non-positive delay
# ===========================================================================


@pytest.mark.asyncio
async def test_sleep_zero_returns_immediately(monkeypatch):
    """Line 236: delay_s <= 0 → return without calling asyncio.sleep."""
    sleep_calls: list[float] = []

    async def _fake_sleep(d):
        sleep_calls.append(d)

    monkeypatch.setattr(http_base_module.asyncio, "sleep", _fake_sleep)
    client = _TransportHarness()
    await client._sleep(0)
    await client._sleep(-1.5)
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_sleep_positive_delegates_to_asyncio(monkeypatch):
    """Sanity-check: positive delay does call asyncio.sleep."""
    sleep_calls: list[float] = []

    async def _fake_sleep(d):
        sleep_calls.append(d)

    monkeypatch.setattr(http_base_module.asyncio, "sleep", _fake_sleep)
    client = _TransportHarness()
    await client._sleep(0.5)
    assert sleep_calls == [0.5]


# ===========================================================================
# _request – total_budget_ms exhausted before first request (line 280 + 342)
# ===========================================================================


@pytest.mark.asyncio
async def test_request_budget_exhausted_before_first_attempt_raises(monkeypatch, record_sleep):
    """Lines 280 + 342: total_budget_ms=0 → loop breaks immediately, no last_exc or
    last_response → raises LLMConnectionError about exhausted budget."""

    _install_retry_fake(monkeypatch, responses=[_RetryResponse(status_code=200)])
    client = _TransportHarness(
        retry_policy=_RetryPolicy(
            max_attempts=5,
            initial_backoff_ms=10,
            total_budget_ms=0,  # already exhausted
        )
    )
    # Monkey-patch time.monotonic so elapsed_ms is always huge
    call_count = [0]

    def _fake_monotonic():
        call_count[0] += 1
        # First call (start) returns 0, subsequent calls return a large number
        if call_count[0] == 1:
            return 0.0
        return 1000.0  # 1000 seconds elapsed → 1,000,000 ms >> any budget

    monkeypatch.setattr(http_base_module.time, "monotonic", _fake_monotonic)

    with pytest.raises(LLMConnectionError, match="retry budget exhausted"):
        await client._request("POST", "https://example.com", json_body={})


# ===========================================================================
# _request – connection error with remaining_ms clamping (line 296)
# ===========================================================================


@pytest.mark.asyncio
async def test_request_connection_error_remaining_ms_clamps_delay(monkeypatch, record_sleep):
    """Line 296: when remaining_ms is set and a connection error fires, delay is
    clamped to min(delay, remaining_ms/1000)."""

    class _FakeConnectError(Exception):
        pass

    _install_retry_fake(
        monkeypatch,
        responses=[
            _FakeConnectError("boom"),
            _RetryResponse(status_code=200, json_data={"ok": True}),
        ],
        connection_exc_types=(_FakeConnectError,),
    )

    call_count = [0]

    def _fake_monotonic():
        call_count[0] += 1
        # Return a stable base so remaining_ms is small but positive
        # start = 0.0; second call = 0.001 (1 ms elapsed of 50 ms budget)
        return call_count[0] * 0.0005  # ~0.5 ms per call

    monkeypatch.setattr(http_base_module.time, "monotonic", _fake_monotonic)

    client = _TransportHarness(
        retry_policy=_RetryPolicy(
            max_attempts=3,
            initial_backoff_ms=5000,  # huge backoff
            max_backoff_ms=5000,
            total_budget_ms=50,  # very tight budget → remaining_ms clamps delay
        )
    )

    response = await client._request("POST", "https://example.com", json_body={})
    assert response.status_code == 200
    # The sleep was called at least once (clamped to remaining budget, not 5 seconds)
    assert len(record_sleep) >= 1
    assert record_sleep[0] < 5.0  # must be clamped below the 5s backoff


# ===========================================================================
# _open_stream – budget exhausted before first attempt (lines 407 + 496)
# ===========================================================================


@pytest.mark.asyncio
async def test_open_stream_budget_exhausted_before_first_attempt(monkeypatch, record_sleep):
    """Lines 407 + 496: total_budget exhausted → no request issued → raises
    LLMConnectionError about exhausted budget."""

    _install_retry_fake(monkeypatch, stream_responses=[_RetryResponse(status_code=200)])
    client = _TransportHarness(
        retry_policy=_RetryPolicy(
            max_attempts=5,
            initial_backoff_ms=10,
            total_budget_ms=0,
        )
    )

    call_count = [0]

    def _fake_monotonic():
        call_count[0] += 1
        return 0.0 if call_count[0] == 1 else 1000.0

    monkeypatch.setattr(http_base_module.time, "monotonic", _fake_monotonic)

    with pytest.raises(LLMConnectionError, match="retry budget exhausted"):
        await client._open_stream("POST", "https://example.com", json_body={})


# ===========================================================================
# _open_stream – connection error with remaining_ms clamping (line 424)
# ===========================================================================


@pytest.mark.asyncio
async def test_open_stream_connection_error_remaining_ms_clamps_delay(monkeypatch, record_sleep):
    """Line 424: remaining_ms path in _open_stream connection-error handler."""

    class _FakeConnectError(Exception):
        pass

    _install_retry_fake(
        monkeypatch,
        stream_responses=[
            _FakeConnectError("boom"),
            _RetryResponse(status_code=200, records=[b"data: ok\n\n"]),
        ],
        connection_exc_types=(_FakeConnectError,),
    )

    call_count = [0]

    def _fake_monotonic():
        call_count[0] += 1
        return call_count[0] * 0.0005

    monkeypatch.setattr(http_base_module.time, "monotonic", _fake_monotonic)

    client = _TransportHarness(
        retry_policy=_RetryPolicy(
            max_attempts=3,
            initial_backoff_ms=5000,
            max_backoff_ms=5000,
            total_budget_ms=50,
        )
    )

    response, cm = await client._open_stream("POST", "https://example.com", json_body={})
    try:
        assert response.status_code == 200
    finally:
        await cm.__aexit__(None, None, None)
    assert len(record_sleep) >= 1
    assert record_sleep[0] < 5.0


# ===========================================================================
# _open_stream – best-effort cm.__aexit__ that raises (lines 447-448)
# ===========================================================================


@pytest.mark.asyncio
async def test_open_stream_best_effort_close_raises_is_swallowed(monkeypatch, record_sleep):
    """Lines 447-448: the best-effort cm.__aexit__ call raises an exception;
    this must be swallowed so the retry can continue."""

    class _RaisingExitResponse(_RetryResponse):
        async def __aexit__(self, *args):
            raise RuntimeError("close failed!")

    # First response is retryable (429) with a broken __aexit__; second is success.
    _install_retry_fake(
        monkeypatch,
        stream_responses=[
            _RaisingExitResponse(status_code=429),
            _RetryResponse(status_code=200),
        ],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=1))

    # Should NOT raise despite __aexit__ throwing
    response, cm = await client._open_stream("POST", "https://example.com", json_body={})
    assert response.status_code == 200
    await cm.__aexit__(None, None, None)


# ===========================================================================
# _open_stream exhaustion – aread / decode / resp_cm.__aexit__ exception paths
# (lines 479-480, 483-484, 487-488)
# ===========================================================================


@pytest.mark.asyncio
async def test_open_stream_aread_raises_body_excerpt_empty(monkeypatch, record_sleep):
    """Lines 479-480: aread() raises → body_excerpt stays ""; error is still raised."""

    class _AreadRaisingResponse(_RetryResponse):
        async def aread(self) -> bytes:
            raise OSError("disk error")

    _install_retry_fake(
        monkeypatch,
        stream_responses=[_AreadRaisingResponse(status_code=429) for _ in range(3)],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=1))

    with pytest.raises(LLMRateLimitError) as exc_info:
        await client._open_stream("POST", "https://example.com", json_body={})
    # Error is raised (no body excerpt because aread failed)
    assert "HTTP 429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_open_stream_decode_raises_body_excerpt_empty(monkeypatch, record_sleep):
    """Lines 483-484: bytes.decode raises → body_excerpt falls back to "".

    We achieve this by making aread() return a non-bytes object that has no
    decode() method, forcing the AttributeError path to the except clause.
    """

    class _DecodeBrokenResponse(_RetryResponse):
        async def aread(self):
            # Return an object whose decode() raises
            class _BadBytes:
                def decode(self, *args, **kwargs):
                    raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")

            return _BadBytes()

    _install_retry_fake(
        monkeypatch,
        stream_responses=[_DecodeBrokenResponse(status_code=429) for _ in range(3)],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=1))

    with pytest.raises(LLMRateLimitError):
        await client._open_stream("POST", "https://example.com", json_body={})


@pytest.mark.asyncio
async def test_open_stream_resp_cm_aexit_raises_swallowed(monkeypatch, record_sleep):
    """Lines 487-488: resp_cm.__aexit__ raises after exhaustion; error is swallowed
    and the original LLMRateLimitError is still raised."""

    class _ExitRaisingResponse(_RetryResponse):
        async def __aexit__(self, *args):
            raise RuntimeError("cleanup explosion!")

    _install_retry_fake(
        monkeypatch,
        stream_responses=[_ExitRaisingResponse(status_code=429) for _ in range(3)],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=1))

    with pytest.raises(LLMRateLimitError):
        await client._open_stream("POST", "https://example.com", json_body={})
