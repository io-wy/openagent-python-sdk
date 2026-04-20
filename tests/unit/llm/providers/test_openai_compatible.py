from __future__ import annotations

import json
import types

import pytest

from openagents.llm.providers import _http_base as http_base_module
from openagents.llm.providers.openai_compatible import (
    OpenAICompatibleClient,
    _is_reasoning_model,
    _normalize_responses_usage,
    _parse_responses_output,
    _response_format_to_responses_text,
)


class _FakeResponse:
    def __init__(
        self, *, status_code: int = 200, json_data: dict | None = None, records: list[bytes] | None = None
    ) -> None:
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
    def __init__(self, *, response: _FakeResponse, stream_response: _FakeResponse | None = None, **kwargs) -> None:
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


@pytest.mark.asyncio
async def test_generate_passes_tools_and_parses_tool_calls_and_usage(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "chatcmpl_123",
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "I'll search that.",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "search",
                                    "arguments": '{"query":"weather"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        }
    )
    fake_client = _install_fake_httpx(monkeypatch, response=response)
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-4o-mini")

    result = await client.generate(
        messages=[{"role": "user", "content": "search weather"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "search"}},
    )

    assert fake_client.requests[0]["url"] == "https://api.openai.com/v1/chat/completions"
    payload = fake_client.requests[0]["json"]
    assert payload["tools"][0]["function"]["name"] == "search"
    assert payload["tool_choice"] == {"type": "function", "function": {"name": "search"}}
    assert result.output_text == "I'll search that."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"query": "weather"}
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.usage.total_tokens == 18
    assert result.usage.metadata.get("cost_usd") is not None
    assert "cost_breakdown" in result.usage.metadata


@pytest.mark.asyncio
async def test_generate_parses_structured_output_from_response_format(monkeypatch):
    response = _FakeResponse(
        json_data={
            "id": "chatcmpl_456",
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"city":"Shanghai","unit":"celsius"}',
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 9,
                "completion_tokens": 6,
                "total_tokens": 15,
            },
        }
    )
    fake_client = _install_fake_httpx(monkeypatch, response=response)
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-4o-mini")

    result = await client.generate(
        messages=[{"role": "user", "content": "Return JSON"}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "weather",
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

    assert fake_client.requests[0]["json"]["response_format"]["type"] == "json_schema"
    assert result.output_text == '{"city":"Shanghai","unit":"celsius"}'
    assert result.structured_output == {"city": "Shanghai", "unit": "celsius"}


@pytest.mark.asyncio
async def test_complete_stream_emits_tool_use_chunks_and_usage(monkeypatch):
    stream_response = _FakeResponse(
        records=[
            (
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",'
                b'"function":{"name":"read"}}]}}]}\n\n'
            ),
            (
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":'
                b'"{\\"path\\":\\"README.md\\"}"}}]}}]}\n\n'
            ),
            b'data: {"choices":[{"finish_reason":"tool_calls"}]}\n\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13}}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    _install_fake_httpx(monkeypatch, response=_FakeResponse(json_data={}), stream_response=stream_response)
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-4o-mini")

    chunks = []
    async for chunk in client.complete_stream(
        messages=[{"role": "user", "content": "read the readme"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ],
    ):
        chunks.append(chunk)

    assert [chunk.type for chunk in chunks] == [
        "content_block_start",
        "content_block_delta",
        "message_stop",
    ]
    assert chunks[0].content == {"type": "tool_use", "id": "call_1", "name": "read"}
    assert chunks[1].delta == {"type": "input_json_delta", "partial_json": '{"path":"README.md"}'}
    assert chunks[2].content == {"stop_reason": "tool_use"}
    assert chunks[2].usage.input_tokens == 9
    assert chunks[2].usage.output_tokens == 4
    assert chunks[2].usage.total_tokens == 13
    assert chunks[2].usage.metadata.get("cost_usd") is not None


# ---------------------------------------------------------------------------
# Phase C: reasoning-model handling, reasoning tokens, seed/top_p/parallel,
# finish_reason unification
# ---------------------------------------------------------------------------


def test_is_reasoning_model_regex_matches_o_families():
    assert _is_reasoning_model("o1", opt_in=None) is True
    assert _is_reasoning_model("o1-mini", opt_in=None) is True
    assert _is_reasoning_model("o3", opt_in=None) is True
    assert _is_reasoning_model("o3-mini", opt_in=None) is True
    assert _is_reasoning_model("o4-preview", opt_in=None) is True
    assert _is_reasoning_model("gpt-5-thinking", opt_in=None) is True
    assert _is_reasoning_model("gpt-5-thinking-fast", opt_in=None) is True


def test_is_reasoning_model_regex_rejects_non_reasoning_families():
    assert _is_reasoning_model("gpt-4o", opt_in=None) is False
    assert _is_reasoning_model("gpt-4o-mini", opt_in=None) is False
    assert _is_reasoning_model("claude-sonnet-4-6", opt_in=None) is False
    assert _is_reasoning_model("", opt_in=None) is False


def test_is_reasoning_model_opt_in_overrides_regex():
    # opt_in=True forces reasoning even for non-matching model
    assert _is_reasoning_model("custom-reasoner", opt_in=True) is True
    # opt_in=False forces off even when regex would match
    assert _is_reasoning_model("o3-custom-ft", opt_in=False) is False


def test_reasoning_model_uses_max_completion_tokens_and_drops_temperature():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="o3-mini",
        default_temperature=0.7,
    )
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=500,
    )
    assert payload["max_completion_tokens"] == 500
    assert "max_tokens" not in payload
    assert "temperature" not in payload


def test_non_reasoning_model_preserves_max_tokens_and_temperature():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
        default_temperature=0.5,
    )
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=200,
    )
    assert payload["max_tokens"] == 200
    assert payload["temperature"] == 0.5
    assert "max_completion_tokens" not in payload


def test_reasoning_model_opt_in_overrides_regex_at_payload_level():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="custom-reasoner",
        reasoning_model=True,
        default_temperature=0.3,
    )
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1000,
    )
    assert payload["max_completion_tokens"] == 1000
    assert "temperature" not in payload


