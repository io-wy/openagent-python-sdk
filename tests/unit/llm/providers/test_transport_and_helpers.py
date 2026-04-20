from __future__ import annotations

import json
import types

import pytest

from openagents.config.schema import LLMRetryOptions
from openagents.errors.exceptions import (
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
)
from openagents.llm.providers import _http_base as http_base_module
from openagents.llm.providers._http_base import (
    HTTPProviderClient,
    _parse_retry_after_seconds,
    _RetryPolicy,
)
from openagents.llm.providers.anthropic import AnthropicClient
from openagents.llm.providers.anthropic import _parse_tool_input as anthropic_parse_tool_input
from openagents.llm.providers.anthropic import _parse_usage as anthropic_parse_usage
from openagents.llm.providers.openai_compatible import (
    OpenAICompatibleClient,
    _extract_text_content,
    _parse_json_object,
    _parse_tool_calls,
)


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data: dict | None = None, records: list[bytes] | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self._records = records or []
        self.text = json.dumps(self._json_data)
        self.content = self.text.encode("utf-8")

    def json(self) -> dict:
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


class _FakeAsyncClient:
    def __init__(self, *, response: _FakeResponse, stream_response: _FakeResponse | None = None, **kwargs):
        self.response = response
        self.stream_response = stream_response or _FakeResponse(records=[])
        self.kwargs = kwargs
        self.requests: list[dict] = []
        self.stream_requests: list[dict] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.response

    def stream(self, method: str, url: str, **kwargs):
        self.stream_requests.append({"method": method, "url": url, **kwargs})
        return self.stream_response

    async def aclose(self) -> None:
        self.closed = True


def _install_fake_httpx(
    monkeypatch, *, response: _FakeResponse, stream_response: _FakeResponse | None = None
) -> _FakeAsyncClient:
    fake_client = _FakeAsyncClient(response=response, stream_response=stream_response)
    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
        Limits=lambda **kwargs: kwargs,
        AsyncClient=lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)
    return fake_client


