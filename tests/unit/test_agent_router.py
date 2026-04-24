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


# ---------------------------------------------------------------------------
# Task 3: Capability constant + RunContext field
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock  # noqa: E402

from openagents.interfaces.capabilities import AGENT_ROUTER_DELEGATE, KNOWN_CAPABILITIES  # noqa: E402
from openagents.interfaces.run_context import RunContext  # noqa: E402


def test_agent_router_delegate_capability_registered():
    assert AGENT_ROUTER_DELEGATE == "agent_router.delegate"
    assert AGENT_ROUTER_DELEGATE in KNOWN_CAPABILITIES


def test_run_context_accepts_agent_router_none():
    ctx = RunContext(
        agent_id="a",
        session_id="s",
        input_text="hi",
        event_bus=MagicMock(),
        agent_router=None,
    )
    assert ctx.agent_router is None


def test_run_context_accepts_agent_router_instance():
    mock_router = MagicMock()
    ctx = RunContext(
        agent_id="a",
        session_id="s",
        input_text="hi",
        event_bus=MagicMock(),
        agent_router=mock_router,
    )
    assert ctx.agent_router is mock_router


# ---------------------------------------------------------------------------
# Task 4: DefaultAgentRouter implementation
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

from openagents.plugins.builtin.agent_router.default import DefaultAgentRouter  # noqa: E402


def _make_ctx(run_id="run-1", session_id="sess-1", parent_run_id=None):
    ctx = MagicMock()
    ctx.run_id = run_id
    ctx.session_id = session_id
    ctx.deps = None
    ctx.run_request = MagicMock(parent_run_id=parent_run_id)
    return ctx


def _make_result(output="done", run_id="child-1"):
    return RunResult(run_id=run_id, final_output=output, stop_reason=StopReason.COMPLETED)


def test_session_isolation_isolated_creates_new_session():
    router = DefaultAgentRouter(config={})
    ctx = _make_ctx(session_id="sess-1", run_id="run-1")
    session_id = router._resolve_session(ctx, "isolated")
    assert session_id != "sess-1"
    assert "run-1" in session_id


def test_session_isolation_shared_inherits_parent():
    router = DefaultAgentRouter(config={})
    ctx = _make_ctx(session_id="sess-1")
    assert router._resolve_session(ctx, "shared") == "sess-1"


def test_session_isolation_forked_contains_parent_and_run():
    router = DefaultAgentRouter(config={})
    ctx = _make_ctx(session_id="sess-1", run_id="run-abc")
    session_id = router._resolve_session(ctx, "forked")
    assert "sess-1" in session_id
    assert "run-abc" in session_id
    assert session_id != "sess-1"


@pytest.mark.asyncio
async def test_delegate_calls_run_fn_with_correct_request():
    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    result = _make_result()
    router._run_fn = AsyncMock(return_value=result)
    ctx = _make_ctx()

    returned = await router.delegate("billing_agent", "refund", ctx, session_isolation="isolated")
    assert returned is result
    call_kwargs = router._run_fn.call_args.kwargs
    req = call_kwargs["request"]
    assert req.agent_id == "billing_agent"
    assert req.input_text == "refund"
    assert req.parent_run_id == "run-1"


@pytest.mark.asyncio
async def test_transfer_raises_handoff_signal():
    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    result = _make_result()
    router._run_fn = AsyncMock(return_value=result)
    ctx = _make_ctx()

    with pytest.raises(HandoffSignal) as exc_info:
        await router.transfer("specialist", "escalate", ctx)
    assert exc_info.value.result is result


@pytest.mark.asyncio
async def test_depth_exceeded_raises():
    router = DefaultAgentRouter(config={"max_delegation_depth": 1})
    router._run_fn = AsyncMock()
    ctx = _make_ctx(run_id="deep-run")
    router._run_depths["deep-run"] = 2

    with pytest.raises(DelegationDepthExceededError):
        await router.delegate("agent_b", "hello", ctx)


@pytest.mark.asyncio
async def test_delegate_records_child_depth():
    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    child_result = _make_result(run_id="child-xyz")
    router._run_fn = AsyncMock(return_value=child_result)
    ctx = _make_ctx(run_id="parent-run")

    await router.delegate("b", "go", ctx, session_isolation="isolated")
    assert "child-xyz" in router._run_depths
    assert router._run_depths["child-xyz"] == 1
