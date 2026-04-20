"""Shared httpx-based transport for LLM providers.

Adds transport-level retry with exponential backoff and classification of
non-200 responses / connection errors into the typed ``LLMError`` hierarchy
defined in ``openagents.errors``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

try:
    import httpx

    _CONNECTION_EXC_TYPES: tuple[type[BaseException], ...] = (
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
    )
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]
    _CONNECTION_EXC_TYPES = ()

from openagents.errors.exceptions import (
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
)
from openagents.llm.base import LLMChunk, LLMChunkErrorType, LLMClient

if TYPE_CHECKING:
    from openagents.config.schema import LLMRetryOptions

logger = logging.getLogger("openagents.llm.providers")


_DEFAULT_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 502, 503, 504})


@dataclass
class _RetryPolicy:
    """Transport-level retry configuration.

    Defaults mirror ``LLMRetryOptions`` defaults. Constructed from
    ``LLMRetryOptions`` via ``from_options`` so every provider gets the same
    behavior when the caller leaves it unset.
    """

    max_attempts: int = 3
    initial_backoff_ms: int = 500
    max_backoff_ms: int = 5000
    backoff_multiplier: float = 2.0
    retry_on_connection_errors: bool = True
    total_budget_ms: int | None = None
    retryable_status: frozenset[int] = field(default_factory=lambda: _DEFAULT_RETRYABLE_STATUS)

    @classmethod
    def from_options(
        cls,
        options: "LLMRetryOptions | None",
        *,
        extra_retryable_status: frozenset[int] | None = None,
    ) -> "_RetryPolicy":
        policy = (
            cls()
            if options is None
            else cls(
                max_attempts=int(options.max_attempts),
                initial_backoff_ms=int(options.initial_backoff_ms),
                max_backoff_ms=int(options.max_backoff_ms),
                backoff_multiplier=float(options.backoff_multiplier),
                retry_on_connection_errors=bool(options.retry_on_connection_errors),
                total_budget_ms=(int(options.total_budget_ms) if options.total_budget_ms is not None else None),
            )
        )
        if extra_retryable_status:
            policy.retryable_status = frozenset(policy.retryable_status | extra_retryable_status)
        return policy

    def backoff_ms(self, attempt: int) -> int:
        """Compute the backoff delay for the given (1-based) attempt index."""
        base = self.initial_backoff_ms * (self.backoff_multiplier ** max(attempt - 1, 0))
        return int(min(base, self.max_backoff_ms))


def _parse_retry_after_seconds(raw: str | None) -> float | None:
    """Parse a ``Retry-After`` header value. Accepts delta-seconds or HTTP-date."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # delta-seconds
    try:
        value = float(text)
        return max(value, 0.0)
    except ValueError:
        pass
    # HTTP-date
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    delta = dt.timestamp() - time.time()
    return max(delta, 0.0)


def _response_headers(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(k): str(v) for k, v in dict(headers).items()}
    except Exception:
        return {}


def _body_excerpt(response: Any) -> str:
    text = getattr(response, "text", None)
    if not isinstance(text, str):
        return ""
    return text[:500]


def _classify_status(status: int, retryable_status: frozenset[int]) -> LLMChunkErrorType:
    if status == 429 or status == 529:
        return "rate_limit"
    if status in retryable_status or 500 <= status < 600:
        return "connection"
    return "unknown"


def _make_error_for_status(
    *,
    url: str,
    status: int,
    body_excerpt: str,
    retryable_status: frozenset[int],
) -> Exception:
    classifier = _classify_status(status, retryable_status)
    msg = f"HTTP {status}: {body_excerpt}"
    if classifier == "rate_limit":
        hint = "provider rate-limited or overloaded; increase 'llm.retry.max_attempts' or slow down request rate"
        return LLMRateLimitError(msg, hint=hint).with_context()
    if classifier == "connection":
        hint = f"upstream server error from {url}; check provider status"
        return LLMConnectionError(msg, hint=hint)
    # Fall through for 4xx other than 429: surface as response error
    return LLMResponseError(msg, hint=f"non-retryable HTTP {status} from {url}")


def _make_error_for_exception(
    *,
    url: str,
    exc: BaseException,
) -> Exception:
    hint = "connection or timeout error talking to the provider; check network and provider health"
    return LLMConnectionError(
        f"{type(exc).__name__} connecting to {url}: {exc}",
        hint=hint,
    )


def _classify_stream_error(
    *,
    status: int | None,
    exc: BaseException | None,
    retryable_status: frozenset[int],
) -> LLMChunkErrorType:
    if status is not None:
        return _classify_status(status, retryable_status)
    if exc is not None:
        return "connection"
    return "unknown"


