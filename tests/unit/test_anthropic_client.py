from __future__ import annotations

import json
import types

import pytest

from openagents.llm.base import LLMUsage
from openagents.llm.providers import _http_base as http_base_module
from openagents.llm.providers.anthropic import AnthropicClient


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
    client = AnthropicClient(api_base='https://api.anthropic.com', model='claude-test')
    assert client._messages_endpoint() == 'https://api.anthropic.com/v1/messages'


def test_messages_endpoint_preserves_existing_v1_prefix():
    client = AnthropicClient(api_base='https://api.minimaxi.com/anthropic/v1', model='MiniMax-M2.5')
    assert client._messages_endpoint() == 'https://api.minimaxi.com/anthropic/v1/messages'


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