def test_reasoning_model_opt_out_overrides_regex_at_payload_level():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="o3-custom-fine-tune",
        reasoning_model=False,
        default_temperature=0.7,
    )
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=300,
    )
    assert payload["max_tokens"] == 300
    assert payload["temperature"] == 0.7
    assert "max_completion_tokens" not in payload


def test_seed_top_p_parallel_tool_calls_forwarded_to_payload():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
        seed=42,
        top_p=0.9,
        parallel_tool_calls=False,
    )
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
    )
    assert payload["seed"] == 42
    assert payload["top_p"] == 0.9
    assert payload["parallel_tool_calls"] is False


def test_response_format_json_schema_strict_preserved():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
    )
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "X",
            "schema": {"type": "object"},
            "strict": True,
        },
    }
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
        response_format=rf,
    )
    assert payload["response_format"] == rf
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_reasoning_tokens_parsed_into_metadata():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="o3-mini",
    )
    usage = client._normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 120,
            "completion_tokens_details": {"reasoning_tokens": 90},
        }
    )
    assert usage.input_tokens == 10
    # reasoning tokens are NOT added to output_tokens (they're already in completion_tokens)
    assert usage.output_tokens == 120
    assert usage.metadata["reasoning_tokens"] == 90


def test_reasoning_tokens_missing_is_tolerated():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="o3-mini",
    )
    usage = client._normalize_usage({"prompt_tokens": 10, "completion_tokens": 50})
    assert "reasoning_tokens" not in usage.metadata


def test_reasoning_tokens_does_not_collide_with_cached_tokens():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="o3-mini",
    )
    usage = client._normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 5},
            "completion_tokens_details": {"reasoning_tokens": 90},
        }
    )
    assert usage.metadata["cached_tokens"] == 5
    assert usage.metadata["reasoning_tokens"] == 90


@pytest.mark.asyncio
async def test_generate_maps_tool_calls_finish_reason_to_tool_use(monkeypatch):
    from tests.unit.llm.providers.test_openai_compatible import (
        _FakeResponse as _TFR,
    )
    from tests.unit.llm.providers.test_openai_compatible import (
        _install_fake_httpx as _install,
    )

    resp = _TFR(
        status_code=200,
        json_data={
            "id": "chat_1",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path":"x"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        },
    )
    _install(monkeypatch, response=resp)
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-4o")

    result = await client.generate(messages=[{"role": "user", "content": "go"}])
    # tool_calls finish_reason must map to the unified "tool_use" label
    assert result.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_generate_preserves_length_finish_reason_unchanged(monkeypatch):
    from tests.unit.llm.providers.test_openai_compatible import (
        _FakeResponse as _TFR,
    )
    from tests.unit.llm.providers.test_openai_compatible import (
        _install_fake_httpx as _install,
    )

    resp = _TFR(
        status_code=200,
        json_data={
            "id": "chat_2",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "truncated..."},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        },
    )
    _install(monkeypatch, response=resp)
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-4o")

    result = await client.generate(messages=[{"role": "user", "content": "go"}])
    assert result.stop_reason == "length"