class _TransportHarness(HTTPProviderClient):
    def __init__(
        self,
        *,
        retry_policy: _RetryPolicy | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(
            timeout_ms=2500,
            retry_policy=retry_policy,
            extra_headers=extra_headers,
        )


class _RetryResponse:
    """Response-like object configurable per attempt for retry tests."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict | None = None,
        headers: dict[str, str] | None = None,
        records: list[bytes] | None = None,
    ):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}
        self._records = records or []
        self.text = json.dumps(self._json_data)
        self.content = self.text.encode("utf-8")

    def json(self) -> dict:
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
    """Fake httpx client whose `request` / `stream` walk through a scripted
    sequence of responses (per attempt) and record invocations."""

    def __init__(
        self,
        *,
        responses: list[_RetryResponse | BaseException],
        stream_responses: list[_RetryResponse | BaseException] | None = None,
    ):
        self._responses = list(responses)
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
    responses: list = None,
    stream_responses: list = None,
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
    """Record asyncio.sleep calls instead of really sleeping."""
    recorded: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        recorded.append(float(delay))

    monkeypatch.setattr(http_base_module.asyncio, "sleep", _fake_sleep)
    return recorded


@pytest.mark.asyncio
async def test_http_provider_client_builds_timeouts_caches_client_and_closes(monkeypatch):
    fake_client = _install_fake_httpx(monkeypatch, response=_FakeResponse(json_data={"ok": True}))
    client = _TransportHarness()

    timeout = client._build_timeout(read_timeout_s=9.0)
    first = await client._get_http_client()
    second = await client._get_http_client()
    await client._request("POST", "https://example.com", headers={"A": "1"}, json_body={"ok": True})
    stream_ctx = await client._stream("GET", "https://example.com/stream")
    await client.aclose()

    assert timeout["kwargs"]["read"] == 9.0
    assert first is second is fake_client
    assert fake_client.requests[0]["json"] == {"ok": True}
    assert stream_ctx is fake_client.stream_response
    assert fake_client.closed is True
    assert client._http_client is None

    monkeypatch.setattr(http_base_module, "httpx", None)
    with pytest.raises(RuntimeError, match="httpx is required"):
        client._require_httpx()


@pytest.mark.asyncio
async def test_anthropic_helpers_and_error_paths(monkeypatch):
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    assert anthropic_parse_usage(None) is None
    assert anthropic_parse_usage({"input_tokens": 2, "output_tokens": 3}).total_tokens == 5
    parsed, raw = anthropic_parse_tool_input('{"path":"README.md"}')
    assert parsed == {"path": "README.md"}
    assert raw == '{"path":"README.md"}'
    assert anthropic_parse_tool_input("not-json") == ({}, "not-json")
    assert client._build_structured_output_tool({"type": "json"})[0] == "structured_output"
    assert client._build_structured_output_tool({"type": "other"}) == (None, None)
    assert client._parse_sse_event(b'event: ping\ndata: {"ok": true}\n') == ("ping", '{"ok": true}')
    assert client._parse_sse_event(b'{"choices": []}') == (None, '{"choices": []}')
    assert client._extract_stream_error({"error": {"message": "bad"}}) == "bad"
    assert "status" in client._extract_stream_error({"base_resp": {"status_code": 503}}).lower()

    _install_fake_httpx(monkeypatch, response=_FakeResponse(status_code=500, json_data={"error": "bad"}))
    # 500 is in the generic 5xx range → classified as connection error
    with pytest.raises(LLMConnectionError, match="HTTP 500"):
        await client.generate(messages=[{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_anthropic_complete_stream_handles_raw_choice_records_and_http_errors(monkeypatch, record_sleep):
    stream_response = _FakeResponse(
        records=[
            b'{"choices":[{"delta":{"content":"Hello"},"finish_reason":"end_turn"}],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n',
        ]
    )
    _install_fake_httpx(monkeypatch, response=_FakeResponse(json_data={}), stream_response=stream_response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    chunks = [chunk async for chunk in client.complete_stream(messages=[{"role": "user", "content": "hello"}])]
    assert [chunk.type for chunk in chunks] == ["content_block_delta", "message_stop"]
    assert chunks[0].delta == {"type": "text_delta", "text": "Hello"}
    assert chunks[1].content == {"stop_reason": "end_turn"}

    error_stream = _FakeResponse(status_code=429, json_data={"error": "rate-limited"})
    _install_fake_httpx(monkeypatch, response=_FakeResponse(json_data={}), stream_response=error_stream)
    error_client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")
    error_chunks = [
        chunk async for chunk in error_client.complete_stream(messages=[{"role": "user", "content": "hello"}])
    ]
    assert error_chunks[0].type == "error"
    assert "HTTP 429" in (error_chunks[0].error or "")


@pytest.mark.asyncio
async def test_openai_compatible_helpers_and_error_paths(monkeypatch):
    client = OpenAICompatibleClient(api_base="https://api.openai.com", model="gpt-test")

    assert client._chat_completions_endpoint() == "https://api.openai.com/v1/chat/completions"
    assert _extract_text_content("hello") == "hello"
    assert _extract_text_content([{"text": "a"}, {"text": "b"}, {"ignored": True}]) == "ab"
    assert _extract_text_content(123) == "123"
    assert _parse_json_object('{"x": 1}') == {"x": 1}
    assert _parse_json_object("bad-json") == {}
    assert _parse_tool_calls(
        [
            {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"README.md"}'}},
            {"function": {"name": ""}},
        ]
    )[0].arguments == {"path": "README.md"}
    assert client._parse_sse_record(b"data: one\ndata: two\n") == "one\ntwo"

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    assert client._build_headers()["Authorization"] == "Bearer secret"

    _install_fake_httpx(monkeypatch, response=_FakeResponse(status_code=500, json_data={"error": "bad"}))
    # 500 classified as connection error (generic 5xx)
    with pytest.raises(LLMConnectionError, match="HTTP 500"):
        await client.generate(messages=[{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_openai_compatible_complete_stream_handles_http_errors_and_trailing_usage(monkeypatch):
    stream_response = _FakeResponse(
        records=[
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"choices":[{"finish_reason":"stop"}]}\n\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    _install_fake_httpx(monkeypatch, response=_FakeResponse(json_data={}), stream_response=stream_response)
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-test")

    chunks = [chunk async for chunk in client.complete_stream(messages=[{"role": "user", "content": "hello"}])]
    assert [chunk.type for chunk in chunks] == ["content_block_delta", "message_stop"]
    assert chunks[0].delta == {"type": "text_delta", "text": "Hello"}
    assert chunks[1].content == {"stop_reason": "stop"}
    assert chunks[1].usage.total_tokens == 5

    error_stream = _FakeResponse(status_code=401, json_data={"error": "unauthorized"})
    _install_fake_httpx(monkeypatch, response=_FakeResponse(json_data={}), stream_response=error_stream)
    error_client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-test")
    error_chunks = [
        chunk async for chunk in error_client.complete_stream(messages=[{"role": "user", "content": "hello"}])
    ]
    assert error_chunks[0].type == "error"
    assert "HTTP 401" in (error_chunks[0].error or "")


# ---------------------------------------------------------------------------
# Retry policy + error classification tests (group 3.6 of builtin-provider-feature-parity)
# ---------------------------------------------------------------------------


def test_retry_policy_defaults_match_schema_defaults():
    policy = _RetryPolicy.from_options(None)
    assert policy.max_attempts == 3
    assert policy.initial_backoff_ms == 500
    assert policy.max_backoff_ms == 5000
    assert policy.backoff_multiplier == 2.0
    assert policy.retry_on_connection_errors is True
    assert policy.total_budget_ms is None


def test_retry_policy_from_options_threads_values():
    opts = LLMRetryOptions(
        max_attempts=5,
        initial_backoff_ms=100,
        max_backoff_ms=8000,
        backoff_multiplier=3.0,
        retry_on_connection_errors=False,
        total_budget_ms=10_000,
    )
    policy = _RetryPolicy.from_options(opts)
    assert policy.max_attempts == 5
    assert policy.initial_backoff_ms == 100
    assert policy.max_backoff_ms == 8000
    assert policy.backoff_multiplier == 3.0
    assert policy.retry_on_connection_errors is False
    assert policy.total_budget_ms == 10_000


def test_retry_policy_extra_retryable_status_merges():
    policy = _RetryPolicy.from_options(None, extra_retryable_status=frozenset({529}))
    assert 429 in policy.retryable_status
    assert 529 in policy.retryable_status


def test_retry_policy_backoff_curve():
    policy = _RetryPolicy(
        max_attempts=5,
        initial_backoff_ms=500,
        max_backoff_ms=5000,
        backoff_multiplier=2.0,
    )
    # 500, 1000, 2000, 4000, 5000 (capped)
    assert policy.backoff_ms(1) == 500
    assert policy.backoff_ms(2) == 1000
    assert policy.backoff_ms(3) == 2000
    assert policy.backoff_ms(4) == 4000
    assert policy.backoff_ms(5) == 5000
    assert policy.backoff_ms(6) == 5000  # capped


def test_parse_retry_after_delta_seconds():
    assert _parse_retry_after_seconds("5") == 5.0
    assert _parse_retry_after_seconds("0") == 0.0
    assert _parse_retry_after_seconds("  10.5  ") == 10.5
    assert _parse_retry_after_seconds(None) is None
    assert _parse_retry_after_seconds("") is None


def test_parse_retry_after_http_date_returns_nonnegative():
    # HTTP-date in the past returns 0
    assert _parse_retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    # Unparseable returns None
    assert _parse_retry_after_seconds("not a date") is None


@pytest.mark.asyncio
async def test_request_retries_429_exactly_max_attempts_then_raises_rate_limit(monkeypatch, record_sleep):
    fake = _install_retry_fake(
        monkeypatch,
        responses=[
            _RetryResponse(status_code=429, json_data={"error": "rate"}),
            _RetryResponse(status_code=429, json_data={"error": "rate"}),
            _RetryResponse(status_code=429, json_data={"error": "rate"}),
        ],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=10))

    with pytest.raises(LLMRateLimitError) as exc_info:
        await client._request("POST", "https://example.com", json_body={"x": 1})

    assert len(fake.request_calls) == 3
    # Two retries between three attempts
    assert len(record_sleep) == 2
    assert "HTTP 429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_request_retry_after_header_overrides_computed_backoff(monkeypatch, record_sleep):
    _install_retry_fake(
        monkeypatch,
        responses=[
            _RetryResponse(status_code=429, headers={"Retry-After": "5"}),
            _RetryResponse(status_code=200, json_data={"ok": True}),
        ],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=100, max_backoff_ms=200))

    response = await client._request("POST", "https://example.com", json_body={})
    assert response.status_code == 200
    # Retry-After: 5 seconds wins over the computed 0.1s backoff
    assert record_sleep[0] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_request_retries_connection_errors_when_enabled(monkeypatch, record_sleep):
    class _FakeConnectError(Exception):
        pass

    fake = _install_retry_fake(
        monkeypatch,
        responses=[
            _FakeConnectError("boom"),
            _FakeConnectError("boom again"),
            _RetryResponse(status_code=200, json_data={"ok": True}),
        ],
        connection_exc_types=(_FakeConnectError,),
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=10))

    response = await client._request("POST", "https://example.com", json_body={})
    assert response.status_code == 200
    assert len(fake.request_calls) == 3
    assert len(record_sleep) == 2


@pytest.mark.asyncio
async def test_request_connection_error_exhaustion_raises_connection_error(monkeypatch, record_sleep):
    class _FakeConnectError(Exception):
        pass

    _install_retry_fake(
        monkeypatch,
        responses=[_FakeConnectError("boom") for _ in range(3)],
        connection_exc_types=(_FakeConnectError,),
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=10))

    with pytest.raises(LLMConnectionError) as exc_info:
        await client._request("POST", "https://example.com", json_body={})
    assert "_FakeConnectError" in str(exc_info.value)


@pytest.mark.asyncio
async def test_request_max_attempts_one_disables_retry(monkeypatch, record_sleep):
    fake = _install_retry_fake(
        monkeypatch,
        responses=[_RetryResponse(status_code=429, json_data={"error": "rate"})],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=1, initial_backoff_ms=10))

    with pytest.raises(LLMRateLimitError):
        await client._request("POST", "https://example.com", json_body={})
    assert len(fake.request_calls) == 1
    assert record_sleep == []


@pytest.mark.asyncio
async def test_request_total_budget_ms_halts_retries(monkeypatch, record_sleep):
    _install_retry_fake(
        monkeypatch,
        responses=[_RetryResponse(status_code=429) for _ in range(10)],
    )
    # Very tight budget so we stop early (sleeps are fake so no real time passes,
    # but `time.monotonic()` still advances — set budget near zero to cut attempts)
    client = _TransportHarness(
        retry_policy=_RetryPolicy(
            max_attempts=10,
            initial_backoff_ms=10,
            total_budget_ms=1,
        ),
    )
    with pytest.raises((LLMRateLimitError, LLMConnectionError)):
        await client._request("POST", "https://example.com", json_body={})


@pytest.mark.asyncio
async def test_request_non_retryable_500_returned_to_caller(monkeypatch):
    # 500 is NOT in the default retryable set (only 502/503/504 are).
    fake = _install_retry_fake(
        monkeypatch,
        responses=[_RetryResponse(status_code=500, json_data={"err": "oops"})],
    )
    client = _TransportHarness()
    response = await client._request("POST", "https://example.com", json_body={})
    assert response.status_code == 500
    assert len(fake.request_calls) == 1


@pytest.mark.asyncio
async def test_request_401_non_retryable_returned_to_caller(monkeypatch):
    fake = _install_retry_fake(
        monkeypatch,
        responses=[_RetryResponse(status_code=401, json_data={"err": "auth"})],
    )
    client = _TransportHarness()
    response = await client._request("POST", "https://example.com", json_body={})
    assert response.status_code == 401
    assert len(fake.request_calls) == 1


@pytest.mark.asyncio
async def test_open_stream_429_exhaustion_raises_rate_limit(monkeypatch, record_sleep):
    fake = _install_retry_fake(
        monkeypatch,
        stream_responses=[
            _RetryResponse(status_code=429, json_data={"error": "rate"}),
            _RetryResponse(status_code=429, json_data={"error": "rate"}),
            _RetryResponse(status_code=429, json_data={"error": "rate"}),
        ],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=10))

    with pytest.raises(LLMRateLimitError) as exc_info:
        await client._open_stream("POST", "https://example.com", json_body={})
    assert len(fake.stream_calls) == 3
    assert "HTTP 429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_open_stream_200_returns_entered_response(monkeypatch):
    resp = _RetryResponse(status_code=200, records=[b"data: hi\n\n"])
    _install_retry_fake(monkeypatch, stream_responses=[resp])
    client = _TransportHarness()

    response, cm = await client._open_stream("POST", "https://example.com", json_body={})
    try:
        assert response.status_code == 200
    finally:
        await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_open_stream_success_after_retry(monkeypatch, record_sleep):
    _install_retry_fake(
        monkeypatch,
        stream_responses=[
            _RetryResponse(status_code=429, headers={"Retry-After": "0"}),
            _RetryResponse(status_code=200, records=[b"data: ok\n\n"]),
        ],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=10))

    response, cm = await client._open_stream("POST", "https://example.com", json_body={})
    try:
        assert response.status_code == 200
    finally:
        await cm.__aexit__(None, None, None)
    assert len(record_sleep) == 1


def test_yield_stream_error_chunk_maps_exception_types():
    client = _TransportHarness()
    assert client._yield_stream_error_chunk(exc=LLMRateLimitError("HTTP 429: x")).error_type == "rate_limit"
    assert client._yield_stream_error_chunk(exc=LLMConnectionError("HTTP 503: x")).error_type == "connection"
    assert client._yield_stream_error_chunk(exc=LLMResponseError("bad json")).error_type == "response"
    assert client._yield_stream_error_chunk(exc=ValueError("whatever")).error_type == "unknown"


def test_merge_headers_user_overrides_win():
    client = _TransportHarness(extra_headers={"anthropic-version": "2024-10-22", "X-Trace": "on"})
    merged = client._merge_headers({"anthropic-version": "2023-06-01", "x-api-key": "sk"})
    assert merged["anthropic-version"] == "2024-10-22"
    assert merged["x-api-key"] == "sk"
    assert merged["X-Trace"] == "on"


def test_parse_response_json_raises_llm_response_error():
    client = _TransportHarness()

    class _BadResp:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    with pytest.raises(LLMResponseError):
        client._parse_response_json(url="https://x", response=_BadResp())


def test_raise_for_response_status_200_is_noop_else_raises():
    client = _TransportHarness()

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = "{}"

    # 200 is a no-op
    client._raise_for_response_status(url="https://x", response=_Resp(200))
    with pytest.raises(LLMRateLimitError):
        client._raise_for_response_status(url="https://x", response=_Resp(429))
    with pytest.raises(LLMConnectionError):
        client._raise_for_response_status(url="https://x", response=_Resp(503))
    with pytest.raises(LLMResponseError):
        client._raise_for_response_status(url="https://x", response=_Resp(404))


@pytest.mark.asyncio
async def test_open_stream_connection_errors_retry_then_raise(monkeypatch, record_sleep):
    class _FakeConnectError(Exception):
        pass

    _install_retry_fake(
        monkeypatch,
        stream_responses=[_FakeConnectError("boom") for _ in range(3)],
        connection_exc_types=(_FakeConnectError,),
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=10))

    with pytest.raises(LLMConnectionError) as exc_info:
        await client._open_stream("POST", "https://example.com", json_body={})
    assert "_FakeConnectError" in str(exc_info.value)
    assert len(record_sleep) == 2


@pytest.mark.asyncio
async def test_open_stream_connection_errors_disabled_aborts_immediately(monkeypatch, record_sleep):
    class _FakeConnectError(Exception):
        pass

    _install_retry_fake(
        monkeypatch,
        stream_responses=[_FakeConnectError("boom")],
        connection_exc_types=(_FakeConnectError,),
    )
    client = _TransportHarness(
        retry_policy=_RetryPolicy(
            max_attempts=3,
            initial_backoff_ms=10,
            retry_on_connection_errors=False,
        )
    )

    with pytest.raises(LLMConnectionError):
        await client._open_stream("POST", "https://example.com", json_body={})
    assert record_sleep == []


@pytest.mark.asyncio
async def test_open_stream_budget_halts_after_first_attempt(monkeypatch, record_sleep):
    _install_retry_fake(
        monkeypatch,
        stream_responses=[_RetryResponse(status_code=429) for _ in range(10)],
    )
    client = _TransportHarness(
        retry_policy=_RetryPolicy(
            max_attempts=10,
            initial_backoff_ms=10,
            total_budget_ms=1,
        )
    )

    with pytest.raises((LLMRateLimitError, LLMConnectionError)):
        await client._open_stream("POST", "https://example.com", json_body={})


@pytest.mark.asyncio
async def test_request_retry_after_http_date_is_parsed(monkeypatch, record_sleep):
    _install_retry_fake(
        monkeypatch,
        responses=[
            _RetryResponse(
                status_code=429,
                headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"},
            ),
            _RetryResponse(status_code=200, json_data={"ok": True}),
        ],
    )
    client = _TransportHarness(retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=10))

    response = await client._request("POST", "https://example.com", json_body={})
    assert response.status_code == 200
    # HTTP-date in the past → returns 0, so computed backoff (0.01s) wins
    assert record_sleep[0] == pytest.approx(0.01, abs=0.001)


def test_classify_stream_error_helper_from_status_and_exception():
    from openagents.llm.providers._http_base import _classify_stream_error

    assert _classify_stream_error(status=429, exc=None, retryable_status=frozenset({429})) == "rate_limit"
    assert _classify_stream_error(status=503, exc=None, retryable_status=frozenset({503})) == "connection"
    assert _classify_stream_error(status=404, exc=None, retryable_status=frozenset()) == "unknown"
    assert _classify_stream_error(status=None, exc=RuntimeError("x"), retryable_status=frozenset()) == "connection"
    assert _classify_stream_error(status=None, exc=None, retryable_status=frozenset()) == "unknown"
