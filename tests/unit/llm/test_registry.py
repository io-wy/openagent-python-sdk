"""Tests for llm registry and providers."""

import pytest

from openagents.config.schema import LLMOptions, LLMRetryOptions
from openagents.errors.exceptions import ConfigError, ConfigValidationError
from openagents.llm.providers.anthropic import AnthropicClient
from openagents.llm.providers.mock import MockLLMClient
from openagents.llm.providers.openai_compatible import OpenAICompatibleClient
from openagents.llm.registry import create_llm_client


def test_create_llm_client_mock():
    """Test creating a mock LLM client."""
    config = LLMOptions(provider="mock")
    client = create_llm_client(config)

    assert client is not None
    assert isinstance(client, MockLLMClient)


def test_create_llm_client_with_model():
    """Test creating a mock LLM client with model."""
    config = LLMOptions(provider="mock", model="gpt-4")
    client = create_llm_client(config)

    assert client is not None


def test_create_llm_client_unknown():
    """Test creating client with unknown provider raises error."""
    with pytest.raises(ConfigValidationError):
        LLMOptions(provider="unknown_provider")


@pytest.mark.asyncio
async def test_mock_llm_client_complete():
    """Test MockLLMClient complete method."""
    client = MockLLMClient()

    result = await client.complete(
        messages=[{"role": "user", "content": "Hello"}],
    )

    assert result is not None
    assert "Echo" in result


@pytest.mark.asyncio
async def test_mock_llm_client_complete_with_model():
    """Test MockLLMClient with model parameter."""
    client = MockLLMClient()

    result = await client.complete(
        messages=[{"role": "user", "content": "test"}],
        model="gpt-4",
    )

    assert result is not None


def test_create_llm_client_none():
    """Test creating client with None returns None."""
    client = create_llm_client(None)

    assert client is None


def test_mock_llm_client_parse_prompt():
    """Test MockLLMClient prompt parsing."""
    client = MockLLMClient()

    # Test basic parsing - needs INPUT: prefix
    result = client._parse_prompt("INPUT: Hello world")
    assert result == {"input": "Hello world", "history_count": 0}

    # Test with history
    text = "INPUT: New message\nHISTORY_COUNT: 3"
    result = client._parse_prompt(text)
    assert result["history_count"] == 3


def test_mock_client_pricing_overridable():
    from openagents.llm.providers.mock import MockClient

    client = MockClient(api_key="", model="mock-1")
    # Default: no prices.
    assert client.price_per_mtok_input is None
    # Manual assignment used by tests.
    client.price_per_mtok_input = 1.0
    client.price_per_mtok_output = 2.0
    assert client.price_per_mtok_input == 1.0

    # count_tokens returns deterministic len//4
    assert client.count_tokens("xxxx" * 4) == 4


# ---------------------------------------------------------------------------
# Phase E: retry / extra_headers / reasoning_model threading
# ---------------------------------------------------------------------------


def test_registry_threads_retry_into_anthropic_client():
    opts = LLMOptions(
        provider="anthropic",
        api_base="https://api.anthropic.com",
        model="claude-test",
        retry=LLMRetryOptions(max_attempts=5, initial_backoff_ms=250),
    )
    client = create_llm_client(opts)
    assert isinstance(client, AnthropicClient)
    assert client._retry_policy.max_attempts == 5
    assert client._retry_policy.initial_backoff_ms == 250
    # Anthropic extras (529) still present
    assert 529 in client._retry_policy.retryable_status


def test_registry_threads_extra_headers_into_anthropic_client():
    opts = LLMOptions(
        provider="anthropic",
        api_base="https://api.anthropic.com",
        model="claude-test",
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )
    client = create_llm_client(opts)
    assert isinstance(client, AnthropicClient)
    assert client._extra_headers == {"anthropic-beta": "prompt-caching-2024-07-31"}


