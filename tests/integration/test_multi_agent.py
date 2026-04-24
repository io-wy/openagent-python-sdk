from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openagents.interfaces.agent_router import DelegationDepthExceededError, HandoffSignal
from openagents.interfaces.runtime import RunResult, StopReason
from openagents.runtime.runtime import Runtime

_CONFIG = {
    "agents": [
        {
            "id": "orchestrator",
            "name": "Orchestrator",
            "memory": {"type": "buffer"},
            "pattern": {"type": "react"},
            "llm": {"provider": "mock"},
        },
        {
            "id": "specialist",
            "name": "Specialist",
            "memory": {"type": "buffer"},
            "pattern": {"type": "react"},
            "llm": {"provider": "mock"},
        },
    ],
    "multi_agent": {"enabled": True, "default_session_isolation": "isolated"},
}


def _make_child_result(output: str, run_id: str = "child-1") -> RunResult:
    return RunResult(run_id=run_id, final_output=output, stop_reason=StopReason.COMPLETED)


def _make_ctx(run_id="run-1", session_id="sess-1", delegation_depth=0):
    from openagents.interfaces.agent_router import DELEGATION_DEPTH_KEY

    ctx = MagicMock()
    ctx.run_id = run_id
    ctx.session_id = session_id
    ctx.deps = None
    metadata: dict = {}
    if delegation_depth:
        metadata[DELEGATION_DEPTH_KEY] = delegation_depth
    ctx.run_request = MagicMock(parent_run_id=None, metadata=metadata)
    return ctx


@pytest.mark.asyncio
async def test_delegate_returns_child_result():
    runtime = Runtime.from_dict(_CONFIG)
    router = runtime._runtime._agent_router
    assert router is not None

    child_result = _make_child_result("specialist done")
    router._run_fn = AsyncMock(return_value=child_result)
    ctx = _make_ctx()

    result = await router.delegate("specialist", "do specialist task", ctx)
    assert result.final_output == "specialist done"
    req = router._run_fn.call_args.kwargs["request"]
    assert req.agent_id == "specialist"
    assert req.parent_run_id == "run-1"
    assert req.session_id != "sess-1"  # isolated → new session


@pytest.mark.asyncio
async def test_transfer_raises_handoff_signal_with_child_result():
    runtime = Runtime.from_dict(_CONFIG)
    router = runtime._runtime._agent_router
    child_result = _make_child_result("transferred output", run_id="child-2")
    router._run_fn = AsyncMock(return_value=child_result)
    ctx = _make_ctx()

    with pytest.raises(HandoffSignal) as exc_info:
        await router.transfer("specialist", "escalate", ctx)
    assert exc_info.value.result.final_output == "transferred output"


@pytest.mark.asyncio
async def test_shared_isolation_passes_parent_session():
    runtime = Runtime.from_dict(_CONFIG)
    router = runtime._runtime._agent_router
    router._run_fn = AsyncMock(return_value=_make_child_result("x"))
    ctx = _make_ctx(session_id="shared-sess")

    await router.delegate("specialist", "hi", ctx, session_isolation="shared")
    req = router._run_fn.call_args.kwargs["request"]
    assert req.session_id == "shared-sess"


@pytest.mark.asyncio
async def test_forked_isolation_creates_distinct_session():
    runtime = Runtime.from_dict(_CONFIG)
    router = runtime._runtime._agent_router
    router._run_fn = AsyncMock(return_value=_make_child_result("x"))
    ctx = _make_ctx(session_id="parent-sess", run_id="parent-run")

    await router.delegate("specialist", "hi", ctx, session_isolation="forked")
    req = router._run_fn.call_args.kwargs["request"]
    assert "parent-sess" in req.session_id
    assert "parent-run" in req.session_id
    assert req.session_id != "parent-sess"


@pytest.mark.asyncio
async def test_delegation_depth_limit_enforced():
    cfg = dict(_CONFIG)
    cfg["multi_agent"] = {"enabled": True, "max_delegation_depth": 1}
    runtime = Runtime.from_dict(cfg)
    router = runtime._runtime._agent_router
    router._run_fn = AsyncMock()
    # Simulate already-deep chain via request metadata (the new depth channel).
    ctx = _make_ctx(run_id="deep-run", delegation_depth=2)

    with pytest.raises(DelegationDepthExceededError) as exc_info:
        await router.delegate("specialist", "hi", ctx)
    assert exc_info.value.depth == 2
    assert exc_info.value.limit == 1


@pytest.mark.asyncio
async def test_child_depth_propagated_via_request_metadata():
    from openagents.interfaces.agent_router import DELEGATION_DEPTH_KEY

    runtime = Runtime.from_dict(_CONFIG)
    router = runtime._runtime._agent_router
    child_result = _make_child_result("done", run_id="child-run-abc")
    router._run_fn = AsyncMock(return_value=child_result)
    ctx = _make_ctx(run_id="root-run")

    await router.delegate("specialist", "go", ctx)
    req = router._run_fn.call_args.kwargs["request"]
    assert req.metadata[DELEGATION_DEPTH_KEY] == 1


@pytest.mark.asyncio
async def test_delegate_unknown_agent_raises_agent_not_found():
    from openagents.interfaces.agent_router import AgentNotFoundError

    runtime = Runtime.from_dict(_CONFIG)
    ctx = _make_ctx()
    router = runtime._runtime._agent_router

    with pytest.raises(AgentNotFoundError) as exc_info:
        await router.delegate("does_not_exist", "hi", ctx)
    assert exc_info.value.agent_id == "does_not_exist"


@pytest.mark.asyncio
async def test_forked_isolation_copies_parent_history():
    """With real in-memory session manager, forked child sees parent messages."""
    runtime = Runtime.from_dict(_CONFIG)
    router = runtime._runtime._agent_router
    # Seed parent session with a message
    await runtime.session_manager.append_message("parent-sess", {"role": "user", "content": "hi"})

    captured = {}

    async def fake_run(*, request):
        captured["session_id"] = request.session_id
        captured["messages"] = await runtime.session_manager.load_messages(request.session_id)
        return _make_child_result("ok", run_id="fork-child")

    router._run_fn = fake_run
    ctx = _make_ctx(session_id="parent-sess", run_id="parent-run")

    await router.delegate("specialist", "go", ctx, session_isolation="forked")
    assert captured["session_id"] != "parent-sess"
    assert [m["content"] for m in captured["messages"]] == ["hi"]
