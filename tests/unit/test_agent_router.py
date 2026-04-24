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