def test_registry_threads_retry_into_openai_client():
    opts = LLMOptions(
        provider="openai_compatible",
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
        retry=LLMRetryOptions(max_attempts=2),
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client._retry_policy.max_attempts == 2


def test_registry_threads_reasoning_model_opt_in_into_openai_client():
    opts = LLMOptions(
        provider="openai_compatible",
        api_base="https://api.openai.com/v1",
        model="custom-reasoner",
        reasoning_model=True,
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client._reasoning_model_opt_in is True


def test_registry_threads_extra_headers_into_openai_client():
    opts = LLMOptions(
        provider="openai_compatible",
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
        extra_headers={"X-Trace": "on"},
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client._extra_headers == {"X-Trace": "on"}


def test_registry_threads_seed_top_p_parallel_tool_calls_from_extras():
    opts = LLMOptions.model_validate(
        {
            "provider": "openai_compatible",
            "api_base": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "seed": 42,
            "top_p": 0.9,
            "parallel_tool_calls": False,
        }
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client._default_seed == 42
    assert client._default_top_p == 0.9
    assert client._default_parallel_tool_calls is False


def test_registry_omitting_new_fields_leaves_defaults_unchanged():
    """Registry behavior for a config without new fields must be byte-identical."""
    opts = LLMOptions(
        provider="anthropic",
        api_base="https://api.anthropic.com",
        model="claude-test",
    )
    client = create_llm_client(opts)
    assert isinstance(client, AnthropicClient)
    # Retry policy gets defaults (Anthropic includes 529 in its retryable set)
    assert client._retry_policy.max_attempts == 3
    assert client._retry_policy.initial_backoff_ms == 500
    assert client._extra_headers == {}


def test_registry_openai_omitting_new_fields_leaves_defaults_unchanged():
    opts = LLMOptions(
        provider="openai_compatible",
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client._retry_policy.max_attempts == 3
    assert client._extra_headers == {}
    assert client._reasoning_model_opt_in is None
    assert client._default_seed is None
    assert client._default_top_p is None
    assert client._default_parallel_tool_calls is None


def test_registry_ignores_non_int_seed():
    opts = LLMOptions.model_validate(
        {
            "provider": "openai_compatible",
            "api_base": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "seed": "not-an-int",
        }
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    # Non-int seed silently ignored to avoid corrupting the outgoing payload
    assert client._default_seed is None


def test_registry_threads_openai_api_style_explicit():
    opts = LLMOptions(
        provider="openai_compatible",
        api_base="https://api.openai.com/v1",
        model="gpt-5",
        openai_api_style="responses",
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_style == "responses"


def test_registry_autodetects_responses_style_from_api_base():
    opts = LLMOptions(
        provider="openai_compatible",
        api_base="https://api.openai.com/v1/responses",
        model="gpt-5",
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_style == "responses"


def test_registry_defaults_openai_api_style_to_chat_completions():
    opts = LLMOptions(
        provider="openai_compatible",
        api_base="https://api.openai.com/v1",
        model="gpt-4o",
    )
    client = create_llm_client(opts)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_style == "chat_completions"


def test_create_llm_client_litellm_routes_to_litellm_client():
    _ = pytest.importorskip("litellm")
    from openagents.llm.providers.litellm_client import LiteLLMClient

    config = LLMOptions(
        provider="litellm",
        model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    )
    client = create_llm_client(config)
    assert isinstance(client, LiteLLMClient)
    assert client.provider_name == "litellm:bedrock"


def test_create_llm_client_litellm_forwards_whitelisted_kwargs():
    _ = pytest.importorskip("litellm")
    config = LLMOptions(
        provider="litellm",
        model="bedrock/foo",
        aws_region_name="us-east-1",
    )
    client = create_llm_client(config)
    assert client._extra_kwargs.get("aws_region_name") == "us-east-1"


def test_create_llm_client_litellm_drops_non_whitelisted_kwargs(caplog):
    _ = pytest.importorskip("litellm")
    config = LLMOptions(
        provider="litellm",
        model="bedrock/foo",
        fallbacks=["some-model"],  # blacklisted: must not forward
        callbacks=["sentinel"],  # blacklisted
    )
    with caplog.at_level("WARNING", logger="openagents.llm"):
        client = create_llm_client(config)
    assert "fallbacks" not in client._extra_kwargs
    assert "callbacks" not in client._extra_kwargs


def test_create_llm_client_litellm_raises_config_error_when_package_missing(monkeypatch):
    _ = pytest.importorskip("litellm")
    from openagents.llm.providers import litellm_client as lc_module

    monkeypatch.setattr(lc_module, "litellm", None)
    config = LLMOptions(provider="litellm", model="bedrock/foo")
    with pytest.raises(ConfigError) as excinfo:
        create_llm_client(config)
    assert "pip install" in str(excinfo.value)