class HTTPProviderClient(LLMClient):
    """LLM client with a reusable AsyncClient-backed transport + retry."""

    def __init__(
        self,
        *,
        timeout_ms: int,
        retry_policy: _RetryPolicy | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self._http_client = None
        self._retry_policy = retry_policy or _RetryPolicy()
        self._extra_headers = dict(extra_headers) if extra_headers else {}

    def _require_httpx(self):
        if httpx is None:
            raise RuntimeError("httpx is required for HTTP-backed LLM providers. Install with: uv add httpx")
        return httpx

    def _build_timeout(self, *, read_timeout_s: float | None = None):
        httpx_mod = self._require_httpx()
        timeout_s = max(self.timeout_ms, 1) / 1000
        read_s = read_timeout_s if read_timeout_s is not None else timeout_s
        return httpx_mod.Timeout(timeout_s, read=read_s, write=timeout_s, pool=timeout_s)

    async def _get_http_client(self):
        if self._http_client is not None:
            return self._http_client

        httpx_mod = self._require_httpx()
        self._http_client = httpx_mod.AsyncClient(
            timeout=self._build_timeout(),
            limits=httpx_mod.Limits(max_connections=100, max_keepalive_connections=20),
            http2=True,
        )
        return self._http_client

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        """Merge provider-supplied headers with user-supplied extra_headers.

        Provider defaults first, then user overrides — user keys win on conflict.
        """
        merged: dict[str, str] = dict(headers or {})
        for key, value in self._extra_headers.items():
            merged[key] = value
        return merged

    async def _sleep(self, delay_s: float) -> None:
        if delay_s <= 0:
            return
        await asyncio.sleep(delay_s)

    async def _compute_backoff(
        self,
        *,
        attempt: int,
        retry_after_s: float | None,
    ) -> float:
        backoff_s = self._retry_policy.backoff_ms(attempt) / 1000.0
        if retry_after_s is not None:
            return max(backoff_s, retry_after_s)
        return backoff_s

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        read_timeout_s: float | None = None,
    ):
        """Issue an HTTP request with retry on retryable status codes / connection errors.

        Returns the final ``httpx`` response. Raises ``LLMRateLimitError`` /
        ``LLMConnectionError`` / ``LLMResponseError`` when retries are exhausted.
        Non-retryable non-200 responses (e.g., 401, 400) are returned to the
        caller unchanged for caller-side classification.
        """
        client = await self._get_http_client()
        policy = self._retry_policy
        retryable = policy.retryable_status
        merged_headers = self._merge_headers(headers)
        start = time.monotonic()
        last_exc: BaseException | None = None
        last_response: Any = None

        for attempt in range(1, policy.max_attempts + 1):
            remaining_ms: float | None = None
            if policy.total_budget_ms is not None:
                elapsed_ms = (time.monotonic() - start) * 1000.0
                remaining_ms = policy.total_budget_ms - elapsed_ms
                if remaining_ms <= 0:
                    break

            try:
                response = await client.request(
                    method,
                    url,
                    headers=merged_headers,
                    json=json_body,
                    timeout=self._build_timeout(read_timeout_s=read_timeout_s),
                )
            except _CONNECTION_EXC_TYPES as exc:
                last_exc = exc
                if not policy.retry_on_connection_errors or attempt >= policy.max_attempts:
                    break
                delay = await self._compute_backoff(attempt=attempt, retry_after_s=None)
                if remaining_ms is not None:
                    delay = min(delay, max(remaining_ms / 1000.0, 0.0))
                logger.warning(
                    "LLM transport retry %d/%d after %s: %s",
                    attempt,
                    policy.max_attempts,
                    type(exc).__name__,
                    exc,
                )
                await self._sleep(delay)
                continue

            status = int(getattr(response, "status_code", 0))
            # Success or non-retryable status: hand the response to the caller
            if status not in retryable:
                return response
            last_response = response
            # On the final attempt, stop retrying and fall through to raise
            if attempt >= policy.max_attempts:
                break
            retry_after = _parse_retry_after_seconds(
                _response_headers(response).get("Retry-After") or _response_headers(response).get("retry-after")
            )
            delay = await self._compute_backoff(attempt=attempt, retry_after_s=retry_after)
            if remaining_ms is not None:
                delay = min(delay, max(remaining_ms / 1000.0, 0.0))
            logger.warning(
                "LLM transport retry %d/%d after HTTP %d",
                attempt,
                policy.max_attempts,
                status,
            )
            await self._sleep(delay)

        if last_exc is not None:
            raise _make_error_for_exception(url=url, exc=last_exc)
        if last_response is not None:
            raise _make_error_for_status(
                url=url,
                status=int(getattr(last_response, "status_code", 0)),
                body_excerpt=_body_excerpt(last_response),
                retryable_status=retryable,
            )
        # Budget exhausted without ever issuing a request
        raise LLMConnectionError(
            f"retry budget exhausted before first attempt to {url}",
            hint="increase 'llm.retry.total_budget_ms' or raise 'llm.timeout_ms'",
        )

    async def _stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        read_timeout_s: float | None = None,
    ):
        """Return an async context manager yielding a streaming response.

        Legacy callers that do ``async with await self._stream(...) as response:``
        still work. Callers that want retry + typed errors on stream-open
        failure should use ``_open_stream`` instead.
        """
        client = await self._get_http_client()
        merged_headers = self._merge_headers(headers)
        return client.stream(
            method,
            url,
            headers=merged_headers,
            json=json_body,
            timeout=self._build_timeout(read_timeout_s=read_timeout_s),
        )

    async def _open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        read_timeout_s: float | None = None,
    ):
        """Open a streaming response with retry + typed error classification.

        Returns an already-entered response object. Caller is responsible for
        calling ``response.__aexit__`` (or using a wrapper async-with) once
        iteration finishes.

        On retryable failure (non-200 in the retryable set, or connection
        errors), retries up to ``max_attempts`` before raising the typed error.
        Once streaming bytes begin, retry is NOT attempted — that's the caller's
        problem.
        """
        client = await self._get_http_client()
        policy = self._retry_policy
        retryable = policy.retryable_status
        merged_headers = self._merge_headers(headers)
        start = time.monotonic()
        last_exc: BaseException | None = None
        last_entered: Any = None
        last_status: int | None = None

        for attempt in range(1, policy.max_attempts + 1):
            remaining_ms: float | None = None
            if policy.total_budget_ms is not None:
                elapsed_ms = (time.monotonic() - start) * 1000.0
                remaining_ms = policy.total_budget_ms - elapsed_ms
                if remaining_ms <= 0:
                    break

            try:
                cm = client.stream(
                    method,
                    url,
                    headers=merged_headers,
                    json=json_body,
                    timeout=self._build_timeout(read_timeout_s=read_timeout_s),
                )
                response = await cm.__aenter__()
            except _CONNECTION_EXC_TYPES as exc:
                last_exc = exc
                if not policy.retry_on_connection_errors or attempt >= policy.max_attempts:
                    break
                delay = await self._compute_backoff(attempt=attempt, retry_after_s=None)
                if remaining_ms is not None:
                    delay = min(delay, max(remaining_ms / 1000.0, 0.0))
                logger.warning(
                    "LLM stream retry %d/%d after %s: %s",
                    attempt,
                    policy.max_attempts,
                    type(exc).__name__,
                    exc,
                )
                await self._sleep(delay)
                continue

            status = int(getattr(response, "status_code", 0))
            # Success or non-retryable: hand the opened stream to the caller
            if status not in retryable:
                return response, cm
            last_entered = (response, cm)
            last_status = status
            # Final attempt with retryable status: fall through to raise
            if attempt >= policy.max_attempts:
                break
            # Best-effort close of the retryable response before sleeping
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass
            retry_after = _parse_retry_after_seconds(
                _response_headers(response).get("Retry-After") or _response_headers(response).get("retry-after")
            )
            delay = await self._compute_backoff(attempt=attempt, retry_after_s=retry_after)
            if remaining_ms is not None:
                delay = min(delay, max(remaining_ms / 1000.0, 0.0))
            logger.warning(
                "LLM stream retry %d/%d after HTTP %d",
                attempt,
                policy.max_attempts,
                status,
            )
            await self._sleep(delay)

        if last_exc is not None:
            raise _make_error_for_exception(url=url, exc=last_exc)
        if last_status is not None:
            # Re-read body of the last entered response for the message, then close
            body_excerpt = ""
            if last_entered is not None:
                resp, resp_cm = last_entered
                body_bytes = b""
                try:
                    body_bytes = await resp.aread()
                except Exception:
                    pass
                try:
                    body_excerpt = body_bytes.decode("utf-8", errors="replace")[:500]
                except Exception:
                    body_excerpt = ""
                try:
                    await resp_cm.__aexit__(None, None, None)
                except Exception:
                    pass
            raise _make_error_for_status(
                url=url,
                status=last_status,
                body_excerpt=body_excerpt,
                retryable_status=retryable,
            )
        raise LLMConnectionError(
            f"retry budget exhausted before first attempt to {url}",
            hint="increase 'llm.retry.total_budget_ms' or raise 'llm.timeout_ms'",
        )

    def _yield_stream_error_chunk(
        self,
        *,
        exc: BaseException,
    ) -> LLMChunk:
        """Build an error LLMChunk from a typed exception for stream callers."""
        if isinstance(exc, LLMRateLimitError):
            classifier: LLMChunkErrorType = "rate_limit"
        elif isinstance(exc, LLMConnectionError):
            classifier = "connection"
        elif isinstance(exc, LLMResponseError):
            classifier = "response"
        else:
            classifier = "unknown"
        # Preserve the concrete message (which includes HTTP status for callers
        # that still match on it).
        return LLMChunk(type="error", error=str(exc).splitlines()[0], error_type=classifier)

    def _raise_for_response_status(
        self,
        *,
        url: str,
        response: Any,
    ) -> None:
        """Raise a typed error when a non-retryable non-200 response arrives."""
        status = int(getattr(response, "status_code", 0))
        if status == 200:
            return
        raise _make_error_for_status(
            url=url,
            status=status,
            body_excerpt=_body_excerpt(response),
            retryable_status=self._retry_policy.retryable_status,
        )

    def _parse_response_json(
        self,
        *,
        url: str,
        response: Any,
    ) -> Any:
        """Parse a successful response body as JSON, raising LLMResponseError on failure."""
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMResponseError(
                f"non-JSON response from {url}: {exc}",
                hint="provider returned non-JSON on a 200 response; inspect upstream logs",
            )

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
