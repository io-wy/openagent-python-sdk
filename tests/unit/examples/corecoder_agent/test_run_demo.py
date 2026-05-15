"""Tests for environment alias normalization in run_demo."""

from __future__ import annotations

import os

from examples.corecoder_agent.run_demo import normalize_env_aliases


def test_normalize_env_aliases_maps_openai_style_vars(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")

    normalize_env_aliases()

    assert os.environ["LLM_PROVIDER"] == "openai_compatible"
    assert os.environ["LLM_API_KEY"] == "sk-test"
    assert os.environ["LLM_API_BASE"] == "https://api.openai.com/v1"
    assert os.environ["LLM_MODEL"] == "gpt-4.1-mini"


def test_normalize_env_aliases_defaults_to_anthropic_for_anthropic_base(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_API_BASE", "https://api.anthropic.com")

    normalize_env_aliases()

    assert os.environ["LLM_PROVIDER"] == "anthropic"
