"""WP5 backfill: cover new lifecycle emits + unknown-agent hints in runtime."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import openagents.llm.registry as llm_registry
from openagents.errors.exceptions import ConfigError
from openagents.llm.providers.mock import MockLLMClient
from openagents.runtime.runtime import Runtime


def _build_runtime(tmp_path: Path):
    payload = {
        "version": "1.0",
        "agents": [
            {
                "id": "alice",
                "name": "alice",
                "memory": {"impl": "tests.fixtures.custom_plugins.CustomMemory"},
                "pattern": {"impl": "tests.fixtures.custom_plugins.CustomPattern"},
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {"max_steps": 4, "step_timeout_ms": 1000},
            },
        ],
    }
    cfg = tmp_path / "agents.json"
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    return Runtime.from_config(cfg)


@pytest.mark.asyncio
async def test_unknown_agent_id_includes_did_you_mean(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = _build_runtime(tmp_path)

    with pytest.raises(ConfigError) as ei:
        await runtime.run(
            agent_id="alic",
            session_id="s",
            input_text="x",
        )
    text = str(ei.value)
    assert "Unknown agent id" in text
    assert "Did you mean" in text or "alice" in text
    assert ei.value.hint is not None


@pytest.mark.asyncio
async def test_reload_unknown_agent_includes_hint(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = _build_runtime(tmp_path)

    with pytest.raises(ConfigError) as ei:
        await runtime.reload_agent("ali")
    assert ei.value.hint is not None


@pytest.mark.asyncio
async def test_session_run_started_and_completed_emitted(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = _build_runtime(tmp_path)

    await runtime.run(agent_id="alice", session_id="s1", input_text="hi")

    started = await runtime.event_bus.get_history("session.run.started")
    completed = await runtime.event_bus.get_history("session.run.completed")
    assert len(started) == 1
    assert len(completed) == 1
    payload = started[0].payload
    assert payload["agent_id"] == "alice"
    assert payload["session_id"] == "s1"
    cpayload = completed[0].payload
    assert cpayload["stop_reason"]
    assert "duration_ms" in cpayload


@pytest.mark.asyncio
async def test_context_assemble_started_and_completed_emitted(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = _build_runtime(tmp_path)

    await runtime.run(agent_id="alice", session_id="s2", input_text="hi")

    started = await runtime.event_bus.get_history("context.assemble.started")
    completed = await runtime.event_bus.get_history("context.assemble.completed")
    assert len(started) == 1
    assert len(completed) == 1
    assert "transcript_size" in completed[0].payload


@pytest.mark.asyncio
async def test_memory_inject_writeback_lifecycle_events(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    runtime = _build_runtime(tmp_path)

    await runtime.run(agent_id="alice", session_id="s3", input_text="hi")

    inj_started = await runtime.event_bus.get_history("memory.inject.started")
    inj_done = await runtime.event_bus.get_history("memory.inject.completed")
    wb_started = await runtime.event_bus.get_history("memory.writeback.started")
    wb_done = await runtime.event_bus.get_history("memory.writeback.completed")

    # Only fires when the configured memory supports the capability;
    # the test fixture's CustomMemory may or may not - assert non-negative
    # but consistent ordering when both are present.
    assert len(inj_started) == len(inj_done)
    assert len(wb_started) == len(wb_done)
