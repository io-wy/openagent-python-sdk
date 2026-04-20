import pytest

from openagents.llm.providers.anthropic import AnthropicClient


@pytest.fixture
def anthropic_client():
    client = AnthropicClient(
        api_key="test",
        model="claude-sonnet-4-6",
    )
    return client


def test_anthropic_price_table_lookup_applies_for_known_model(anthropic_client):
    assert anthropic_client.price_per_mtok_input == 3.00
    assert anthropic_client.price_per_mtok_output == 15.00
    assert anthropic_client.price_per_mtok_cached_read == 0.30
    assert anthropic_client.price_per_mtok_cached_write == 3.75


def test_anthropic_unknown_model_leaves_prices_none():
    client = AnthropicClient(api_key="test", model="claude-unknown-model")
    assert client.price_per_mtok_input is None
    assert client.price_per_mtok_output is None


def test_anthropic_extracts_cache_tokens_from_response_usage():
    # Simulate Anthropic's usage payload with cache fields
    raw_usage = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
        "cache_creation_input_tokens": 100,
    }
    client = AnthropicClient(api_key="test", model="claude-sonnet-4-6")
    normalized = client._normalize_usage(raw_usage)
    assert normalized.input_tokens == 1000
    assert normalized.output_tokens == 500
    assert normalized.metadata.get("cache_read_input_tokens") == 200
    assert normalized.metadata.get("cache_creation_input_tokens") == 100


def test_anthropic_count_tokens_falls_back_to_len_div_4():
    client = AnthropicClient(api_key="test", model="claude-sonnet-4-6")
    assert client.count_tokens("abcd" * 8) == 8
