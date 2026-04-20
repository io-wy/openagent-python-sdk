from __future__ import annotations

import json
import types

import pytest

from openagents.errors.exceptions import LLMRateLimitError
from openagents.llm.providers import _http_base as http_base_module
from openagents.llm.providers._http_base import _RetryPolicy
from openagents.llm.providers.anthropic import AnthropicClient, _coalesce_system_content


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = json.dumps(self._json_data)
        self.content = self.text.encode("utf-8")

    def json(self) -> dict:
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, *, response: _FakeResponse, **kwargs) -> None:
        self.response = response
        self.kwargs = kwargs
        self.requests: list[dict] = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.response

    async def aclose(self) -> None:
        return None


def _install_fake_httpx(monkeypatch, *, response: _FakeResponse) -> _FakeAsyncClient:
    fake_client = _FakeAsyncClient(response=response)
    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
        Limits=lambda **kwargs: kwargs,
        AsyncClient=lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)
    return fake_client


def test_messages_endpoint_adds_v1_when_base_is_root():
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")
    assert client._messages_endpoint() == "https://api.anthropic.com/v1/messages"


def test_messages_endpoint_preserves_existing_v1_prefix():
    client = AnthropicClient(api_base="https://api.minimaxi.com/anthropic/v1", model="MiniMax-M2.5")
    assert client._messages_endpoint() == "https://api.minimaxi.com/anthropic/v1/messages"


def test_build_payload_uses_x_api_key_without_authorization_header(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = AnthropicClient(api_base="https://api.minimaxi.com/anthropic", model="MiniMax-M2.7")

    _, headers = client._build_payload(
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
    )

    assert headers["x-api-key"] == "test-key"
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_generate_parses_tool_calls_and_usage(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "msg_123",
            "model": "claude-test",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 12, "output_tokens": 5},
            "content": [
                {"type": "text", "text": "I'll inspect that."},
                {"type": "tool_use", "id": "toolu_1", "name": "read", "input": {"path": "README.md"}},
            ],
        }
    )
    fake_client = _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    result = await client.generate(
        messages=[{"role": "user", "content": "read the readme"}],
        tools=[
            {
                "name": "read",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
    )

    assert fake_client.requests[0]["url"] == "https://api.anthropic.com/v1/messages"
    assert result.output_text == "I'll inspect that."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read"
    assert result.tool_calls[0].arguments == {"path": "README.md"}
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens == 17
    assert result.usage.metadata.get("cost_usd") is None
    assert result.usage.metadata.get("cost_breakdown") == {}


@pytest.mark.asyncio
async def test_generate_structured_output_uses_synthetic_tool(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "msg_456",
            "model": "claude-test",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 4},
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_structured",
                    "name": "structured_weather",
                    "input": {"city": "Shanghai", "unit": "celsius"},
                }
            ],
        }
    )
    fake_client = _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    result = await client.generate(
        messages=[{"role": "user", "content": "Return JSON weather data"}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "structured_weather",
                "schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "unit": {"type": "string"},
                    },
                    "required": ["city", "unit"],
                },
            },
        },
    )

    payload = fake_client.requests[0]["json"]
    assert payload["tool_choice"] == {"type": "tool", "name": "structured_weather"}
    assert payload["tools"][0]["name"] == "structured_weather"
    assert result.structured_output == {"city": "Shanghai", "unit": "celsius"}
    assert result.tool_calls == []


# ---------------------------------------------------------------------------
# Phase B: thinking blocks, system list, cache_control, extra_headers, 529
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_preserves_thinking_block_in_content_not_output_text(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "msg_thinking",
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 15},
            "content": [
                {"type": "thinking", "thinking": "Let me reason about this..."},
                {"type": "text", "text": "The answer is 42."},
            ],
        }
    )
    _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-sonnet-4-6")

    result = await client.generate(messages=[{"role": "user", "content": "meaning of life?"}])

    # output_text contains ONLY user-visible text, NOT thinking
    assert result.output_text == "The answer is 42."
    # content preserves both blocks in order
    assert len(result.content) == 2
    assert result.content[0]["type"] == "thinking"
    assert result.content[0]["thinking"] == "Let me reason about this..."
    assert result.content[1]["type"] == "text"


@pytest.mark.asyncio
async def test_generate_preserves_redacted_thinking_block_verbatim(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "msg_redacted",
            "model": "claude-opus-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 8},
            "content": [
                {"type": "redacted_thinking", "data": "encrypted-blob-xyz"},
                {"type": "text", "text": "Done."},
            ],
        }
    )
    _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-opus-4-6")

    result = await client.generate(messages=[{"role": "user", "content": "hi"}])

    assert result.content[0]["type"] == "redacted_thinking"
    assert result.content[0]["data"] == "encrypted-blob-xyz"
    assert result.output_text == "Done."


def test_coalesce_system_content_string_only():
    assert _coalesce_system_content(["you are helpful"]) == "you are helpful"
    assert _coalesce_system_content(["a", "b"]) == "a\nb"


