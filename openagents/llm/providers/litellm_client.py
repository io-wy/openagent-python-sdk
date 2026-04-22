"""LiteLLM-backed LLM provider for non-OpenAI protocol backends.

Wraps ``litellm.acompletion`` with the SDK's ``LLMClient`` contract. Covers
AWS Bedrock, Google Vertex AI, Gemini native, Cohere, Azure deployment, and
any other backend LiteLLM supports through ``<prefix>/<model>`` identifiers.

Instantiating this client has process-global side effects: it sets
``litellm.telemetry = False``, clears ``litellm.success_callback`` and
``litellm.failure_callback``, and sets ``litellm.drop_params = True``.
"""

from __future__ import annotations

import json as _json
import logging
import os
from typing import TYPE_CHECKING, Any

from openagents.errors.exceptions import (
    ConfigError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
)
from openagents.llm.base import (
    LLMChunk,
    LLMClient,
    LLMResponse,
    LLMToolCall,
    LLMUsage,
    _parse_structured_output,
)

if TYPE_CHECKING:
    from openagents.config.schema import LLMPricing, LLMRetryOptions

try:
    import litellm  # type: ignore
except ImportError:  # pragma: no cover
    litellm = None

logger = logging.getLogger("openagents.llm.providers.litellm")


_FORWARDABLE_KWARGS: frozenset[str] = frozenset(
    {
        "aws_region_name",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "aws_profile_name",
        "vertex_project",
        "vertex_location",
        "vertex_credentials",
        "azure_deployment",
        "api_version",
        "seed",
        "top_p",
        "parallel_tool_calls",
        "response_format",
    }
)


def _build_retry_policy_kwargs(retry_options: "LLMRetryOptions | None") -> dict[str, Any]:
    """Translate SDK ``LLMRetryOptions`` → LiteLLM kwargs (single direction).

    - ``max_attempts - 1`` → ``num_retries``
    - When ``retry_on_connection_errors`` is True and ``num_retries > 0``, add
      a structured ``litellm.RetryPolicy`` limiting retries to Timeout +
      RateLimit categories.
    """
    if retry_options is None:
        return {}
    num_retries = max(int(retry_options.max_attempts) - 1, 0)
    kwargs: dict[str, Any] = {"num_retries": num_retries}
    if retry_options.retry_on_connection_errors and num_retries > 0:
        kwargs["retry_policy"] = litellm.RetryPolicy(
            TimeoutErrorRetries=num_retries,
            RateLimitErrorRetries=num_retries,
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            ContentPolicyViolationErrorRetries=0,
        )
    return kwargs


def _derive_provider_name(model: str) -> str:
    if not model or "/" not in model:
        return "litellm"
    prefix = model.split("/", 1)[0].strip()
    return f"litellm:{prefix}" if prefix else "litellm"


def _extract_cached_tokens(usage_obj: Any) -> int:
    """Read prompt-cache tokens from both OpenAI-style and Anthropic-style fields.

    Anthropic-style ``cache_read_input_tokens`` wins when present and non-zero;
    otherwise fall back to OpenAI-style ``prompt_tokens_details.cached_tokens``.
    """
    anthropic_style = getattr(usage_obj, "cache_read_input_tokens", None)
    if isinstance(anthropic_style, int) and anthropic_style > 0:
        return anthropic_style
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if isinstance(cached, int) and cached > 0:
            return cached
    return 0


_STREAM_ERROR_TYPE_BY_NAME: dict[str, str] = {
    "RateLimitError": "rate_limit",
    "APIConnectionError": "connection",
    "Timeout": "connection",
    "APIError": "response",
}


def _classify_litellm_error_type(exc: BaseException) -> str:
    name = type(exc).__name__
    if type(exc).__module__.startswith("litellm"):
        return _STREAM_ERROR_TYPE_BY_NAME.get(name, "response")
    return "unknown"


