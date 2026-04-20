"""Base LLM client contracts and normalized response models."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

if TYPE_CHECKING:
    from openagents.config.schema import LLMPricing

logger = logging.getLogger("openagents.llm")


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


@dataclass
class LLMUsage:
    """Normalized token usage for one LLM response."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized(self) -> "LLMUsage":
        input_tokens = max(int(self.input_tokens), 0)
        output_tokens = max(int(self.output_tokens), 0)
        total_tokens = max(int(self.total_tokens or (input_tokens + output_tokens)), 0)
        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            metadata=dict(self.metadata),
        )

    def merge(self, other: "LLMUsage | None") -> "LLMUsage":
        if other is None:
            return self.normalized()

        current = self.normalized()
        incoming = other.normalized()
        return LLMUsage(
            input_tokens=incoming.input_tokens or current.input_tokens,
            output_tokens=incoming.output_tokens or current.output_tokens,
            total_tokens=incoming.total_tokens or current.total_tokens,
            metadata={**current.metadata, **incoming.metadata},
        )


@dataclass
class LLMToolCall:
    """Normalized tool call emitted by a provider."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    raw_arguments: str | None = None
    type: str = "tool_call"


@dataclass
class LLMResponse:
    """Normalized non-streaming response from a provider."""

    output_text: str = ""
    content: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: LLMUsage | None = None
    stop_reason: str | None = None
    structured_output: Any = None
    model: str | None = None
    provider: str | None = None
    response_id: str | None = None
    raw: dict[str, Any] | list[Any] | None = None


LLMChunkErrorType = Literal["rate_limit", "connection", "response", "unknown"]


@dataclass
class LLMChunk:
    """Streaming chunk from LLM.

    ``error_type`` classifies streaming failures into the same buckets as the
    non-streaming typed errors (`LLMRateLimitError`, `LLMConnectionError`,
    `LLMResponseError`). It is ``None`` on non-error chunks and ``None`` on
    error chunks whose underlying cause could not be classified (the message
    still lands in ``error``).
    """

    type: str  # "content_block_delta", "message_stop", "error", ...
    delta: dict[str, Any] | str | None = None
    content: dict[str, Any] | None = None
    error: str | None = None
    error_type: LLMChunkErrorType | None = None
    usage: LLMUsage | None = None


@dataclass
class LLMCostBreakdown:
    input: float = 0.0
    output: float = 0.0
    cached_read: float = 0.0
    cached_write: float = 0.0

    @property
    def total(self) -> float:
        return self.input + self.output + self.cached_read + self.cached_write


def compute_cost(
    *,
    input_tokens_non_cached: int,
    output_tokens: int,
    cached_read_tokens: int,
    cached_write_tokens: int,
    rates: "LLMPricing",
) -> LLMCostBreakdown | None:
    """Compute per-call cost. Return None if any required rate is missing."""
    if rates is None:
        return None

    def _rate(value: float | None, tokens: int) -> float | None:
        if tokens <= 0:
            return 0.0
        if value is None:
            return None
        return (tokens / 1_000_000.0) * value

    input_cost = _rate(rates.input, input_tokens_non_cached)
    output_cost = _rate(rates.output, output_tokens)
    cached_read_cost = _rate(rates.cached_read, cached_read_tokens)
    cached_write_cost = _rate(rates.cached_write, cached_write_tokens)

    for part in (input_cost, output_cost, cached_read_cost, cached_write_cost):
        if part is None:
            return None
    return LLMCostBreakdown(
        input=input_cost,
        output=output_cost,
        cached_read=cached_read_cost,
        cached_write=cached_write_cost,
    )


class LLMClient:
    provider_name: str = "unknown"
    model_id: str = "unknown"

    price_per_mtok_input: float | None = None
    price_per_mtok_output: float | None = None
    price_per_mtok_cached_read: float | None = None
    price_per_mtok_cached_write: float | None = None

    def count_tokens(self, text: str) -> int:
        """Approximate token count using a provider-native tokenizer.

        Default: len(text) // 4 with a one-time WARN per client instance.
        Providers override when a real tokenizer is available.
        """
        if not getattr(self, "_count_tokens_warned", False):
            logger.warning(
                "LLMClient.count_tokens fallback (len//4) active for %s/%s; token budgets will be approximate.",
                self.provider_name,
                self.model_id,
            )
            self._count_tokens_warned = True
        return max(1, len(text or "") // 4)

    def _effective_pricing(self, overrides: "LLMPricing | None") -> "LLMPricing":
        from openagents.config.schema import LLMPricing

        merged = LLMPricing(
            input=self.price_per_mtok_input,
            output=self.price_per_mtok_output,
            cached_read=self.price_per_mtok_cached_read,
            cached_write=self.price_per_mtok_cached_write,
        )
        if overrides is None:
            return merged
        for field_name in ("input", "output", "cached_read", "cached_write"):
            value = getattr(overrides, field_name)
            if value is not None:
                setattr(merged, field_name, value)
        return merged

    def _compute_cost_for(
        self,
        *,
        usage: LLMUsage,
        overrides: "LLMPricing | None",
    ) -> LLMUsage:
        """Attach cost_usd and cost_breakdown onto usage.metadata."""
        cached_read = int(
            usage.metadata.get(
                "cache_read_input_tokens",
                usage.metadata.get("cached_tokens", 0),
            )
            or 0
        )
        cached_write = int(usage.metadata.get("cache_creation_input_tokens", 0) or 0)
        non_cached_input = max(0, usage.input_tokens - cached_read - cached_write)
        rates = self._effective_pricing(overrides)
        breakdown = compute_cost(
            input_tokens_non_cached=non_cached_input,
            output_tokens=usage.output_tokens,
            cached_read_tokens=cached_read,
            cached_write_tokens=cached_write,
            rates=rates,
        )
        merged_meta = dict(usage.metadata)
        if breakdown is None:
            merged_meta["cost_usd"] = None
            merged_meta["cost_breakdown"] = {}
        else:
            merged_meta["cost_usd"] = breakdown.total
            merged_meta["cost_breakdown"] = {
                "input": breakdown.input,
                "output": breakdown.output,
                "cached_read": breakdown.cached_read,
                "cached_write": breakdown.cached_write,
            }
        return LLMUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            metadata=merged_meta,
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
        """Generate one normalized response."""
        if type(self).complete is not LLMClient.complete:
            text = await self.complete(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
            )
            response = LLMResponse(
                output_text=text,
                structured_output=_parse_structured_output(text, response_format),
            )
            return self._store_response(response)
        raise NotImplementedError

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Complete a chat request and return text only."""
        if type(self).generate is not LLMClient.generate:
            response = await self.generate(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
            )
            return response.output_text
        raise NotImplementedError

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
        """Complete a chat request with streaming."""
        result = await self.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
        )
        yield LLMChunk(type="content_block_delta", delta=result)
        last_response = self.get_last_response()
        yield LLMChunk(type="message_stop", usage=last_response.usage if last_response else None)

    async def aclose(self) -> None:
        """Close provider resources."""
        return None

    def get_last_response(self) -> LLMResponse | None:
        return getattr(self, "_last_response", None)

    def _store_response(self, response: LLMResponse) -> LLMResponse:
        setattr(self, "_last_response", response)
        return response