def test_coalesce_system_content_list_only_preserves_cache_control():
    block = {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
    assert _coalesce_system_content([[block]]) == [block]


def test_coalesce_system_content_list_list_concatenates():
    a = {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}}
    b = {"type": "text", "text": "b"}
    assert _coalesce_system_content([[a], [b]]) == [a, b]


def test_coalesce_system_content_mixed_collapses_to_string():
    result = _coalesce_system_content(["s1", [{"type": "text", "text": "s2"}]])
    assert result == "s1\ns2"


def test_coalesce_system_content_empty_returns_empty_string():
    assert _coalesce_system_content([]) == ""
    assert _coalesce_system_content([None, ""]) == ""


@pytest.mark.asyncio
async def test_generate_system_string_roundtrips_to_payload(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "m",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": "ok"}],
        }
    )
    fake = _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    await client.generate(
        messages=[
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ]
    )

    payload = fake.requests[0]["json"]
    assert payload["system"] == "you are helpful"


@pytest.mark.asyncio
async def test_generate_system_list_preserves_cache_control(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "m",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": "ok"}],
        }
    )
    fake = _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    system_blocks = [{"type": "text", "text": "system prompt", "cache_control": {"type": "ephemeral"}}]
    await client.generate(
        messages=[
            {"role": "system", "content": system_blocks},
            {"role": "user", "content": "hi"},
        ]
    )

    payload = fake.requests[0]["json"]
    assert payload["system"] == system_blocks
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_generate_system_mixed_coalesces(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "m",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": "ok"}],
        }
    )
    fake = _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    await client.generate(
        messages=[
            {"role": "system", "content": "first string"},
            {"role": "system", "content": [{"type": "text", "text": "second block"}]},
            {"role": "user", "content": "hi"},
        ]
    )

    payload = fake.requests[0]["json"]
    assert payload["system"] == "first string\nsecond block"


@pytest.mark.asyncio
async def test_generate_tool_cache_control_preserved_in_payload(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "m",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": "ok"}],
        }
    )
    fake = _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    tool_def = {
        "name": "read",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "cache_control": {"type": "ephemeral"},
    }
    await client.generate(
        messages=[{"role": "user", "content": "read README"}],
        tools=[tool_def],
    )

    payload = fake.requests[0]["json"]
    assert payload["tools"][0] == tool_def
    assert payload["tools"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_generate_message_content_cache_control_passes_through(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "m",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "text", "text": "ok"}],
        }
    )
    fake = _install_fake_httpx(monkeypatch, response=response)
    client = AnthropicClient(api_base="https://api.anthropic.com", model="claude-test")

    user_content = [
        {"type": "text", "text": "long context", "cache_control": {"type": "ephemeral"}},
    ]
    await client.generate(messages=[{"role": "user", "content": user_content}])

    payload = fake.requests[0]["json"]
    assert payload["messages"][0]["content"] == user_content
    assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_extra_headers_merge_into_headers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real")
    client = AnthropicClient(
        api_base="https://api.anthropic.com",
        model="claude-test",
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31", "X-Trace": "on"},
    )
    headers = client._merge_headers(client._build_headers())
    assert headers["anthropic-beta"] == "prompt-caching-2024-07-31"
    assert headers["X-Trace"] == "on"
    assert headers["x-api-key"] == "sk-real"
    assert headers["anthropic-version"] == "2023-06-01"  # default preserved


def test_user_anthropic_version_wins_over_default():
    client = AnthropicClient(
        api_base="https://api.anthropic.com",
        model="claude-test",
        extra_headers={"anthropic-version": "2024-10-22"},
    )
    merged = client._merge_headers(client._build_headers())
    assert merged["anthropic-version"] == "2024-10-22"


@pytest.mark.asyncio
async def test_generate_529_overloaded_classified_as_rate_limit_after_retries(
    monkeypatch,
):
    """After retry exhaustion on Anthropic 529 (overloaded), we raise LLMRateLimitError."""

    # Script three 529 responses so retries exhaust
    calls = {"n": 0}

    class _LocalFakeAsync:
        async def request(self, method, url, **kwargs):
            calls["n"] += 1
            return _FakeResponse(status_code=529, json_data={"error": "overloaded"})

        def stream(self, method, url, **kwargs):
            raise NotImplementedError

        async def aclose(self):
            pass

    fake_httpx = types.SimpleNamespace(
        Timeout=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
        Limits=lambda **kwargs: kwargs,
        AsyncClient=lambda **kwargs: _LocalFakeAsync(),
    )
    monkeypatch.setattr(http_base_module, "httpx", fake_httpx)

    # Fake asyncio.sleep so test runs fast
    async def _fast_sleep(delay):
        return None

    monkeypatch.setattr(http_base_module.asyncio, "sleep", _fast_sleep)

    client = AnthropicClient(
        api_base="https://api.anthropic.com",
        model="claude-test",
        retry_policy=_RetryPolicy(max_attempts=3, initial_backoff_ms=1),
    )

    with pytest.raises(LLMRateLimitError) as exc_info:
        await client.generate(messages=[{"role": "user", "content": "hi"}])

    assert calls["n"] == 3
    assert "HTTP 529" in str(exc_info.value)
