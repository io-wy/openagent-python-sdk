"""Deterministic mock LLM provider for local development/tests."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, AsyncIterator

from openagents.llm.base import (
    LLMChunk,
    LLMClient,
    LLMResponse,
    LLMToolCall,
    LLMUsage,
)

if TYPE_CHECKING:
    from openagents.config.schema import LLMPricing


def _parse_structured_output(
    output_text: str,
    response_format: dict[str, Any] | None,
) -> Any:
    if not isinstance(response_format, dict):
        return None
    format_type = str(response_format.get("type", "")).strip().lower()
    if format_type not in {"json", "json_object", "json_schema"}:
        return None
    try:
        return json.loads(output_text)
    except (TypeError, json.JSONDecodeError):
        return None


class MockLLMClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        pricing: "LLMPricing | None" = None,
    ) -> None:
        _ = api_key  # mock ignores credentials
        self.provider_name = "mock"
        self.model_id = model or ""
        self._pricing_overrides = pricing

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> str:
        _ = (model, temperature, max_tokens, tools, tool_choice)
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                break

        parsed = self._parse_prompt(user_text)
        input_text = parsed.get("input", "")
        history_count = parsed.get("history_count", 0)

        if input_text.startswith("/tool"):
            rest = input_text[len("/tool") :].strip()
            if not rest:
                return json.dumps(
                    {"type": "final", "content": "Usage: /tool <tool_id> <query>"},
                    ensure_ascii=True,
                )
            parts = rest.split(maxsplit=1)
            tool_id = parts[0]
            query = parts[1] if len(parts) == 2 else ""
            return json.dumps(
                {"type": "tool_call", "tool": tool_id, "params": {"query": query}},
                ensure_ascii=True,
            )

        return json.dumps(
            {
                "type": "final",
                "content": f"Echo: {input_text} (history={history_count})",
            },
            ensure_ascii=True,
        )

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
        output_text = await self.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )
        # Try to emit a tool call when the /tool directive hits a real tool.
        tool_calls: list[LLMToolCall] = []
        stop_reason = "end_turn"
        parsed_json = _parse_structured_output(output_text, {"type": "json_object"})
        if isinstance(parsed_json, dict) and parsed_json.get("type") == "tool_call" and tools:
            tool_id = str(parsed_json.get("tool", ""))
            tool_names = {str(t.get("name")) for t in tools if isinstance(t, dict)}
            if tool_id and tool_id in tool_names:
                params = parsed_json.get("params") or {}
                if not isinstance(params, dict):
                    params = {}
                tool_calls.append(
                    LLMToolCall(
                        id=f"mock_{tool_id}",
                        name=tool_id,
                        arguments=dict(params),
                        raw_arguments=json.dumps(params, ensure_ascii=True),
                        type="tool_use",
                    )
                )
                stop_reason = "tool_use"

        structured_output = _parse_structured_output(output_text, response_format)
        usage = self._build_usage(messages=messages, output_text=output_text)
        response_id = "mock-" + hashlib.sha256(output_text.encode("utf-8")).hexdigest()[:12]
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": output_text}] if output_text else []
        result = LLMResponse(
            output_text=output_text,
            content=content_blocks,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
            structured_output=structured_output,
            model=self.model_id or (model or ""),
            provider="mock",
            response_id=response_id,
            raw={"output_text": output_text},
        )
        return self._store_response(result)

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
        response = await self.generate(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
        )
        if response.output_text:
            yield LLMChunk(
                type="content_block_delta",
                delta={"type": "text_delta", "text": response.output_text},
                content={"type": "text", "text": response.output_text},
            )
        yield LLMChunk(
            type="message_stop",
            content={"stop_reason": response.stop_reason or "end_turn"},
            usage=response.usage,
        )

    def _build_usage(
        self,
        *,
        messages: list[dict[str, Any]],
        output_text: str,
    ) -> LLMUsage:
        input_text = ""
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                input_text += content
        input_tokens = max(1, len(input_text) // 4) if input_text else 0
        output_tokens = max(1, len(output_text) // 4) if output_text else 0
        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            metadata={},
        )

    def _parse_prompt(self, text: str) -> dict[str, Any]:
        values: dict[str, Any] = {}
        in_history = False
        history_lines = []

        for line in text.splitlines():
            if line.startswith("CONVERSATION_HISTORY:") or line.startswith("HISTORY:"):
                in_history = True
                continue
            elif line.startswith("INPUT:") or line.startswith("AVAILABLE_TOOLS:"):
                in_history = False

            if in_history:
                if line.strip() and not line.startswith(" "):
                    # This is a new history entry marker
                    pass
                history_lines.append(line)
            elif line.startswith("INPUT:"):
                values["input"] = line[len("INPUT:") :].strip()
            elif line.startswith("HISTORY_COUNT:"):
                raw = line[len("HISTORY_COUNT:") :].strip()
                try:
                    values["history_count"] = int(raw)
                except ValueError:
                    values["history_count"] = 0

        # Count history items by counting "User:" markers (each user message = 1 history entry)
        history_count = sum(1 for line in history_lines if line.strip().startswith("User:"))
        values.setdefault("input", "")
        values.setdefault("history_count", history_count)
        return values


# Alias for callers that prefer the shorter name.
MockClient = MockLLMClient
