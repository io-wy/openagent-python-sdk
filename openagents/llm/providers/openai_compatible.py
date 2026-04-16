"""OpenAI-compatible LLM provider via reusable httpx transport."""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

from openagents.llm.base import LLMChunk, LLMResponse, LLMToolCall, LLMUsage
from openagents.llm.providers._http_base import HTTPProviderClient

try:
    import tiktoken  # type: ignore
except ImportError:  # pragma: no cover
    tiktoken = None


_OPENAI_PRICE_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o":         {"in": 2.50, "out": 10.00, "cached_read": 1.25},
    "gpt-4o-mini":    {"in": 0.15, "out":  0.60, "cached_read": 0.075},
    "o1":             {"in": 15.00, "out": 60.00, "cached_read": 7.50},
}


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)
    if content is None:
        return ""
    return str(content)


def _parse_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_tool_calls(payload: list[Any]) -> list[LLMToolCall]:
    tool_calls: list[LLMToolCall] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        function = item.get("function", {})
        if not isinstance(function, dict):
            function = {}
        arguments_raw = function.get("arguments")
        tool_calls.append(
            LLMToolCall(
                id=item.get("id"),
                name=str(function.get("name", "")),
                arguments=_parse_json_object(arguments_raw),
                raw_arguments=arguments_raw if isinstance(arguments_raw, str) else None,
                type=str(item.get("type", "tool_call")),
            )
        )
    return [tool_call for tool_call in tool_calls if tool_call.name]


def _parse_structured_output(
    output_text: str,
    response_format: dict[str, Any] | None,
) -> Any:
    if not isinstance(response_format, dict):
        return None
    response_type = str(response_format.get("type", "")).strip().lower()
    if response_type not in {"json", "json_object", "json_schema"}:
        return None
    try:
        return json.loads(output_text)
    except json.JSONDecodeError:
        return None


