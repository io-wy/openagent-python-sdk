"""Tests for the handwritten CoreCoder local runner."""

from __future__ import annotations

import pytest

from examples.corecoder_agent.app.runner import CoreCoderLocalRunner


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "mock-1")


@pytest.mark.asyncio
async def test_local_runner_smoke() -> None:
    runner = CoreCoderLocalRunner("examples/corecoder_agent/agent.json")
    try:
        result = await runner.run_detailed(
            agent_id="corecoder",
            session_id="runner-smoke",
            input_text="INPUT: hello",
        )
    finally:
        await runner.close()

    assert result.stop_reason.value == "completed"
    assert result.final_output is not None
    assert "Echo: hello" in result.final_output
    assert result.usage.llm_calls >= 1


@pytest.mark.asyncio
async def test_local_runner_second_turn_does_not_duplicate_history() -> None:
    runner = CoreCoderLocalRunner("examples/corecoder_agent/agent.json")
    try:
        await runner.run_detailed(
            agent_id="corecoder",
            session_id="runner-history",
            input_text="INPUT: first",
        )
        result = await runner.run_detailed(
            agent_id="corecoder",
            session_id="runner-history",
            input_text="INPUT: second",
        )
        transcript = await runner._sessions.load_messages("runner-history")
    finally:
        await runner.close()

    assert result.stop_reason.value == "completed"
    assert "Echo: second" in (result.final_output or "")
    assert [entry["role"] for entry in transcript] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
