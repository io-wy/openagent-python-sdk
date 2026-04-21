"""Integration tests: DiagnosticsPlugin wired into DefaultRuntime."""

from __future__ import annotations

import pytest

import openagents.llm.registry as llm_registry
from openagents.llm.providers.mock import MockLLMClient
from openagents.runtime.runtime import Runtime
from tests.fixtures.diagnostics_plugins import (
    get_last_singleton,
    reset_singleton,
)

_DIAG_IMPL = "tests.fixtures.diagnostics_plugins.SingletonCapturingDiagnosticsPlugin"


def _minimal_config_with_diagnostics():
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "diag-agent",
                "name": "Diag Agent",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react"},
                "llm": {"provider": "mock"},
            }
        ],
        "diagnostics": {"impl": _DIAG_IMPL},
    }


@pytest.mark.asyncio
async def test_on_run_complete_called_on_success(monkeypatch):
    reset_singleton()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())

    runtime = Runtime.from_dict(_minimal_config_with_diagnostics())
    diag = get_last_singleton()
    assert diag is not None

    result = await runtime.run(
        agent_id="diag-agent",
        session_id="s1",
        input_text="hello",
    )

    assert isinstance(result, str)
    assert len(diag.run_completes) == 1
    run_result, snapshot = diag.run_completes[0]
    assert snapshot is None
    assert run_result.stop_reason in ("completed", "COMPLETED")


@pytest.mark.asyncio
async def test_record_llm_call_receives_metrics(monkeypatch):
    reset_singleton()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())

    runtime = Runtime.from_dict(_minimal_config_with_diagnostics())
    diag = get_last_singleton()
    assert diag is not None

    await runtime.run(agent_id="diag-agent", session_id="s1", input_text="hello")

    assert len(diag.llm_calls) >= 1
    run_id, metrics = diag.llm_calls[0]
    assert isinstance(run_id, str) and run_id
    assert metrics.latency_ms >= 0.0
    # Fields are always populated as dataclass attributes, but token counts
    # depend on MockLLMClient — simply ensure the type is LLMCallMetrics.
    from openagents.interfaces.diagnostics import LLMCallMetrics

    assert isinstance(metrics, LLMCallMetrics)


@pytest.mark.asyncio
async def test_null_diagnostics_does_not_break_run(monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())

    config = _minimal_config_with_diagnostics()
    config["diagnostics"] = {"type": "null"}

    runtime = Runtime.from_dict(config)
    result = await runtime.run(
        agent_id="diag-agent",
        session_id="s1",
        input_text="hello",
    )
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_missing_diagnostics_defaults_to_null(monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())

    config = _minimal_config_with_diagnostics()
    config.pop("diagnostics", None)

    runtime = Runtime.from_dict(config)
    result = await runtime.run(
        agent_id="diag-agent",
        session_id="s1",
        input_text="hello",
    )
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_error_snapshot_attached_on_failure(monkeypatch):
    """Simulate an LLM failure and assert ErrorSnapshot is produced."""
    reset_singleton()

    class _FailingClient(MockLLMClient):
        async def generate(self, **kwargs):
            raise RuntimeError("simulated upstream 500")

    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: _FailingClient())

    runtime = Runtime.from_dict(_minimal_config_with_diagnostics())
    diag = get_last_singleton()
    assert diag is not None

    from openagents.interfaces.runtime import RunRequest

    req = RunRequest(
        agent_id="diag-agent",
        session_id="s-fail",
        input_text="trigger failure",
    )
    result = await runtime.run_detailed(request=req)

    assert result.stop_reason != "completed"
    assert len(diag.run_completes) == 1
    _, snapshot = diag.run_completes[0]
    assert snapshot is not None
    assert snapshot.error_type  # non-empty
    assert snapshot.run_id == req.run_id
    # error_snapshot should also be embedded in metadata
    assert "error_snapshot" in result.metadata