def test_extra_headers_merge_in_openai_compatible(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
        extra_headers={"X-Trace": "on", "User-Agent": "my-app"},
    )
    merged = client._merge_headers(client._build_headers())
    assert merged["X-Trace"] == "on"
    assert merged["User-Agent"] == "my-app"
    assert merged["Authorization"] == "Bearer sk-real"


# ---------------------------------------------------------------------------
# Responses API (v2) support
# ---------------------------------------------------------------------------


def test_api_style_defaults_to_chat_completions():
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-4o")
    assert client.api_style == "chat_completions"


def test_api_style_autodetected_responses_from_suffix():
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1/responses", model="gpt-5")
    assert client.api_style == "responses"


def test_api_style_explicit_overrides_autodetect():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1/responses",
        model="gpt-5",
        api_style="chat_completions",
    )
    assert client.api_style == "chat_completions"


def test_response_format_translation_json_schema_flattens_wrapper():
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "Weather",
            "schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            "strict": True,
            "description": "Weather report",
        },
    }
    out = _response_format_to_responses_text(rf)
    assert out == {
        "type": "json_schema",
        "name": "Weather",
        "schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        "strict": True,
        "description": "Weather report",
    }


def test_response_format_translation_json_object_passthrough():
    assert _response_format_to_responses_text({"type": "json_object"}) == {"type": "json_object"}


def test_response_format_translation_text_passthrough():
    assert _response_format_to_responses_text({"type": "text"}) == {"type": "text"}


def test_response_format_translation_none_and_unknown():
    assert _response_format_to_responses_text(None) is None
    assert _response_format_to_responses_text({"type": "gibberish"}) is None


def test_responses_endpoint_routing():
    c1 = OpenAICompatibleClient(api_base="https://api.openai.com", model="gpt-5", api_style="responses")
    assert c1._responses_endpoint() == "https://api.openai.com/v1/responses"
    c2 = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-5", api_style="responses")
    assert c2._responses_endpoint() == "https://api.openai.com/v1/responses"
    c3 = OpenAICompatibleClient(api_base="https://api.openai.com/v1/responses", model="gpt-5")
    assert c3._responses_endpoint() == "https://api.openai.com/v1/responses"


def test_endpoint_for_style_picks_chat_by_default():
    c = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-4o")
    assert c._endpoint_for_style().endswith("/chat/completions")


def test_endpoint_for_style_picks_responses_when_style_is_responses():
    c = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-5", api_style="responses")
    assert c._endpoint_for_style().endswith("/responses")


def test_responses_payload_splits_system_to_instructions():
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-5", api_style="responses")
    payload = client._build_payload(
        messages=[
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi"},
        ],
        max_tokens=500,
    )
    assert "messages" not in payload  # responses API doesn't use messages
    assert payload["input"] == [{"role": "user", "content": "hi"}]
    assert payload["instructions"] == "be helpful"
    assert payload["max_output_tokens"] == 500
    assert "max_tokens" not in payload


def test_responses_payload_concatenates_multiple_system_messages():
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-5", api_style="responses")
    payload = client._build_payload(
        messages=[
            {"role": "system", "content": "sys1"},
            {"role": "system", "content": "sys2"},
            {"role": "user", "content": "hi"},
        ],
    )
    assert payload["instructions"] == "sys1\nsys2"


def test_responses_payload_extracts_text_blocks_from_list_system():
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-5", api_style="responses")
    payload = client._build_payload(
        messages=[
            {"role": "system", "content": [{"type": "text", "text": "block sys"}]},
            {"role": "user", "content": "hi"},
        ],
    )
    assert payload["instructions"] == "block sys"


def test_responses_payload_response_format_lands_in_text_format():
    client = OpenAICompatibleClient(api_base="https://api.openai.com/v1", model="gpt-5", api_style="responses")
    rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "X",
            "schema": {"type": "object"},
            "strict": True,
        },
    }
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
        response_format=rf,
    )
    # Responses API uses text.format with FLATTENED fields (no json_schema nest)
    assert payload["text"] == {
        "format": {
            "type": "json_schema",
            "name": "X",
            "schema": {"type": "object"},
            "strict": True,
        }
    }
    assert "response_format" not in payload


def test_responses_payload_reasoning_model_drops_temperature_keeps_max_output_tokens():
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="o3-mini",
        api_style="responses",
        default_temperature=0.5,
    )
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1000,
    )
    assert "temperature" not in payload
    # Responses API uses max_output_tokens even for reasoning models
    assert payload["max_output_tokens"] == 1000
    assert "max_completion_tokens" not in payload


