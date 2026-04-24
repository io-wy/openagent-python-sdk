from __future__ import annotations

from openagents.interfaces.agent_router import (
    AgentNotFoundError,
    DelegationDepthExceededError,
    HandoffSignal,
)
from openagents.interfaces.runtime import RunResult, StopReason


def test_handoff_signal_carries_result():
    result = RunResult(run_id="r1", final_output="hello", stop_reason=StopReason.COMPLETED)
    sig = HandoffSignal(result)
    assert sig.result is result


def test_handoff_signal_is_base_exception():
    result = RunResult(run_id="r1", final_output="hi", stop_reason=StopReason.COMPLETED)
    sig = HandoffSignal(result)
    assert isinstance(sig, BaseException)
    assert not isinstance(sig, Exception)


def test_delegation_depth_error_message():
    err = DelegationDepthExceededError(depth=5, limit=3)
    assert "5" in str(err)
    assert "3" in str(err)
    assert err.depth == 5
    assert err.limit == 3


def test_agent_not_found_carries_agent_id():
    err = AgentNotFoundError("billing_agent")
    assert isinstance(err, Exception)
    assert "billing_agent" in str(err)
    assert err.agent_id == "billing_agent"


# ---------------------------------------------------------------------------
# Task 2: Config schema
# ---------------------------------------------------------------------------

from openagents.config.schema import AppConfig, MultiAgentConfig  # noqa: E402


def test_appconfig_parses_without_multi_agent():
    cfg = AppConfig.model_validate(
        {
            "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
        }
    )
    assert cfg.multi_agent is None


def test_appconfig_parses_multi_agent_block():
    cfg = AppConfig.model_validate(
        {
            "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
            "multi_agent": {"enabled": True, "default_session_isolation": "forked", "max_delegation_depth": 3},
        }
    )
    assert cfg.multi_agent is not None
    assert cfg.multi_agent.enabled is True
    assert cfg.multi_agent.default_session_isolation == "forked"
    assert cfg.multi_agent.max_delegation_depth == 3


def test_multi_agent_config_defaults():
    m = MultiAgentConfig()
    assert m.enabled is False
    assert m.default_session_isolation == "isolated"
    assert m.max_delegation_depth == 5
