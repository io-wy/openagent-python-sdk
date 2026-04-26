"""Unit tests for less-common _BoundTool branches.

Covers event-bus emissions (idempotency warning, approval-needed,
background submitted), finally-block exception swallowing, no-fallback
re-raise, describe/schema defaults, __getattr__ delegation, and
invoke_batch sequential fallback + error propagation.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from openagents.interfaces.tool import (
    BatchItem,
    JobHandle,
    ToolExecutionSpec,
    ToolPlugin,
)
from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
from openagents.plugins.builtin.runtime.default_runtime import _BoundTool
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class _CapturingBus:
    """Minimal event bus stub matching the emit(name, **payload) protocol."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, name: str, **payload):
        self.events.append((name, payload))


class _NonIdempotentTool(ToolPlugin):
    durable_idempotent = False

    def __init__(self):
        super().__init__(config={})

    async def invoke(self, params, context):
        return "ok"


class _StubRunRequest:
    def __init__(self, *, durable=False, approvals=None):
        self.budget = None
        self.durable = durable
        self.run_id = "run-xyz"
        self.context_hints = {"approvals": approvals} if approvals is not None else None


class _Ctx:
    def __init__(self, *, durable=False, bus=None, approvals=None):
        self.scratch: dict = {}
        self.run_request = _StubRunRequest(durable=durable, approvals=approvals)
        self.usage = None
        self.agent_id = "a"
        self.session_id = "s"
        self.event_bus = bus


# ---------------------------------------------------------------------------
# Durable idempotency warning (lines 236-252 in default_runtime.py)
# ---------------------------------------------------------------------------


def test_durable_idempotency_warning_emits_once_per_tool():
    async def run():
        tool = _NonIdempotentTool()
        bus = _CapturingBus()
        bound = _BoundTool(tool_id="t1", tool=tool, executor=SafeToolExecutor())
        ctx = _Ctx(durable=True, bus=bus)

        await bound.invoke({}, ctx)
        await bound.invoke({}, ctx)

        warnings = [e for e in bus.events if e[0] == "run.durable_idempotency_warning"]
        assert len(warnings) == 1
        payload = warnings[0][1]
        assert payload["tool_id"] == "t1"
        assert payload["run_id"] == "run-xyz"
        assert "durable_idempotent=False" in payload["hint"]

    asyncio.run(run())


def test_durable_idempotency_warning_swallows_bus_exceptions():
    class _ExplodingBus:
        async def emit(self, name, **payload):
            raise RuntimeError("bus unavailable")

    async def run():
        tool = _NonIdempotentTool()
        bound = _BoundTool(tool_id="t1", tool=tool, executor=SafeToolExecutor())
        ctx = _Ctx(durable=True, bus=_ExplodingBus())
        result = await bound.invoke({}, ctx)
        assert result.success is True

    asyncio.run(run())


def test_durable_idempotency_warning_skipped_when_tool_is_idempotent():
    class _IdemTool(ToolPlugin):
        def __init__(self):
            super().__init__(config={})

        async def invoke(self, params, context):
            return "ok"

    async def run():
        bus = _CapturingBus()
        bound = _BoundTool(tool_id="t-safe", tool=_IdemTool(), executor=SafeToolExecutor())
        ctx = _Ctx(durable=True, bus=bus)
        await bound.invoke({}, ctx)
        warnings = [e for e in bus.events if e[0] == "run.durable_idempotency_warning"]
        assert warnings == []

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Approval-needed event emission (lines 255-265)
# ---------------------------------------------------------------------------


class _ApprovalTool(ToolPlugin):
    def __init__(self):
        super().__init__(config={})

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(approval_mode="always")

    async def invoke(self, params, context):
        return "approved"


def test_approval_needed_event_emitted_when_bus_present():
    async def run():
        bus = _CapturingBus()
        bound = _BoundTool(tool_id="risk", tool=_ApprovalTool(), executor=SafeToolExecutor())
        ctx = _Ctx(bus=bus, approvals={"*": "allow"})

        result = await bound.invoke({"p": 1}, ctx)
        assert result.success is True
        names = [e[0] for e in bus.events]
        assert "tool.approval_needed" in names
        payload = dict(next(e[1] for e in bus.events if e[0] == "tool.approval_needed"))
        assert payload["tool_id"] == "risk"
        assert payload["params"] == {"p": 1}

    asyncio.run(run())


def test_approval_event_swallows_bus_exceptions():
    class _ExplodingBus:
        async def emit(self, name, **payload):
            raise RuntimeError("down")

    async def run():
        bound = _BoundTool(tool_id="risk", tool=_ApprovalTool(), executor=SafeToolExecutor())
        ctx = _Ctx(bus=_ExplodingBus(), approvals={"*": "allow"})
        result = await bound.invoke({}, ctx)
        assert result.success is True

    asyncio.run(run())


