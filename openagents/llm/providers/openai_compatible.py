"""OpenAI-compatible LLM provider via reusable httpx transport."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

from openagents.errors.exceptions import (
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
)
from openagents.llm.base import LLMChunk, LLMResponse, LLMToolCall, LLMUsage
from openagents.llm.providers._http_base import (
    HTTPProviderClient,
    _classify_status,
    _RetryPolicy,
)

if TYPE_CHECKING:
    from openagents.config.schema import LLMPricing

try:
    import tiktoken  # type: ignore
except ImportError:  # pragma: no cover
    tiktoken = None


logger = logging.getLogger("openagents.llm.providers.openai_compatible")


_OPENAI_PRICE_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o": {"in": 2.50, "out": 10.00, "cached_read": 1.25},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60, "cached_read": 0.075},
    "o1": {"in": 15.00, "out": 60.00, "cached_read": 7.50},
}


# Matches reasoning-model families: o1/o3/o4/... (any o<digit>) and gpt-5-thinking*.
_REASONING_MODEL_PATTERN = re.compile(
    r"^(o\d+(?:-.*)?|gpt-5-thinking.*)$",
    re.IGNORECASE,
)


def _is_reasoning_model(model_id: str, *, opt_in: bool | None) -> bool:
    """Return True iff this model should use reasoning-model payload shape.

    Explicit `opt_in` always wins (True or False) over the regex match.
    """
    if opt_in is not None:
        return bool(opt_in)
    if not model_id:
        return False
    return bool(_REASONING_MODEL_PATTERN.match(model_id))


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


def _response_format_to_responses_text(
    response_format: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Translate a Chat-Completions `response_format` to the Responses API `text.format`.

    Chat-Completions shape:
      `{"type": "json_schema", "json_schema": {"name": N, "schema": S, "strict": T}}`
    Responses-API shape (flattened):
      `{"type": "json_schema", "name": N, "schema": S, "strict": T}`

    For `json_object` / `text`, the shape is already compatible — passed through.
    """
    if not isinstance(response_format, dict):
        return None
    rf_type = str(response_format.get("type", "")).strip().lower()
    if rf_type == "json_schema":
        inner = response_format.get("json_schema", {})
        if not isinstance(inner, dict):
            inner = {}
        out: dict[str, Any] = {"type": "json_schema"}
        if "name" in inner:
            out["name"] = inner["name"]
        if "schema" in inner:
            out["schema"] = inner["schema"]
        if "strict" in inner:
            out["strict"] = inner["strict"]
        if "description" in inner:
            out["description"] = inner["description"]
        return out
    if rf_type in {"json_object", "text"}:
        return {"type": rf_type}
    return None


def _parse_responses_output(
    data: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], list[LLMToolCall]]:
    """Parse a Responses API response body into (output_text, content_blocks, tool_calls).

    The `output` field is an array of items. Each item's `type` can be:
    - `"message"`: assistant text reply, with `content` being a list of
      `{"type": "output_text", "text": "..."}` blocks
    - `"reasoning"`: model-internal reasoning block (preserve in content; do NOT
      add to output_text)
    - `"function_call"`: tool call with `call_id`, `name`, `arguments` (JSON string)
    """
    output_parts: list[str] = []
    content_blocks: list[dict[str, Any]] = []
    tool_calls: list[LLMToolCall] = []

    # Convenience field — some providers set it directly; use it as fallback
    convenience_text = data.get("output_text")

    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        content_blocks.append(item)
        if item_type == "message":
            inner = item.get("content", [])
            if isinstance(inner, list):
                for block in inner:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        text = block.get("text")
                        if isinstance(text, str):
                            output_parts.append(text)
        elif item_type == "reasoning":
            # preserve but do NOT contribute to output_text
            continue
        elif item_type == "function_call":
            name = str(item.get("name", ""))
            raw_args = item.get("arguments")
            parsed_args: dict[str, Any] = {}
            raw_str: str | None = None
            if isinstance(raw_args, str) and raw_args.strip():
                raw_str = raw_args
                try:
                    decoded = json.loads(raw_args)
                    if isinstance(decoded, dict):
                        parsed_args = decoded
                except json.JSONDecodeError:
                    parsed_args = {}
            elif isinstance(raw_args, dict):
                parsed_args = raw_args
                raw_str = json.dumps(raw_args, ensure_ascii=False)
            if name:
                tool_calls.append(
                    LLMToolCall(
                        id=item.get("call_id") or item.get("id"),
                        name=name,
                        arguments=parsed_args,
                        raw_arguments=raw_str,
                        type="tool_use",
                    )
                )

    output_text = "".join(output_parts)
    if not output_text and isinstance(convenience_text, str):
        output_text = convenience_text
    return output_text, content_blocks, tool_calls


