from openagents.llm.providers.openai_compatible import OpenAICompatibleClient


def test_openai_compatible_price_table_for_known_models():
    client = OpenAICompatibleClient(api_key="k", model="gpt-4o")
    # At least the minimum known models must have pricing assigned.
    # Verified names/values come from the module's _OPENAI_PRICE_TABLE.
    assert client.price_per_mtok_input is not None
    assert client.price_per_mtok_output is not None


def test_openai_compatible_extracts_cached_tokens():
    raw_usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": 300},
    }
    client = OpenAICompatibleClient(api_key="k", model="gpt-4o")
    usage = client._normalize_usage(raw_usage)
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 500
    assert usage.metadata.get("cached_tokens") == 300


def test_openai_compatible_count_tokens_returns_positive_int():
    client = OpenAICompatibleClient(api_key="k", model="gpt-4o")
    # We don't assert exact tokenization; only that it returns a positive int.
    assert client.count_tokens("hello world") >= 1
