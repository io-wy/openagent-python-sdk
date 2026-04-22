from __future__ import annotations

from openagents.errors.exceptions import LLMRateLimitError, ToolRateLimitError


def test_tool_rate_limit_carries_retry_after_ms():
    exc = ToolRateLimitError("slow down", tool_name="api", retry_after_ms=5_000)
    assert exc.retry_after_ms == 5_000
    assert exc.to_dict()["context"]["retry_after_ms"] == 5_000


def test_tool_rate_limit_defaults_none():
    exc = ToolRateLimitError("slow down", tool_name="api")
    assert exc.retry_after_ms is None
    assert exc.to_dict()["context"]["retry_after_ms"] is None


def test_llm_rate_limit_carries_retry_after_ms():
    exc = LLMRateLimitError("429", retry_after_ms=2_500)
    assert exc.retry_after_ms == 2_500
    assert exc.to_dict()["context"]["retry_after_ms"] == 2_500
    assert exc.to_dict()["retryable"] is True


def test_llm_rate_limit_defaults_none():
    exc = LLMRateLimitError("429")
    assert exc.retry_after_ms is None
    assert exc.to_dict()["context"]["retry_after_ms"] is None
