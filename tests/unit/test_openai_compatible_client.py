from __future__ import annotations

import json
import types

import pytest

from openagents.llm.base import LLMUsage
from openagents.llm.providers import _http_base as http_base_module
from openagents.llm.providers.openai_compatible import OpenAICompatibleClient


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data: dict | None = None, records: list[bytes] | None = None) -> None:
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


def _install_fake_httpx(monkeypatch, *, response: _FakeResponse, stream_response: _FakeResponse | None = None) -> _FakeAsyncClient:
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
                                    "arguments": "{\"query\":\"weather\"}",
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
                        "content": "{\"city\":\"Shanghai\",\"unit\":\"celsius\"}",
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
    assert result.output_text == "{\"city\":\"Shanghai\",\"unit\":\"celsius\"}"
    assert result.structured_output == {"city": "Shanghai", "unit": "celsius"}


@pytest.mark.asyncio
async def test_complete_stream_emits_tool_use_chunks_and_usage(monkeypatch):
    stream_response = _FakeResponse(
        records=[
            (
                b"data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_1\",\"type\":\"function\","
                b"\"function\":{\"name\":\"read\"}}]}}]}\n\n"
            ),
            (
                b"data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":"
                b"\"{\\\"path\\\":\\\"README.md\\\"}\"}}]}}]}\n\n"
            ),
            b"data: {\"choices\":[{\"finish_reason\":\"tool_calls\"}]}\n\n",
            b"data: {\"choices\":[],\"usage\":{\"prompt_tokens\":9,\"completion_tokens\":4,\"total_tokens\":13}}\n\n",
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
    assert chunks[1].delta == {"type": "input_json_delta", "partial_json": "{\"path\":\"README.md\"}"}
    assert chunks[2].content == {"stop_reason": "tool_use"}
    assert chunks[2].usage.input_tokens == 9
    assert chunks[2].usage.output_tokens == 4
    assert chunks[2].usage.total_tokens == 13
    assert chunks[2].usage.metadata.get("cost_usd") is not None