def _normalize_responses_usage(raw_usage: dict[str, Any] | None) -> LLMUsage | None:
    """Responses API uses `input_tokens`/`output_tokens` (not prompt_/completion_).

    Also `output_tokens_details.reasoning_tokens` for reasoning models.
    """
    if not isinstance(raw_usage, dict):
        return None
    input_tokens = int(raw_usage.get("input_tokens", 0) or 0)
    output_tokens = int(raw_usage.get("output_tokens", 0) or 0)
    meta: dict[str, Any] = {}
    details = raw_usage.get("input_tokens_details") or {}
    if isinstance(details, dict) and "cached_tokens" in details:
        meta["cached_tokens"] = int(details["cached_tokens"] or 0)
    out_details = raw_usage.get("output_tokens_details") or {}
    if isinstance(out_details, dict) and "reasoning_tokens" in out_details:
        meta["reasoning_tokens"] = int(out_details["reasoning_tokens"] or 0)
    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        metadata=meta,
    )


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
        pricing: "LLMPricing | None" = None,
        retry_policy: _RetryPolicy | None = None,
        extra_headers: dict[str, str] | None = None,
        reasoning_model: bool | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        parallel_tool_calls: bool | None = None,
        api_style: Literal["chat_completions", "responses"] | None = None,
    ) -> None:
        super().__init__(
            timeout_ms=timeout_ms,
            retry_policy=retry_policy,
            extra_headers=extra_headers,
        )
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
        self._pricing_overrides = pricing
        self._reasoning_model_opt_in = reasoning_model
        self._default_seed = seed
        self._default_top_p = top_p
        self._default_parallel_tool_calls = parallel_tool_calls
        self._api_style: Literal["chat_completions", "responses"] = self._resolve_api_style(api_style)

    def _resolve_api_style(
        self,
        explicit: Literal["chat_completions", "responses"] | None,
    ) -> Literal["chat_completions", "responses"]:
        """Pick the API style. Explicit wins; otherwise infer from api_base suffix."""
        if explicit is not None:
            return explicit
        tail = self.api_base.rstrip("/").lower()
        if tail.endswith("/responses"):
            return "responses"
        return "chat_completions"

    @property
    def api_style(self) -> Literal["chat_completions", "responses"]:
        return self._api_style

    def _responses_endpoint(self) -> str:
        if self.api_base.endswith("/responses"):
            return self.api_base
        if self.api_base.endswith("/v1"):
            return f"{self.api_base}/responses"
        return f"{self.api_base}/v1/responses"

    def _endpoint_for_style(self) -> str:
        return self._responses_endpoint() if self._api_style == "responses" else self._chat_completions_endpoint()

    def _normalize_usage(self, raw_usage: dict[str, Any] | None) -> LLMUsage:
        raw = raw_usage or {}
        details = raw.get("prompt_tokens_details") or {}
        completion_details = raw.get("completion_tokens_details") or {}
        meta: dict[str, Any] = {}
        if "cached_tokens" in details:
            meta["cached_tokens"] = int(details["cached_tokens"] or 0)
        # Reasoning tokens land in metadata; they are ALREADY included in
        # completion_tokens by the API, so we do NOT add them to output_tokens.
        if "reasoning_tokens" in completion_details:
            meta["reasoning_tokens"] = int(completion_details["reasoning_tokens"] or 0)
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
        if self._api_style == "responses":
            return self._build_payload_responses(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
            )
        return self._build_payload_chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
        )

    def _build_payload_chat(
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
        is_reasoning = _is_reasoning_model(chosen_model or "", opt_in=self._reasoning_model_opt_in)

        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
        }
        # Reasoning models: drop temperature, use max_completion_tokens.
        if is_reasoning:
            if chosen_temp is not None:
                logger.debug(
                    "dropping temperature=%s for reasoning model %r",
                    chosen_temp,
                    chosen_model,
                )
            if max_tokens is not None:
                payload["max_completion_tokens"] = max_tokens
        else:
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
        if self._default_seed is not None:
            payload["seed"] = self._default_seed
        if self._default_top_p is not None:
            payload["top_p"] = self._default_top_p
        if self._default_parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = self._default_parallel_tool_calls
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _build_payload_responses(
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
        """Build payload for the Responses API (OpenAI `/v1/responses`).

        Key differences from Chat Completions:
        - `messages` → split into `instructions` (system role) + `input` (user/assistant roles)
        - `max_tokens` → `max_output_tokens` (even for reasoning models)
        - `response_format` → `text.format` (flattened; no nested `json_schema` wrapper)
        """
        chosen_model = model or self.model
        chosen_temp = self.default_temperature if temperature is None else temperature
        is_reasoning = _is_reasoning_model(chosen_model or "", opt_in=self._reasoning_model_opt_in)

        instructions_parts: list[str] = []
        input_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                if isinstance(content, str):
                    instructions_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            text = block.get("text")
                            if isinstance(text, str):
                                instructions_parts.append(text)
            else:
                input_messages.append({"role": role, "content": content})

        payload: dict[str, Any] = {
            "model": chosen_model,
            "input": input_messages,
        }
        if instructions_parts:
            payload["instructions"] = "\n".join(instructions_parts)

        # Reasoning models: drop temperature, still use max_output_tokens (Responses
        # API doesn't have a separate max_completion_tokens field).
        if is_reasoning and chosen_temp is not None:
            logger.debug(
                "dropping temperature=%s for reasoning model %r (responses API)",
                chosen_temp,
                chosen_model,
            )
        elif chosen_temp is not None:
            payload["temperature"] = chosen_temp

        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens

        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        text_format = _response_format_to_responses_text(response_format)
        if text_format is not None:
            payload["text"] = {"format": text_format}
        if self._default_seed is not None:
            payload["seed"] = self._default_seed
        if self._default_top_p is not None:
            payload["top_p"] = self._default_top_p
        if self._default_parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = self._default_parallel_tool_calls
        if stream:
            payload["stream"] = True
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
        url = self._endpoint_for_style()
        response = await self._request(
            "POST",
            url,
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
        # Non-retryable non-200 surface as typed errors
        self._raise_for_response_status(url=url, response=response)

        data = self._parse_response_json(url=url, response=response)
        if self._api_style == "responses":
            result = self._parse_responses_generate(data=data, response_format=response_format)
        else:
            result = self._parse_chat_generate(data=data, response_format=response_format)
        return self._store_response(result)

    def _parse_chat_generate(
        self,
        *,
        data: dict[str, Any],
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        choices = data.get("choices", [])
        choice = choices[0] if choices else {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        output_text = _extract_text_content(message.get("content"))

        raw_usage = data.get("usage")
        normalized_usage = self._normalize_usage(raw_usage).normalized() if isinstance(raw_usage, dict) else None
        if normalized_usage is not None:
            normalized_usage = self._compute_cost_for(
                usage=normalized_usage,
                overrides=self._pricing_overrides,
            )
        raw_finish = choice.get("finish_reason") if isinstance(choice, dict) else None
        stop_reason = "tool_use" if raw_finish == "tool_calls" else raw_finish
        return LLMResponse(
            output_text=output_text,
            content=message.get("content", []) if isinstance(message.get("content"), list) else [],
            tool_calls=_parse_tool_calls(message.get("tool_calls", [])),
            usage=normalized_usage,
            stop_reason=stop_reason,
            structured_output=_parse_structured_output(output_text, response_format),
            model=data.get("model"),
            provider="openai_compatible",
            response_id=data.get("id"),
            raw=data,
        )

    def _parse_responses_generate(
        self,
        *,
        data: dict[str, Any],
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        output_text, content_blocks, tool_calls = _parse_responses_output(data)

        raw_usage = data.get("usage")
        normalized_usage = _normalize_responses_usage(raw_usage).normalized() if isinstance(raw_usage, dict) else None
        if normalized_usage is not None:
            normalized_usage = self._compute_cost_for(
                usage=normalized_usage,
                overrides=self._pricing_overrides,
            )

        # Derive stop_reason: Responses API uses `status` ("completed", etc.) and
        # the presence of function_call items signals tool_use.
        if tool_calls:
            stop_reason: str | None = "tool_use"
        else:
            status = data.get("status")
            if isinstance(status, str) and status:
                stop_reason = "end_turn" if status == "completed" else status
            else:
                stop_reason = None

        return LLMResponse(
            output_text=output_text,
            content=content_blocks,
            tool_calls=tool_calls,
            usage=normalized_usage,
            stop_reason=stop_reason,
            structured_output=_parse_structured_output(output_text, response_format),
            model=data.get("model"),
            provider="openai_compatible",
            response_id=data.get("id"),
            raw=data,
        )

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
        if self._api_style == "responses":
            # Responses API SSE events have a different shape than Chat Completions.
            # Fall back to non-streaming generate() and emit a single delta+stop.
            # Streaming parity for Responses API is scoped out of this change.
            try:
                result = await self.generate(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                    response_format=response_format,
                )
            except (LLMRateLimitError, LLMConnectionError, LLMResponseError) as exc:
                yield self._yield_stream_error_chunk(exc=exc)
                return
            if result.output_text:
                yield LLMChunk(
                    type="content_block_delta",
                    delta={"type": "text_delta", "text": result.output_text},
                    content={"type": "text", "text": result.output_text},
                )
            yield LLMChunk(
                type="message_stop",
                content={"stop_reason": result.stop_reason or "end_turn"},
                usage=result.usage,
            )
            return

        pending_stop_reason: str | None = None
        latest_usage: LLMUsage | None = None
        tool_state: dict[int, dict[str, Any]] = {}

        stream_url = self._chat_completions_endpoint()
        try:
            response, stream_cm = await self._open_stream(
                "POST",
                stream_url,
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
            )
        except (LLMRateLimitError, LLMConnectionError, LLMResponseError) as exc:
            yield self._yield_stream_error_chunk(exc=exc)
            return

        try:
            # _open_stream either retried to success (200) or raised;
            # any non-200 here is a non-retryable status.
            if response.status_code != 200:
                body = await response.aread()
                error_text = body.decode("utf-8", errors="replace")
                classifier = _classify_status(int(response.status_code), self._retry_policy.retryable_status)
                yield LLMChunk(
                    type="error",
                    error=f"HTTP {response.status_code}: {error_text[:500]}",
                    error_type=classifier,
                )
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
                        latest_usage = self._compute_cost_for(
                            usage=self._normalize_usage(raw_usage).normalized(),
                            overrides=self._pricing_overrides,
                        )

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
                            pending_stop_reason = "tool_use" if finish_reason == "tool_calls" else finish_reason

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
                            latest_usage = self._compute_cost_for(
                                usage=self._normalize_usage(raw_usage).normalized(),
                                overrides=self._pricing_overrides,
                            )

            if pending_stop_reason is not None:
                yield LLMChunk(
                    type="message_stop",
                    content={"stop_reason": pending_stop_reason},
                    usage=latest_usage,
                )
        finally:
            try:
                await stream_cm.__aexit__(None, None, None)
            except Exception:
                pass

        self._store_response(
            LLMResponse(
                usage=latest_usage,
                stop_reason=pending_stop_reason,
                provider="openai_compatible",
            )
        )