class OpenAICompatibleClient(HTTPProviderClient):
    def __init__(
        self,
        *,
        api_base: str = "https://api.openai.com/v1",
        model: str,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_ms: int = 30000,
        default_temperature: float | None = None,
    ) -> None:
        super().__init__(timeout_ms=timeout_ms)
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.default_temperature = default_temperature

        self.provider_name = "openai_compatible"
        self.model_id = model or ""
        rates = _OPENAI_PRICE_TABLE.get(self.model_id, {})
        self.price_per_mtok_input = rates.get("in")
        self.price_per_mtok_output = rates.get("out")
        self.price_per_mtok_cached_read = rates.get("cached_read")
        # OpenAI has no cache-write concept
        self.price_per_mtok_cached_write = rates.get("cached_write")

    def _normalize_usage(self, raw_usage: dict[str, Any] | None) -> LLMUsage:
        raw = raw_usage or {}
        details = raw.get("prompt_tokens_details") or {}
        meta: dict[str, Any] = {}
        if "cached_tokens" in details:
            meta["cached_tokens"] = int(details["cached_tokens"] or 0)
        input_tokens = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
        output_tokens = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            metadata=meta,
        )

    def count_tokens(self, text: str) -> int:
        if tiktoken is None:
            return super().count_tokens(text)
        try:
            enc = tiktoken.encoding_for_model(self.model_id)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(text or "")))

    def _chat_completions_endpoint(self) -> str:
        if self.api_base.endswith("/chat/completions"):
            return self.api_base
        if self.api_base.endswith("/v1"):
            return f"{self.api_base}/chat/completions"
        return f"{self.api_base}/v1/chat/completions"

    def _build_headers(self) -> dict[str, str]:
        api_key = self.api_key if self.api_key is not None else os.getenv(self.api_key_env, "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _build_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        chosen_model = model or self.model
        chosen_temp = self.default_temperature if temperature is None else temperature

        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
        }
        if chosen_temp is not None:
            payload["temperature"] = chosen_temp
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        response = await self._request(
            "POST",
            self._chat_completions_endpoint(),
            headers=self._build_headers(),
            json_body=self._build_payload(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
            ),
        )
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")

        data = response.json()
        choices = data.get("choices", [])
        choice = choices[0] if choices else {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        output_text = _extract_text_content(message.get("content"))

        raw_usage = data.get("usage")
        normalized_usage = (
            self._normalize_usage(raw_usage).normalized()
            if isinstance(raw_usage, dict)
            else None
        )
        result = LLMResponse(
            output_text=output_text,
            content=message.get("content", []) if isinstance(message.get("content"), list) else [],
            tool_calls=_parse_tool_calls(message.get("tool_calls", [])),
            usage=normalized_usage,
            stop_reason=choice.get("finish_reason"),
            structured_output=_parse_structured_output(output_text, response_format),
            model=data.get("model"),
            provider="openai_compatible",
            response_id=data.get("id"),
            raw=data,
        )
        return self._store_response(result)

    def _parse_sse_record(self, raw: bytes) -> str | None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        data_lines = []
        for line in text.splitlines():
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return None
        return "\n".join(data_lines)

    async def complete_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMChunk]:
        pending_stop_reason: str | None = None
        latest_usage: LLMUsage | None = None
        tool_state: dict[int, dict[str, Any]] = {}

        async with await self._stream(
            "POST",
            self._chat_completions_endpoint(),
            headers=self._build_headers(),
            json_body=self._build_payload(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
            ),
            read_timeout_s=120.0,
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                error_text = body.decode("utf-8", errors="replace")
                yield LLMChunk(type="error", error=f"HTTP {response.status_code}: {error_text[:500]}")
                return

            buffer = b""
            async for chunk in response.aiter_bytes():
                buffer += chunk
                while b"\n\n" in buffer:
                    record, buffer = buffer.split(b"\n\n", 1)
                    data_str = self._parse_sse_record(record)
                    if data_str is None:
                        continue
                    if data_str == "[DONE]":
                        if pending_stop_reason is not None:
                            yield LLMChunk(
                                type="message_stop",
                                content={"stop_reason": pending_stop_reason},
                                usage=latest_usage,
                            )
                            pending_stop_reason = None
                        continue

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    raw_usage = data.get("usage")
                    if isinstance(raw_usage, dict):
                        latest_usage = self._normalize_usage(raw_usage).normalized()

                    choices = data.get("choices", [])
                    for choice in choices:
                        if not isinstance(choice, dict):
                            continue
                        delta = choice.get("delta", {})
                        if not isinstance(delta, dict):
                            delta = {}

                        content_delta = delta.get("content")
                        if isinstance(content_delta, str) and content_delta:
                            yield LLMChunk(
                                type="content_block_delta",
                                delta={"type": "text_delta", "text": content_delta},
                                content={"type": "text", "text": content_delta},
                            )

                        tool_calls = delta.get("tool_calls", [])
                        if isinstance(tool_calls, list):
                            for tool_delta in tool_calls:
                                if not isinstance(tool_delta, dict):
                                    continue
                                index = int(tool_delta.get("index", 0) or 0)
                                current = tool_state.setdefault(index, {})
                                current_id = tool_delta.get("id") or current.get("id")
                                if current_id:
                                    current["id"] = current_id

                                function = tool_delta.get("function", {})
                                if not isinstance(function, dict):
                                    function = {}

                                name = function.get("name")
                                if isinstance(name, str) and name:
                                    current["name"] = name
                                    yield LLMChunk(
                                        type="content_block_start",
                                        content={
                                            "type": "tool_use",
                                            "id": current.get("id"),
                                            "name": name,
                                        },
                                    )

                                arguments = function.get("arguments")
                                if isinstance(arguments, str) and arguments:
                                    yield LLMChunk(
                                        type="content_block_delta",
                                        delta={"type": "input_json_delta", "partial_json": arguments},
                                    )

                        finish_reason = choice.get("finish_reason")
                        if isinstance(finish_reason, str) and finish_reason:
                            pending_stop_reason = (
                                "tool_use" if finish_reason == "tool_calls" else finish_reason
                            )

                    if pending_stop_reason is not None and latest_usage is not None:
                        yield LLMChunk(
                            type="message_stop",
                            content={"stop_reason": pending_stop_reason},
                            usage=latest_usage,
                        )
                        pending_stop_reason = None

            if buffer.strip():
                data_str = self._parse_sse_record(buffer)
                if data_str and data_str != "[DONE]":
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = None
                    if isinstance(data, dict):
                        raw_usage = data.get("usage")
                        if isinstance(raw_usage, dict):
                            latest_usage = self._normalize_usage(raw_usage).normalized()

            if pending_stop_reason is not None:
                yield LLMChunk(
                    type="message_stop",
                    content={"stop_reason": pending_stop_reason},
                    usage=latest_usage,
                )

        self._store_response(
            LLMResponse(
                usage=latest_usage,
                stop_reason=pending_stop_reason,
                provider="openai_compatible",
            )
        )
