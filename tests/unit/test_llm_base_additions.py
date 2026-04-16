import logging

import pytest

from openagents.config.schema import LLMOptions, LLMPricing
from openagents.llm.base import (
    LLMClient,
    LLMCostBreakdown,
    compute_cost,
)


class _DummyClient(LLMClient):
    provider_name = "dummy"
    model_id = "dummy-1"


def test_llm_client_has_price_attrs_none_by_default():
    client = _DummyClient()
    assert client.price_per_mtok_input is None
    assert client.price_per_mtok_output is None
    assert client.price_per_mtok_cached_read is None
    assert client.price_per_mtok_cached_write is None


def test_count_tokens_fallback_uses_len_over_4(caplog):
    client = _DummyClient()
    with caplog.at_level(logging.WARNING, logger="openagents"):
        assert client.count_tokens("abcd" * 4) == 4  # len=16, //4=4
        assert client.count_tokens("abcd" * 4) == 4
    assert len([r for r in caplog.records if "fallback" in r.message.lower()]) == 1


def test_compute_cost_returns_none_when_any_rate_missing():
    rates = LLMPricing(input=1.0)  # output missing
    result = compute_cost(
        input_tokens_non_cached=100,
        output_tokens=100,
        cached_read_tokens=0,
        cached_write_tokens=0,
        rates=rates,
    )
    assert result is None


def test_compute_cost_multiplies_each_bucket():
    rates = LLMPricing(input=3.0, output=15.0, cached_read=0.3, cached_write=3.75)
    breakdown = compute_cost(
        input_tokens_non_cached=1_000_000,
        output_tokens=500_000,
        cached_read_tokens=200_000,
        cached_write_tokens=100_000,
        rates=rates,
    )
    assert isinstance(breakdown, LLMCostBreakdown)
    assert breakdown.input == pytest.approx(3.00)
    assert breakdown.output == pytest.approx(7.50)
    assert breakdown.cached_read == pytest.approx(0.06)
    assert breakdown.cached_write == pytest.approx(0.375)
    assert breakdown.total == pytest.approx(3.00 + 7.50 + 0.06 + 0.375)


def test_llm_options_pricing_parses():
    options = LLMOptions(provider="mock", pricing={"input": 1.0, "output": 2.0})
    assert options.pricing is not None
    assert options.pricing.input == 1.0
    assert options.pricing.output == 2.0
    assert options.pricing.cached_read is None
