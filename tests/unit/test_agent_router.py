from __future__ import annotations

from openagents.interfaces.agent_router import (
    DELEGATION_DEPTH_KEY,  # noqa: F401 — re-exported for tests below
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
    assert m.default_child_budget is None


def test_multi_agent_config_parses_default_child_budget():
    from openagents.interfaces.runtime import RunBudget

    m = MultiAgentConfig.model_validate(
        {
            "enabled": True,
            "default_child_budget": {"max_steps": 5, "max_cost_usd": 0.1},
        }
    )
    assert isinstance(m.default_child_budget, RunBudget)
    assert m.default_child_budget.max_steps == 5
    assert m.default_child_budget.max_cost_usd == 0.1


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


def _make_ctx(run_id="run-1", session_id="sess-1", parent_run_id=None, delegation_depth=0):

    ctx = MagicMock()
    ctx.run_id = run_id
    ctx.session_id = session_id
    ctx.deps = None
    metadata: dict = {}
    if delegation_depth:
        metadata[DELEGATION_DEPTH_KEY] = delegation_depth
    ctx.run_request = MagicMock(parent_run_id=parent_run_id, metadata=metadata)
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
    ctx = _make_ctx(run_id="deep-run", delegation_depth=2)

    with pytest.raises(DelegationDepthExceededError):
        await router.delegate("agent_b", "hello", ctx)


@pytest.mark.asyncio
async def test_delegate_records_child_depth():

    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    child_result = _make_result(run_id="child-xyz")
    router._run_fn = AsyncMock(return_value=child_result)
    ctx = _make_ctx(run_id="parent-run")

    await router.delegate("b", "go", ctx, session_isolation="isolated")
    req = router._run_fn.call_args.kwargs["request"]
    assert req.metadata[DELEGATION_DEPTH_KEY] == 1


@pytest.mark.asyncio
async def test_delegate_increments_depth_from_parent_metadata():

    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    router._run_fn = AsyncMock(return_value=_make_result(run_id="gc"))
    ctx = _make_ctx(run_id="mid-run", delegation_depth=2)

    await router.delegate("c", "go", ctx, session_isolation="isolated")
    req = router._run_fn.call_args.kwargs["request"]
    assert req.metadata[DELEGATION_DEPTH_KEY] == 3


def test_router_keeps_no_per_run_state():
    """After many sequential delegations the router must not accumulate per-run keys."""
    router = DefaultAgentRouter(config={})
    for attr in vars(router).values():
        if isinstance(attr, dict):
            # run_depths would have grown; the only dicts on the router now
            # are typed config references, never keyed by run_id.
            assert all(not key.startswith("child-") and not key.startswith("run-") for key in attr)


# ---------------------------------------------------------------------------
# AgentNotFoundError pre-check (G1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_raises_agent_not_found_when_registry_rejects():
    router = DefaultAgentRouter(config={})
    router._run_fn = AsyncMock()
    router._agent_exists = lambda aid: aid == "known"
    ctx = _make_ctx()

    with pytest.raises(AgentNotFoundError) as exc_info:
        await router.delegate("nope", "hi", ctx)
    assert exc_info.value.agent_id == "nope"
    router._run_fn.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_raises_agent_not_found_not_handoff():
    router = DefaultAgentRouter(config={})
    router._run_fn = AsyncMock()
    router._agent_exists = lambda aid: False
    ctx = _make_ctx()

    with pytest.raises(AgentNotFoundError):
        await router.transfer("nope", "hi", ctx)


# ---------------------------------------------------------------------------
# default_child_budget fallback (G4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_budget_wins_over_default_child_budget():
    from openagents.interfaces.runtime import RunBudget

    router = DefaultAgentRouter(config={"default_child_budget": {"max_steps": 5, "max_cost_usd": 0.5}})
    router._run_fn = AsyncMock(return_value=_make_result())
    ctx = _make_ctx()

    await router.delegate("b", "x", ctx, budget=RunBudget(max_steps=2))
    req = router._run_fn.call_args.kwargs["request"]
    assert req.budget.max_steps == 2


@pytest.mark.asyncio
async def test_default_child_budget_fallback_when_explicit_is_none():
    router = DefaultAgentRouter(config={"default_child_budget": {"max_steps": 5, "max_cost_usd": 0.1}})
    router._run_fn = AsyncMock(return_value=_make_result())
    ctx = _make_ctx()

    await router.delegate("b", "x", ctx)
    req = router._run_fn.call_args.kwargs["request"]
    assert req.budget is not None
    assert req.budget.max_steps == 5
    assert req.budget.max_cost_usd == 0.1


@pytest.mark.asyncio
async def test_no_budget_when_neither_explicit_nor_default():
    router = DefaultAgentRouter(config={})
    router._run_fn = AsyncMock(return_value=_make_result())
    ctx = _make_ctx()

    await router.delegate("b", "x", ctx)
    req = router._run_fn.call_args.kwargs["request"]
    assert req.budget is None


# ---------------------------------------------------------------------------
# Misconfiguration error paths
# ---------------------------------------------------------------------------


def test_coerce_budget_rejects_invalid_type():
    with pytest.raises(TypeError):
        DefaultAgentRouter(config={"default_child_budget": "not a budget"})


def test_coerce_budget_accepts_run_budget_instance():
    from openagents.interfaces.runtime import RunBudget

    rb = RunBudget(max_steps=7)
    router = DefaultAgentRouter(config={"default_child_budget": rb})
    assert router._default_child_budget is rb


@pytest.mark.asyncio
async def test_delegate_errors_when_run_fn_missing():
    router = DefaultAgentRouter(config={})
    # _run_fn left as None simulates bad Runtime wiring.
    ctx = _make_ctx()
    with pytest.raises(RuntimeError, match="_run_fn not set"):
        await router.delegate("b", "x", ctx)


@pytest.mark.asyncio
async def test_forked_errors_when_session_manager_missing():
    router = DefaultAgentRouter(config={})
    router._run_fn = AsyncMock()
    ctx = _make_ctx()
    with pytest.raises(RuntimeError, match="_session_manager not set"):
        await router.delegate("b", "x", ctx, session_isolation="forked")


def test_current_depth_handles_non_int_metadata_gracefully():
    router = DefaultAgentRouter(config={})
    ctx = _make_ctx()

    ctx.run_request.metadata = {DELEGATION_DEPTH_KEY: ["not", "an", "int"]}
    assert router._current_depth(ctx) == 0


# ---------------------------------------------------------------------------
# Task 5: Registry + loader
# ---------------------------------------------------------------------------


def test_default_agent_router_in_registry():
    from openagents.plugins.registry import get_builtin_plugin_class

    cls = get_builtin_plugin_class("agent_router", "default")
    assert cls is DefaultAgentRouter


def test_load_agent_router_plugin_returns_none_when_disabled():
    from openagents.plugins.loader import load_agent_router_plugin

    assert load_agent_router_plugin(None) is None


def test_load_agent_router_plugin_returns_router_when_enabled():
    from openagents.config.schema import MultiAgentConfig
    from openagents.plugins.loader import load_agent_router_plugin

    cfg = MultiAgentConfig(enabled=True, max_delegation_depth=3)
    router = load_agent_router_plugin(cfg)
    assert isinstance(router, DefaultAgentRouter)
    assert router._max_depth == 3


# ---------------------------------------------------------------------------
# Task 6: DefaultRuntime wiring
# ---------------------------------------------------------------------------


def test_default_runtime_has_agent_router_field():
    from openagents.plugins.builtin.runtime.default_runtime import DefaultRuntime

    runtime = DefaultRuntime()
    assert hasattr(runtime, "_agent_router")
    assert runtime._agent_router is None


@pytest.mark.asyncio
async def test_handoff_signal_caught_by_default_runtime():
    """DefaultRuntime.run() must catch HandoffSignal and return its result."""
    from contextlib import asynccontextmanager

    from openagents.interfaces.runtime import RunRequest
    from openagents.plugins.builtin.runtime.default_runtime import DefaultRuntime

    child_result = RunResult(run_id="child", final_output="child output", stop_reason=StopReason.COMPLETED)

    mock_pattern = MagicMock()
    mock_pattern.execute = AsyncMock(side_effect=HandoffSignal(child_result))
    mock_pattern.setup = AsyncMock()
    mock_pattern.context = MagicMock()
    mock_pattern.context.scratch = {}

    mock_plugins = MagicMock()
    mock_plugins.pattern = mock_pattern
    mock_plugins.memory = MagicMock()
    mock_plugins.memory.capabilities = set()
    mock_plugins.tool_executor = None
    mock_plugins.context_assembler = None
    mock_plugins.tools = {}

    runtime = DefaultRuntime()
    mock_bus = AsyncMock()
    mock_bus.subscribe = MagicMock()
    mock_bus.unsubscribe = MagicMock()
    runtime._event_bus = mock_bus

    @asynccontextmanager
    async def fake_session(session_id):
        yield {}

    mock_session = MagicMock()
    mock_session.session = fake_session
    mock_session.append_message = AsyncMock()
    mock_session.save_artifact = AsyncMock()
    mock_session.load_messages = AsyncMock(return_value=[])
    mock_session.list_artifacts = AsyncMock(return_value=[])
    runtime._session_manager = mock_session

    mock_agent = MagicMock()
    mock_agent.id = "test_agent"
    mock_agent.llm = None
    mock_agent.runtime = MagicMock(max_steps=16, step_timeout_ms=30000)
    mock_agent.memory = MagicMock(on_error="continue")

    request = RunRequest(agent_id="test_agent", session_id="s1", input_text="hi", run_id="parent-run")

    result = await runtime.run(
        request=request,
        app_config=MagicMock(agents=[mock_agent]),
        agents_by_id={"test_agent": mock_agent},
        agent_plugins=mock_plugins,
    )
    assert result.final_output == "child output"
    assert result.stop_reason == StopReason.COMPLETED.value
    assert result.metadata["handoff_from"] == "child"


# ---------------------------------------------------------------------------
# Task 7: Runtime facade wiring
# ---------------------------------------------------------------------------


def test_runtime_injects_agent_router_when_enabled():
    from openagents.runtime.runtime import Runtime

    runtime = Runtime.from_dict(
        {
            "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
            "multi_agent": {"enabled": True},
        }
    )
    assert isinstance(runtime._runtime._agent_router, DefaultAgentRouter)
    assert runtime._runtime._agent_router._run_fn is not None


def test_runtime_no_agent_router_when_absent():
    from openagents.runtime.runtime import Runtime

    runtime = Runtime.from_dict(
        {
            "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
        }
    )
    assert runtime._runtime._agent_router is None


def test_runtime_no_agent_router_when_disabled():
    from openagents.runtime.runtime import Runtime

    runtime = Runtime.from_dict(
        {
            "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
            "multi_agent": {"enabled": False},
        }
    )
    assert runtime._runtime._agent_router is None
