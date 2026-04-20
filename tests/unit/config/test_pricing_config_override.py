from openagents.config.schema import LLMOptions, LLMPricing
from openagents.llm.registry import build_llm_client_from_options


def test_options_pricing_flows_into_provider():
    options = LLMOptions(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_env="FAKE_KEY_ENV",
        pricing=LLMPricing(input=1.0, output=2.0),
    )
    client = build_llm_client_from_options(options)
    # Base-class defaults still in place for unsupplied fields.
    assert client.price_per_mtok_cached_read == 0.30  # from provider's static price table
    # Overrides are stored on the client for per-call use.
    assert client._pricing_overrides is not None
    assert client._pricing_overrides.input == 1.0
    assert client._pricing_overrides.output == 2.0
    assert client._pricing_overrides.cached_read is None  # not overridden
