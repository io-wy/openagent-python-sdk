from openagents.config.schema import LLMPricing
from openagents.llm.base import LLMUsage
from openagents.llm.providers.anthropic import AnthropicClient


def test_anthropic_generate_attaches_cost_when_rates_available(monkeypatch):
    client = AnthropicClient(api_key="", model="claude-sonnet-4-6")
    # Pretend the provider returned this usage payload.
    raw_usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 500_000,
        "cache_read_input_tokens": 200_000,
        "cache_creation_input_tokens": 100_000,
    }
    usage = client._compute_cost_for(
        usage=client._normalize_usage(raw_usage),
        overrides=None,
    )
    # At the Sonnet 4.6 rates above:
    #   non-cached input = 1_000_000 - 200_000 - 100_000 = 700_000 → 0.7M × 3.00 = 2.10
    #   output  = 0.5M × 15.00 = 7.50
    #   c_read  = 0.2M × 0.30  = 0.06
    #   c_write = 0.1M × 3.75  = 0.375
    #   total   = 10.035
    # Note: if your implementation differs (e.g., uses raw input_tokens without subtracting),
    # adjust the assertion. The _compute_cost_for in base.py subtracts cached tokens from input.
    import pytest
    assert usage.metadata.get("cost_usd") == pytest.approx(10.035)


def test_provider_cost_none_when_any_rate_is_none():
    client = AnthropicClient(api_key="", model="claude-unknown")
    raw_usage = {"input_tokens": 100, "output_tokens": 50}
    usage = client._compute_cost_for(
        usage=client._normalize_usage(raw_usage),
        overrides=None,
    )
    assert usage.metadata.get("cost_usd") is None


def test_provider_cost_respects_per_field_override():
    client = AnthropicClient(api_key="", model="claude-sonnet-4-6")
    usage = client._compute_cost_for(
        usage=client._normalize_usage({"input_tokens": 1_000_000, "output_tokens": 0}),
        overrides=LLMPricing(input=1.0),  # override input only; output stays at 15.00 default
    )
    assert usage.metadata.get("cost_usd") == 1.0