# ---------------------------------------------------------------------------
# _approvals_dict None-paths (lines 340, 343)
# ---------------------------------------------------------------------------


def test_approvals_dict_returns_none_when_no_run_request():
    class _NoReqCtx:
        def __init__(self):
            self.run_request = None

    tool = _ApprovalTool()
    bound = _BoundTool(tool_id="risk", tool=tool, executor=SafeToolExecutor())
    assert bound._approvals_dict(_NoReqCtx()) is None


def test_approvals_dict_returns_none_when_hints_not_dict():
    class _BadHintsReq:
        context_hints = "not-a-dict"

    class _Ctx2:
        run_request = _BadHintsReq()

    bound = _BoundTool(tool_id="x", tool=_ApprovalTool(), executor=SafeToolExecutor())
    assert bound._approvals_dict(_Ctx2()) is None


# ---------------------------------------------------------------------------
# requires_approval swallows exceptions (line 334-335)
# ---------------------------------------------------------------------------


def test_requires_approval_returns_false_when_check_raises():
    class _BrokenCheckTool(ToolPlugin):
        def __init__(self):
            super().__init__(config={})

        def requires_approval(self, params, context):
            raise ValueError("broken")

        async def invoke(self, params, context):
            return "ok"

    async def run():
        bound = _BoundTool(tool_id="x", tool=_BrokenCheckTool(), executor=SafeToolExecutor())
        ctx = _Ctx()
        # requires_approval raising should be treated as False → no PermanentToolError.
        result = await bound.invoke({}, ctx)
        assert result.success is True

    asyncio.run(run())


# ---------------------------------------------------------------------------
# after_invoke exception is swallowed (lines 324-326 and 440-442)
# ---------------------------------------------------------------------------


class _AfterExplodesTool(ToolPlugin):
    def __init__(self):
        super().__init__(config={})

    async def invoke(self, params, context):
        return "ok"

    async def after_invoke(self, params, context, result, exception=None):
        raise RuntimeError("after_invoke exploded")


def test_after_invoke_exception_swallowed_on_invoke():
    async def run():
        bound = _BoundTool(tool_id="t", tool=_AfterExplodesTool(), executor=SafeToolExecutor())
        ctx = _Ctx()
        result = await bound.invoke({}, ctx)
        assert result.success is True

    asyncio.run(run())


class _BgAfterExplodesTool(ToolPlugin):
    def __init__(self):
        super().__init__(config={})

    async def invoke(self, params, context):
        raise NotImplementedError

    async def invoke_background(self, params, context):
        return JobHandle(job_id="j1", tool_id="t", status="running", created_at=time.time())

    async def after_invoke(self, params, context, result, exception=None):
        raise RuntimeError("bg after exploded")


def test_after_invoke_exception_swallowed_on_background():
    async def run():
        bound = _BoundTool(tool_id="t", tool=_BgAfterExplodesTool(), executor=SafeToolExecutor())
        ctx = _Ctx(bus=_CapturingBus())
        handle = await bound.invoke_background({}, ctx)
        assert handle.job_id == "j1"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# invoke_background emits tool.background.submitted (lines 420-431)
# ---------------------------------------------------------------------------


class _BgSimpleTool(ToolPlugin):
    def __init__(self):
        super().__init__(config={})

    async def invoke(self, params, context):
        raise NotImplementedError

    async def invoke_background(self, params, context):
        return JobHandle(job_id="bg-1", tool_id="t", status="running", created_at=time.time())


def test_background_submitted_event_emitted():
    async def run():
        bus = _CapturingBus()
        bound = _BoundTool(tool_id="t", tool=_BgSimpleTool(), executor=SafeToolExecutor())
        ctx = _Ctx(bus=bus)
        handle = await bound.invoke_background({}, ctx)
        assert handle.job_id == "bg-1"
        evts = [e for e in bus.events if e[0] == "tool.background.submitted"]
        assert len(evts) == 1
        payload = evts[0][1]
        assert payload["tool_id"] == "t"
        assert payload["job_id"] == "bg-1"

    asyncio.run(run())


def test_background_event_swallows_bus_exceptions():
    class _ExplodingBus:
        async def emit(self, name, **payload):
            raise RuntimeError("nope")

    async def run():
        bound = _BoundTool(tool_id="t", tool=_BgSimpleTool(), executor=SafeToolExecutor())
        ctx = _Ctx(bus=_ExplodingBus())
        handle = await bound.invoke_background({}, ctx)
        assert handle.job_id == "bg-1"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# fallback re-raises when tool has no fallback method (line 454)
# ---------------------------------------------------------------------------


