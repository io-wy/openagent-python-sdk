from __future__ import annotations

import types

import pytest

from openagents.llm.providers import _http_base as http_base_module
from openagents.llm.providers.anthropic import AnthropicClient


class _FakeStreamResponse:
    def __init__(self, records: list[bytes]) -> None:
        self.status_code = 200
        self._records = records

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def aread(self) -> bytes:
        return b""

    async def aiter_bytes(self):
        for record in self._records:
            yield record


class _FakeAsyncClient:
    def __init__(self, *, timeout=None, records: list[bytes], **kwargs) -> None:
        self.timeout = timeout
        self.records = records
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def stream(self, method: str, url: str, **kwargs):
        _ = (method, url, kwargs)
        return _FakeStreamResponse(self.records)


@pytest.mark.asyncio
async def test_complete_stream_preserves_anthropic_delta_payload(monkeypatch):
    delta_payload = {"type": "text_delta", "text": "Hello"}
    records = [
        (
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
        ),
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]

    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=lambda **kwargs: _FakeAsyncClient(records=records, **kwargs),
        Limits=lambda **kwargs: None,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)

    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    chunks = []
    async for chunk in client.complete_stream(messages=[{"role": "user", "content": "hello"}]):
        chunks.append(chunk)

    assert len(chunks) == 2
    assert chunks[0].type == "content_block_delta"
    assert chunks[0].delta == delta_payload
    assert chunks[1].type == "message_stop"


@pytest.mark.asyncio
async def test_complete_stream_normalizes_content_block_start_payload(monkeypatch):
    records = [
        (
            b"event: content_block_start\n"
            b'data: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"tool_use","id":"toolu_1","name":"read"}}\n\n'
        ),
    ]

    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=lambda **kwargs: _FakeAsyncClient(records=records, **kwargs),
        Limits=lambda **kwargs: None,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)

    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    chunks = []
    async for chunk in client.complete_stream(messages=[{"role": "user", "content": "hello"}]):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].type == "content_block_start"
    assert chunks[0].content == {"type": "tool_use", "id": "toolu_1", "name": "read"}


@pytest.mark.asyncio
async def test_complete_stream_surfaces_json_error_body_without_sse_delimiter(monkeypatch):
    records = [
        (
            b'{"base_resp":{"status_code":1004,"status_msg":"login fail: Please carry the API secret key in the '
            b"'Authorization' field of the request header\"}}"
        ),
    ]

    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=lambda **kwargs: _FakeAsyncClient(records=records, **kwargs),
        Limits=lambda **kwargs: None,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)

    client = AnthropicClient(
        api_base="https://api.minimax.chat/v1",
        model="MiniMax-M2.7",
        stream_endpoint="https://api.minimax.chat/v1/text/chatcompletion_v2",
    )

    chunks = []
    async for chunk in client.complete_stream(messages=[{"role": "user", "content": "hello"}]):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert "login fail" in (chunks[0].error or "")


@pytest.mark.asyncio
async def test_complete_stream_carries_usage_and_stop_reason_from_message_events(monkeypatch):
    records = [
        (
            b"event: message_start\n"
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"output_tokens":1}}}\n\n'
        ),
        (
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
        ),
        (
            b"event: message_delta\n"
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":4}}\n\n'
        ),
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]

    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=lambda **kwargs: _FakeAsyncClient(records=records, **kwargs),
        Limits=lambda **kwargs: None,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)

    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    chunks = []
    async for chunk in client.complete_stream(messages=[{"role": "user", "content": "hello"}]):
        chunks.append(chunk)

    assert len(chunks) == 4
    assert chunks[0].type == "message_start"
    assert chunks[1].type == "content_block_delta"
    assert chunks[2].type == "message_delta"
    assert chunks[3].type == "message_stop"
    assert chunks[3].content == {"stop_reason": "end_turn"}
    assert chunks[3].usage.input_tokens == 10
    assert chunks[3].usage.output_tokens == 4
    assert chunks[3].usage.total_tokens == 14
    assert chunks[3].usage.metadata.get("cost_usd") is None
    assert chunks[3].usage.metadata.get("cost_breakdown") == {}


@pytest.mark.asyncio
async def test_complete_stream_preserves_thinking_block_events(monkeypatch):
    """Thinking deltas must surface as distinct content chunks so consumers
    can filter them — they shouldn't be silently swallowed."""
    records = [
        (
            b"event: content_block_start\n"
            b'data: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"thinking","thinking":""}}\n\n'
        ),
        (
            b"event: content_block_delta\n"
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"thinking_delta","thinking":"Let me think..."}}\n\n'
        ),
        (b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'),
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]

    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: None,
        AsyncClient=lambda **kwargs: _FakeAsyncClient(records=records, **kwargs),
        Limits=lambda **kwargs: None,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)

    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-sonnet-4-6")

    chunks = []
    async for chunk in client.complete_stream(messages=[{"role": "user", "content": "hi"}]):
        chunks.append(chunk)

    types_seen = [c.type for c in chunks]
    # The thinking lifecycle events must pass through (no silent swallow)
    assert types_seen == [
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_stop",
    ]
    # The start chunk's content carries the thinking-block shape
    assert chunks[0].content["type"] == "thinking"
    # The delta chunk carries the thinking_delta shape
    assert chunks[1].delta["type"] == "thinking_delta"
    assert chunks[1].delta["thinking"] == "Let me think..."
