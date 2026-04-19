from __future__ import annotations

import pytest
from pydantic import ValidationError

from openagents.config.schema import AppConfig, LLMOptions, LLMRetryOptions, MemoryRef


def test_app_config_model_validate_parses_minimal_payload():
    config = AppConfig.model_validate(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "assistant",
                    "name": "demo",
                    "memory": {"type": "window_buffer"},
                    "pattern": {"type": "react"},
                    "llm": {"provider": "mock"},
                    "tools": [],
                }
            ],
        }
    )

    assert config.agents[0].memory == MemoryRef(type="window_buffer")


def test_memory_ref_model_validate_defaults_on_error_literal():
    memory = MemoryRef.model_validate({"type": "window_buffer"})

    assert memory.on_error == "continue"


def test_llm_options_new_fields_default_to_none():
    opts = LLMOptions(provider="mock")

    assert opts.retry is None
    assert opts.extra_headers is None
    assert opts.reasoning_model is None


def test_llm_retry_options_defaults():
    retry = LLMRetryOptions()

    assert retry.max_attempts == 3
    assert retry.initial_backoff_ms == 500
    assert retry.max_backoff_ms == 5000
    assert retry.backoff_multiplier == 2.0
    assert retry.retry_on_connection_errors is True
    assert retry.total_budget_ms is None


def test_llm_retry_options_rejects_zero_max_attempts():
    with pytest.raises(ValidationError):
        LLMRetryOptions(max_attempts=0)


def test_llm_retry_options_rejects_sub_unit_multiplier():
    with pytest.raises(ValidationError):
        LLMRetryOptions(backoff_multiplier=0.5)


def test_llm_retry_options_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        LLMRetryOptions.model_validate({"max_attempts": 3, "unknown_field": True})


def test_llm_options_accepts_extra_headers_dict():
    opts = LLMOptions.model_validate(
        {
            "provider": "anthropic",
            "extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31", "X-Trace": "on"},
        }
    )

    assert opts.extra_headers == {
        "anthropic-beta": "prompt-caching-2024-07-31",
        "X-Trace": "on",
    }


def test_llm_options_rejects_non_string_header_value():
    with pytest.raises(ValidationError):
        LLMOptions.model_validate(
            {"provider": "anthropic", "extra_headers": {"X-Num": 42}}
        )


def test_llm_options_rejects_non_dict_extra_headers():
    with pytest.raises(ValidationError):
        LLMOptions.model_validate({"provider": "anthropic", "extra_headers": ["k", "v"]})


def test_llm_options_empty_extra_headers_normalized_to_none():
    opts = LLMOptions.model_validate({"provider": "anthropic", "extra_headers": {}})

    assert opts.extra_headers is None


def test_llm_options_legacy_payload_loads_byte_identical():
    """Existing configs without new fields must load unchanged."""
    legacy = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "api_base": "https://api.anthropic.com",
        "temperature": 0.5,
        "max_tokens": 1024,
    }
    opts = LLMOptions.model_validate(legacy)

    assert opts.provider == "anthropic"
    assert opts.model == "claude-sonnet-4-6"
    assert opts.temperature == 0.5
    assert opts.max_tokens == 1024
    assert opts.retry is None
    assert opts.extra_headers is None
    assert opts.reasoning_model is None


def test_llm_options_retry_nested_model_validates():
    opts = LLMOptions.model_validate(
        {
            "provider": "mock",
            "retry": {"max_attempts": 5, "initial_backoff_ms": 250},
        }
    )

    assert opts.retry is not None
    assert opts.retry.max_attempts == 5
    assert opts.retry.initial_backoff_ms == 250
    # Other retry fields keep their defaults
    assert opts.retry.max_backoff_ms == 5000
    assert opts.retry.backoff_multiplier == 2.0


def test_llm_options_reasoning_model_bool_accepted():
    opts_true = LLMOptions.model_validate({"provider": "mock", "reasoning_model": True})
    opts_false = LLMOptions.model_validate({"provider": "mock", "reasoning_model": False})

    assert opts_true.reasoning_model is True
    assert opts_false.reasoning_model is False
