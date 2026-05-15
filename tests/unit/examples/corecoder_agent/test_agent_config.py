"""Smoke tests for examples/corecoder_agent/agent.json config."""

from pathlib import Path

import pytest

from openagents.config.loader import load_config


@pytest.fixture(autouse=True)
def _corecoder_env(monkeypatch):
    """Set required env vars referenced by agent.json placeholders."""
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_API_BASE", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")


def test_corecoder_agent_json_loads_with_openai_compatible_provider():
    cfg = load_config(Path("examples/corecoder_agent/agent.json"))
    agent_ids = {a.id for a in cfg.agents}
    assert agent_ids == {"corecoder", "corecoder-subagent"}
    assert {a.llm.provider for a in cfg.agents if a.llm is not None} == {
        "openai_compatible"
    }