def test_parse_responses_output_extracts_message_text():
    data = {
        "id": "resp_1",
        "model": "gpt-5",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Hello "},
                    {"type": "output_text", "text": "world."},
                ],
            }
        ],
    }
    output_text, content, tool_calls = _parse_responses_output(data)
    assert output_text == "Hello world."
    assert tool_calls == []
    assert len(content) == 1
    assert content[0]["type"] == "message"


def test_parse_responses_output_preserves_reasoning_block_not_in_output_text():
    data = {
        "output": [
            {"type": "reasoning", "content": [{"text": "internal"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Answer"}],
            },
        ]
    }
    output_text, content, _ = _parse_responses_output(data)
    assert output_text == "Answer"
    # Reasoning block is preserved in content
    assert any(b.get("type") == "reasoning" for b in content)


def test_parse_responses_output_parses_function_call_item():
    data = {
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "read",
                "arguments": '{"path": "README.md"}',
            }
        ]
    }
    _, _, tool_calls = _parse_responses_output(data)
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "read"
    assert tool_calls[0].arguments == {"path": "README.md"}
    assert tool_calls[0].id == "call_1"
    assert tool_calls[0].type == "tool_use"


def test_parse_responses_output_falls_back_to_convenience_output_text():
    data = {"output_text": "convenience", "output": []}
    output_text, _, _ = _parse_responses_output(data)
    assert output_text == "convenience"


def test_normalize_responses_usage_parses_reasoning_and_cached_tokens():
    usage = _normalize_responses_usage(
        {
            "input_tokens": 10,
            "output_tokens": 120,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 90},
        }
    )
    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.output_tokens == 120
    assert usage.metadata["cached_tokens"] == 3
    assert usage.metadata["reasoning_tokens"] == 90


def test_normalize_responses_usage_none_for_non_dict():
    assert _normalize_responses_usage(None) is None
    assert _normalize_responses_usage("not a dict") is None


@pytest.mark.asyncio
async def test_generate_via_responses_api_end_to_end(monkeypatch):
    resp = _FakeResponse(
        status_code=200,
        json_data={
            "id": "resp_abc",
            "model": "gpt-5",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello from v2."}],
                }
            ],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 5,
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        },
    )
    fake = _install_fake_httpx(monkeypatch, response=resp)
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="gpt-5",
        api_style="responses",
    )

    result = await client.generate(
        messages=[
            {"role": "system", "content": "be concise"},
            {"role": "user", "content": "say hi"},
        ],
        max_tokens=50,
    )

    # The request went to the /responses endpoint
    assert fake.requests[0]["url"].endswith("/v1/responses")
    # Payload uses input + instructions (not messages)
    payload = fake.requests[0]["json"]
    assert payload["input"] == [{"role": "user", "content": "say hi"}]
    assert payload["instructions"] == "be concise"
    assert payload["max_output_tokens"] == 50
    assert "max_tokens" not in payload
    # Response parsed correctly
    assert result.output_text == "Hello from v2."
    assert result.stop_reason == "end_turn"  # status=completed
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 5
    assert result.usage.metadata["reasoning_tokens"] == 2


@pytest.mark.asyncio
async def test_generate_via_responses_api_with_function_call(monkeypatch):
    resp = _FakeResponse(
        status_code=200,
        json_data={
            "id": "resp_tool",
            "model": "gpt-5",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "search",
                    "arguments": '{"q":"weather"}',
                }
            ],
            "usage": {"input_tokens": 8, "output_tokens": 3},
        },
    )
    _install_fake_httpx(monkeypatch, response=resp)
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="gpt-5",
        api_style="responses",
    )

    result = await client.generate(
        messages=[{"role": "user", "content": "search weather"}],
        tools=[{"type": "function", "name": "search"}],
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "weather"}
    assert result.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_complete_stream_responses_falls_back_to_single_delta(monkeypatch):
    resp = _FakeResponse(
        status_code=200,
        json_data={
            "id": "resp_1",
            "model": "gpt-5",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "stream fallback"}],
                }
            ],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    )
    _install_fake_httpx(monkeypatch, response=resp)
    client = OpenAICompatibleClient(
        api_base="https://api.openai.com/v1",
        model="gpt-5",
        api_style="responses",
    )

    chunks = []
    async for chunk in client.complete_stream(messages=[{"role": "user", "content": "go"}]):
        chunks.append(chunk)

    # Responses streaming falls back to non-streaming + single delta + stop
    assert [c.type for c in chunks] == ["content_block_delta", "message_stop"]
    assert chunks[0].delta["text"] == "stream fallback"
    assert chunks[1].content["stop_reason"] == "end_turn"
