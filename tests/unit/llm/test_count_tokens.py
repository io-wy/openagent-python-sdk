"""Cross-provider count_tokens behavior (Task 32)."""

from __future__ import annotations

from openagents.llm.providers.anthropic import AnthropicClient
from openagents.llm.providers.mock import MockLLMClient
from openagents.llm.providers.openai_compatible import OpenAICompatibleClient


def test_anthropic_count_tokens_fallback():
    """Anthropic provider uses the inherited len//4 fallback in Phase 1."""
    c = AnthropicClient(api_key="", model="claude-sonnet-4-6")
    assert c.count_tokens("abcdefgh") == 2


def test_openai_count_tokens_returns_positive_int():
    """OpenAI-compatible provider returns a positive token count regardless of
    whether tiktoken is installed."""
    c = OpenAICompatibleClient(api_key="k", model="gpt-4o")
    assert c.count_tokens("hello world") >= 1


def test_mock_count_tokens_fallback():
    """Mock provider uses the base fallback for deterministic test behavior."""
    c = MockLLMClient(api_key="", model="mock-1")
    assert c.count_tokens("abcdefgh") == 2


def test_fallback_warns_only_once_per_client():
    import logging

    # AnthropicClient is guaranteed to use the fallback path in Phase 1.
    client = AnthropicClient(api_key="", model="claude-sonnet-4-6")
    logger = logging.getLogger("openagents.llm")

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.warnings: list[str] = []

        def emit(self, record):
            if record.levelno >= logging.WARNING and "fallback" in record.getMessage().lower():
                self.warnings.append(record.getMessage())

    handler = _Capture()
    logger.addHandler(handler)
    try:
        # Reset the per-instance warn sentinel so this test is isolated.
        client._count_tokens_warned = False
        for _ in range(3):
            client.count_tokens("abc")
        assert len(handler.warnings) == 1
    finally:
        logger.removeHandler(handler)