def test_fallback_reraises_original_error_when_tool_lacks_fallback():
    class _NoFallbackTool(ToolPlugin):
        def __init__(self):
            super().__init__(config={})

        async def invoke(self, params, context):
            return "ok"

    async def run():
        bound = _BoundTool(tool_id="t", tool=_NoFallbackTool(), executor=SafeToolExecutor())
        original = ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            await bound.fallback(original, {}, context=None)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# describe / schema default return when tool doesn't implement them (lines 460, 466)
# ---------------------------------------------------------------------------


def test_describe_returns_default_shape_when_tool_omits_method():
    class _PlainTool(ToolPlugin):
        def __init__(self):
            super().__init__(config={})

        async def invoke(self, params, context):
            return None

        # Avoid inheriting ToolPlugin's default describe so the fallback
        # branch in _BoundTool.describe() is actually exercised.
        describe = None
        schema = None

    bound = _BoundTool(tool_id="plain", tool=_PlainTool(), executor=SafeToolExecutor())
    d = bound.describe()
    assert d == {"name": "plain", "description": "", "parameters": {"type": "object"}}
    s = bound.schema()
    assert s == {"type": "object", "properties": {}, "required": []}


# ---------------------------------------------------------------------------
# __getattr__ delegation (line 469)
# ---------------------------------------------------------------------------


def test_bound_tool_getattr_delegates_to_wrapped_tool():
    class _TaggedTool(ToolPlugin):
        CUSTOM_FLAG = "hello"

        def __init__(self):
            super().__init__(config={})

        async def invoke(self, params, context):
            return None

    bound = _BoundTool(tool_id="tag", tool=_TaggedTool(), executor=SafeToolExecutor())
    # __getattr__ is only invoked for attributes _BoundTool does not
    # define itself; CUSTOM_FLAG satisfies that.
    assert bound.CUSTOM_FLAG == "hello"


# ---------------------------------------------------------------------------
# invoke_batch sequential fallback + error propagation (lines 388, 395-402)
# ---------------------------------------------------------------------------


class _BatchableTool(ToolPlugin):
    """Tool whose single-invoke success/fail depends on params["ok"]."""

    def __init__(self):
        super().__init__(config={})

    async def invoke(self, params, context):
        if not params.get("ok", True):
            raise RuntimeError(f"refused:{params.get('id')}")
        return {"echo": params.get("id")}


class _ExecutorWithoutBatch:
    """Executor exposing only execute() → forces sequential fallback at line 388."""

    async def execute(self, request):
        return await SafeToolExecutor().execute(request)


def test_invoke_batch_uses_sequential_fallback_when_executor_lacks_execute_batch():
    async def run():
        tool = _BatchableTool()
        bound = _BoundTool(tool_id="b", tool=tool, executor=_ExecutorWithoutBatch())
        items = [
            BatchItem(item_id="a", params={"id": "a"}),
            BatchItem(item_id="b", params={"id": "b"}),
        ]
        results = await bound.invoke_batch(items, context=None)
        assert len(results) == 2
        assert [r.item_id for r in results] == ["a", "b"]
        assert all(r.success for r in results)
        assert results[0].data == {"echo": "a"}

    asyncio.run(run())


def test_invoke_batch_empty_items_short_circuits():
    async def run():
        bound = _BoundTool(tool_id="b", tool=_BatchableTool(), executor=SafeToolExecutor())
        assert await bound.invoke_batch([], context=None) == []

    asyncio.run(run())


def test_invoke_batch_preserves_per_item_errors():
    async def run():
        tool = _BatchableTool()
        bound = _BoundTool(tool_id="b", tool=tool, executor=_ExecutorWithoutBatch())
        items = [
            BatchItem(item_id="good", params={"id": "good", "ok": True}),
            BatchItem(item_id="bad", params={"id": "bad", "ok": False}),
        ]
        results = await bound.invoke_batch(items, context=None)
        by_id = {r.item_id: r for r in results}
        assert by_id["good"].success is True
        assert by_id["bad"].success is False
        assert by_id["bad"].exception is not None
        assert "refused:bad" in str(by_id["bad"].exception)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Integration smoke with a real AsyncEventBus so the emit() path runs through
# the actual EventBusPlugin contract (belt-and-suspenders against stub drift).
# ---------------------------------------------------------------------------


def test_approval_event_via_real_async_event_bus():
    async def run():
        bus = AsyncEventBus()
        received: list[tuple[str, dict]] = []

        async def handler(event):
            received.append((event.name, dict(event.payload or {})))

        bus.subscribe("*", handler)
        try:
            bound = _BoundTool(tool_id="risk", tool=_ApprovalTool(), executor=SafeToolExecutor())
            ctx = _Ctx(bus=bus, approvals={"*": "allow"})
            result = await bound.invoke({}, ctx)
            assert result.success is True
        finally:
            await bus.close()

        names = [name for name, _ in received]
        assert "tool.approval_needed" in names

    asyncio.run(run())
