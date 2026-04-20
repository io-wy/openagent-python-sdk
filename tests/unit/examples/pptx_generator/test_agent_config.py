"""Smoke tests for examples/pptx_generator/agent.json config."""

from pathlib import Path

import pytest

from openagents.config.loader import load_config


@pytest.fixture(autouse=True)
def _pptx_env(monkeypatch):
    """Set required env vars referenced by agent.json placeholders."""
    monkeypatch.setenv("LLM_API_BASE", "https://api.anthropic.com")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")


def test_agent_json_loads():
    cfg = load_config(Path("examples/pptx_generator/agent.json"))
    agent_ids = {a.id for a in cfg.agents}
    assert agent_ids == {
        "intent-analyst",
        "research-agent",
        "outliner",
        "theme-selector",
        "slide-generator",
    }


def test_shared_memory_is_chain_with_markdown():
    cfg = load_config(Path("examples/pptx_generator/agent.json"))
    for agent in cfg.agents:
        mem = agent.memory
        assert mem.type == "chain"
        mems = mem.config["memories"]
        assert any(m["type"] == "markdown_memory" for m in mems)