def _map_litellm_exception(exc: BaseException) -> Exception:
    """Map ``litellm.exceptions.*`` to the SDK's typed error hierarchy.

    Returns ``exc`` unchanged for non-LiteLLM exceptions so callers can
    decide to re-raise or wrap.
    """
    if not type(exc).__module__.startswith("litellm"):
        return exc
    name = type(exc).__name__
    if name == "RateLimitError":
        return LLMRateLimitError(str(exc))
    if name in ("APIConnectionError", "Timeout"):
        return LLMConnectionError(str(exc))
    # APIError and subclasses, plus any unclassified litellm exception
    return LLMResponseError(str(exc))


def _parse_tool_calls(raw: Any) -> list[LLMToolCall]:
    if not raw:
        return []
    out: list[LLMToolCall] = []
    for tc in raw:
        fn = getattr(tc, "function", None)
        if fn is None and isinstance(tc, dict):
            fn = tc.get("function")
        if fn is None:
            continue
        name = getattr(fn, "name", None)
        if name is None and isinstance(fn, dict):
            name = fn.get("name")
        name = name or ""
        args_raw = getattr(fn, "arguments", None)
        if args_raw is None and isinstance(fn, dict):
            args_raw = fn.get("arguments")
        tc_id = getattr(tc, "id", None)
        if tc_id is None and isinstance(tc, dict):
            tc_id = tc.get("id")
        args_str = args_raw if isinstance(args_raw, str) else _json.dumps(args_raw or {})
        try:
            args_dict = _json.loads(args_str) if args_str else {}
            if not isinstance(args_dict, dict):
                args_dict = {}
        except (TypeError, _json.JSONDecodeError):
            args_dict = {}
        out.append(LLMToolCall(name=name, arguments=args_dict, id=tc_id, raw_arguments=args_str))
    return out


