"""End-to-end: a runtime configured with sqlite session sees state across runs."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

import openagents.llm.registry as llm_registry
from openagents.interfaces.session import _TRANSCRIPT_KEY
from openagents.llm.providers.mock import MockLLMClient
from openagents.runtime.runtime import Runtime


@pytest.mark.asyncio
async def test_sqlite_session_replays_across_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        llm_registry, "create_llm_client", lambda llm: MockLLMClient()
    )
    db_path = tmp_path / "agent.db"
    payload = {
        "version": "1.0",
        "session": {"type": "sqlite", "config": {"db_path": str(db_path)}},
        "agents": [
            {
                "id": "assistant",
                "name": "demo-agent",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react", "config": {"max_steps": 4}},
                "llm": {"provider": "mock"},
                "runtime": {"max_steps": 4, "step_timeout_ms": 5000},
            }
        ],
    }

    runtime = Runtime.from_dict(payload)
    first = await runtime.run(
        agent_id="assistant",
        session_id="sqlite-sess",
        input_text="hello first",
    )
    assert isinstance(first, str)

    state_after_first = await runtime.session_manager.get_state("sqlite-sess")
    transcript_after_first = list(state_after_first.get(_TRANSCRIPT_KEY, []))
    assert len(transcript_after_first) >= 1

    # Second run on the SAME runtime/session should accumulate transcript.
    second = await runtime.run(
        agent_id="assistant",
        session_id="sqlite-sess",
        input_text="hello second",
    )
    assert isinstance(second, str)

    # Now construct a fresh runtime pointing at the same db; replay must
    # see the transcript persisted by the first runtime.
    runtime2 = Runtime.from_dict(payload)
    state2 = await runtime2.session_manager.get_state("sqlite-sess")
    transcript2 = list(state2.get(_TRANSCRIPT_KEY, []))
    assert len(transcript2) >= len(transcript_after_first)
    contents = [m.get("content") for m in transcript2 if isinstance(m, dict)]
    # The user inputs should appear verbatim in the persisted transcript.
    assert "hello first" in contents
    assert "hello second" in contents