class LiteLLMClient(LLMClient):
    """LiteLLM-backed ``LLMClient``. See module docstring."""

    def __init__(
        self,
        *,
        model: str,
        api_base: str | None = None,
        api_key_env: str | None = None,
        timeout_ms: int = 30000,
        default_temperature: float | None = None,
        max_tokens: int = 1024,
        pricing: "LLMPricing | None" = None,
        retry_options: "LLMRetryOptions | None" = None,
        extra_headers: dict[str, str] | None = None,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if litellm is None:
            raise ConfigError("provider 'litellm' requires: pip install 'io-openagent-sdk[litellm]'")

        # Process-level telemetry/callbacks lockdown. Idempotent.
        litellm.telemetry = False
        litellm.success_callback = []
        litellm.failure_callback = []
        litellm.drop_params = True

        self.model_id = model or ""
        self.provider_name = _derive_provider_name(self.model_id)

        self._api_base = api_base
        self._api_key_env = api_key_env
        self._timeout_s = max(timeout_ms / 1000.0, 0.1)
        self._default_temperature = default_temperature
        self._max_tokens = max_tokens
        self._pricing = pricing
        self._retry_options = retry_options
        self._extra_headers = dict(extra_headers) if extra_headers else None
        self._extra_kwargs = dict(extra_kwargs) if extra_kwargs else {}

        # Pricing overrides on base class so _compute_cost_for picks them up.
        if pricing is not None:
            self.price_per_mtok_input = pricing.input
            self.price_per_mtok_output = pricing.output
            self.price_per_mtok_cached_read = pricing.cached_read
            self.price_per_mtok_cached_write = pricing.cached_write

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
        kwargs = self._build_kwargs(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stream=False,
        )
        try:
            raw = await litellm.acompletion(**kwargs)
        except Exception as exc:
            mapped = _map_litellm_exception(exc)
            if mapped is exc:
                raise
            raise mapped from exc
        return self._to_llm_response(raw, response_format=response_format)

    def _build_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        response_format: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        effective_model = model or self.model_id
        effective_temp = temperature if temperature is not None else self._default_temperature
        effective_max = max_tokens if max_tokens is not None else self._max_tokens

        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "max_tokens": effective_max,
            "timeout": self._timeout_s,
            "stream": stream,
        }
        if effective_temp is not None:
            kwargs["temperature"] = effective_temp
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._extra_headers:
            kwargs["extra_headers"] = self._extra_headers
        api_key = self._resolve_api_key()
        if api_key is not None:
            kwargs["api_key"] = api_key
        kwargs.update(self._extra_kwargs)
        kwargs.update(_build_retry_policy_kwargs(self._retry_options))
        return kwargs

    def _resolve_api_key(self) -> str | None:
        if not self._api_key_env:
            return None
        return os.environ.get(self._api_key_env) or None

    def _to_llm_response(
        self,
        raw: Any,
        *,
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        choice = raw.choices[0]
        message = choice.message
        text = getattr(message, "content", None) or ""
        tool_calls = _parse_tool_calls(getattr(message, "tool_calls", None))

        usage_obj = getattr(raw, "usage", None)
        if usage_obj is not None:
            usage = LLMUsage(
                input_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
                metadata={"cache_read_input_tokens": _extract_cached_tokens(usage_obj)},
            ).normalized()
        else:
            usage = LLMUsage().normalized()
        usage = self._compute_cost_for(usage=usage, overrides=self._pricing)

        dump = raw.model_dump() if hasattr(raw, "model_dump") else None

        response = LLMResponse(
            output_text=text,
            content=[{"type": "text", "text": text}] if text else [],
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=getattr(choice, "finish_reason", None),
            structured_output=_parse_structured_output(text, response_format),
            model=getattr(raw, "model", self.model_id),
            provider=self.provider_name,
            response_id=getattr(raw, "id", None),
            raw=dump,
        )
        return self._store_response(response)

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
    ):
        kwargs = self._build_kwargs(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stream=True,
        )
        kwargs.setdefault("stream_options", {"include_usage": True})

        try:
            stream = await litellm.acompletion(**kwargs)
        except Exception as exc:
            yield LLMChunk(
                type="error",
                error=str(exc),
                error_type=_classify_litellm_error_type(exc),
            )
            return

        last_usage: LLMUsage | None = None
        try:
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or [None]
                choice = choices[0]
                if choice is not None:
                    delta = getattr(choice, "delta", None)
                    if delta is not None:
                        content_piece = getattr(delta, "content", None)
                        if content_piece:
                            yield LLMChunk(type="content_block_delta", delta=content_piece)
                        tool_deltas = getattr(delta, "tool_calls", None) or []
                        for td in tool_deltas:
                            fn = getattr(td, "function", None)
                            name = getattr(fn, "name", None) if fn else None
                            args_delta = getattr(fn, "arguments", None) if fn else None
                            yield LLMChunk(
                                type="content_block_delta",
                                delta={
                                    "tool_use": {
                                        "index": getattr(td, "index", None),
                                        "id": getattr(td, "id", None),
                                        "name": name,
                                        "arguments_delta": args_delta or "",
                                    }
                                },
                            )
                usage_obj = getattr(chunk, "usage", None)
                if usage_obj is not None:
                    last_usage = LLMUsage(
                        input_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                        output_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
                        total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
                        metadata={"cache_read_input_tokens": _extract_cached_tokens(usage_obj)},
                    ).normalized()
        except Exception as exc:
            yield LLMChunk(
                type="error",
                error=str(exc),
                error_type=_classify_litellm_error_type(exc),
            )
            return

        yield LLMChunk(type="message_stop", usage=last_usage)

    def count_tokens(self, text: str) -> int:
        try:
            return int(litellm.token_counter(model=self.model_id, text=text or ""))
        except Exception:
            return super().count_tokens(text or "")

    async def aclose(self) -> None:
        session = getattr(litellm, "aclient_session", None) if litellm else None
        if session is None:
            return
        try:
            await session.aclose()
        except Exception:  # pragma: no cover - defensive
            pass
