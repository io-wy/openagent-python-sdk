# Multi-Agent Agent Router Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `agent_router` as the 9th kernel seam, enabling Orchestrator (delegate) and Handoff (transfer) patterns between agents with configurable session isolation and depth limiting.

**Architecture:** New `AgentRouterPlugin` interface in `openagents/interfaces/`; `DefaultAgentRouter` builtin holds `_run_fn = runtime.run_detailed` injected by `Runtime.__init__` post-construction; `DefaultRuntime` gains `_agent_router` field, injects it into `RunContext`, and catches `HandoffSignal` (a `BaseException`) before the general `Exception` handler to return the child run's result as the parent's final output.

**Tech Stack:** Python 3.10+, pydantic v2, asyncio, existing `RunRequest`/`RunResult`/`RunContext` interfaces.

---

## File Map

| File | Change |
|------|--------|
| `openagents/interfaces/agent_router.py` | **New** — `HandoffSignal`, error types, `AgentRouterPlugin` |
| `openagents/config/schema.py` | Edit — add `MultiAgentConfig`, add `multi_agent` field to `AppConfig` |
| `openagents/interfaces/capabilities.py` | Edit — add `AGENT_ROUTER_DELEGATE` constant |
| `openagents/interfaces/run_context.py` | Edit — add `agent_router: Any \| None = None` |
| `openagents/plugins/builtin/agent_router/__init__.py` | **New** — package marker |
| `openagents/plugins/builtin/agent_router/default.py` | **New** — `DefaultAgentRouter` |
| `openagents/plugins/registry.py` | Edit — register `"agent_router": {"default": DefaultAgentRouter}` |
| `openagents/plugins/loader.py` | Edit — add `load_agent_router_plugin()` |
| `openagents/plugins/builtin/runtime/default_runtime.py` | Edit — `_agent_router` field, ctx injection, `HandoffSignal` catch |
| `openagents/runtime/runtime.py` | Edit — create and wire router in `__init__` |
| `tests/unit/test_agent_router.py` | **New** — unit tests |
| `tests/integration/test_multi_agent.py` | **New** — end-to-end delegate + transfer |

---

## Task 1: Interface + Error Types

**Files:**
- Create: `openagents/interfaces/agent_router.py`
- Create: `tests/unit/test_agent_router.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_agent_router.py
from __future__ import annotations
import pytest
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
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: `ImportError: cannot import name 'HandoffSignal' from 'openagents.interfaces.agent_router'`

- [ ] **Step 3: Create the interface file**

```python
# openagents/interfaces/agent_router.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NoReturn

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunBudget, RunResult


class HandoffSignal(BaseException):
    """Raised by AgentRouterPlugin.transfer() to terminate the parent run with the child's result."""

    def __init__(self, result: "RunResult") -> None:
        super().__init__()
        self.result = result


class AgentNotFoundError(Exception):
    """Raised when the target agent_id is not found in the loaded config."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' not found in config")
        self.agent_id = agent_id


class DelegationDepthExceededError(Exception):
    """Raised when max_delegation_depth is exceeded to prevent infinite recursion."""

    def __init__(self, depth: int, limit: int) -> None:
        super().__init__(f"Delegation depth {depth} exceeds limit {limit}")
        self.depth = depth
        self.limit = limit


class AgentRouterPlugin:
    """Protocol for the agent_router seam.

    Implementations must provide delegate() and transfer().
    """

    async def delegate(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] = "isolated",
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> "RunResult":
        """Invoke a sub-agent and await its result before continuing."""
        raise NotImplementedError

    async def transfer(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] = "isolated",
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> NoReturn:
        """Transfer control to another agent permanently. Raises HandoffSignal."""
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add openagents/interfaces/agent_router.py tests/unit/test_agent_router.py
git commit -m "feat: add AgentRouterPlugin interface and HandoffSignal/error types"
```

---

## Task 2: Config Schema

**Files:**
- Modify: `openagents/config/schema.py`
- Modify: `tests/unit/test_agent_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_agent_router.py`:

```python
from openagents.config.schema import AppConfig, MultiAgentConfig


def test_appconfig_parses_without_multi_agent():
    cfg = AppConfig.model_validate({
        "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
    })
    assert cfg.multi_agent is None


def test_appconfig_parses_multi_agent_block():
    cfg = AppConfig.model_validate({
        "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
        "multi_agent": {"enabled": True, "default_session_isolation": "forked", "max_delegation_depth": 3},
    })
    assert cfg.multi_agent is not None
    assert cfg.multi_agent.enabled is True
    assert cfg.multi_agent.default_session_isolation == "forked"
    assert cfg.multi_agent.max_delegation_depth == 3


def test_multi_agent_config_defaults():
    m = MultiAgentConfig()
    assert m.enabled is False
    assert m.default_session_isolation == "isolated"
    assert m.max_delegation_depth == 5
```

- [ ] **Step 2: Run tests to see them fail**

```
uv run pytest tests/unit/test_agent_router.py::test_appconfig_parses_without_multi_agent tests/unit/test_agent_router.py::test_appconfig_parses_multi_agent_block tests/unit/test_agent_router.py::test_multi_agent_config_defaults -v
```
Expected: `ImportError: cannot import name 'MultiAgentConfig'`

- [ ] **Step 3: Add `MultiAgentConfig` and `AppConfig.multi_agent`**

In `openagents/config/schema.py`, add after the `DiagnosticsRef` class (after line ~117):

```python
class MultiAgentConfig(BaseModel):
    """Top-level multi_agent configuration block."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_session_isolation: Literal["shared", "isolated", "forked"] = "isolated"
    max_delegation_depth: int = 5
```

In `AppConfig` (around line 283), add the field after `diagnostics`:

```python
multi_agent: MultiAgentConfig | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add openagents/config/schema.py tests/unit/test_agent_router.py
git commit -m "feat: add MultiAgentConfig schema and AppConfig.multi_agent field"
```

---

## Task 3: Capability Constant + RunContext Field

**Files:**
- Modify: `openagents/interfaces/capabilities.py`
- Modify: `openagents/interfaces/run_context.py`
- Modify: `tests/unit/test_agent_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_agent_router.py`:

```python
from unittest.mock import MagicMock
from openagents.interfaces.capabilities import AGENT_ROUTER_DELEGATE, KNOWN_CAPABILITIES
from openagents.interfaces.run_context import RunContext


def test_agent_router_delegate_capability_registered():
    assert AGENT_ROUTER_DELEGATE == "agent_router.delegate"
    assert AGENT_ROUTER_DELEGATE in KNOWN_CAPABILITIES


def test_run_context_accepts_agent_router_none():
    ctx = RunContext(
        agent_id="a", session_id="s", input_text="hi",
        event_bus=MagicMock(), agent_router=None,
    )
    assert ctx.agent_router is None


def test_run_context_accepts_agent_router_instance():
    mock_router = MagicMock()
    ctx = RunContext(
        agent_id="a", session_id="s", input_text="hi",
        event_bus=MagicMock(), agent_router=mock_router,
    )
    assert ctx.agent_router is mock_router
```

- [ ] **Step 2: Run tests to see them fail**

```
uv run pytest tests/unit/test_agent_router.py::test_agent_router_delegate_capability_registered tests/unit/test_agent_router.py::test_run_context_accepts_agent_router_none tests/unit/test_agent_router.py::test_run_context_accepts_agent_router_instance -v
```
Expected: `ImportError: cannot import name 'AGENT_ROUTER_DELEGATE'`

- [ ] **Step 3: Add capability constant to `capabilities.py`**

In `openagents/interfaces/capabilities.py`, after `DIAG_EXPORT = "diagnostics.export"`:

```python
AGENT_ROUTER_DELEGATE = "agent_router.delegate"
```

Add `AGENT_ROUTER_DELEGATE` to the `KNOWN_CAPABILITIES` set.

- [ ] **Step 4: Add `agent_router` field to `RunContext`**

In `openagents/interfaces/run_context.py`, add after `artifacts: list["RunArtifact"] = Field(default_factory=list)`:

```python
agent_router: Any | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: `10 passed`

- [ ] **Step 6: Commit**

```bash
git add openagents/interfaces/capabilities.py openagents/interfaces/run_context.py tests/unit/test_agent_router.py
git commit -m "feat: add AGENT_ROUTER_DELEGATE capability and RunContext.agent_router field"
```

---

## Task 4: `DefaultAgentRouter` Implementation

**Files:**
- Create: `openagents/plugins/builtin/agent_router/__init__.py`
- Create: `openagents/plugins/builtin/agent_router/default.py`
- Modify: `tests/unit/test_agent_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_agent_router.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock
from openagents.plugins.builtin.agent_router.default import DefaultAgentRouter
from openagents.interfaces.agent_router import DelegationDepthExceededError, HandoffSignal
from openagents.interfaces.runtime import RunResult, StopReason


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


def test_delegate_calls_run_fn_with_correct_request():
    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    result = _make_result()
    router._run_fn = AsyncMock(return_value=result)
    ctx = _make_ctx()

    returned = asyncio.get_event_loop().run_until_complete(
        router.delegate("billing_agent", "refund", ctx, session_isolation="isolated")
    )
    assert returned is result
    call_kwargs = router._run_fn.call_args.kwargs
    req = call_kwargs["request"]
    assert req.agent_id == "billing_agent"
    assert req.input_text == "refund"
    assert req.parent_run_id == "run-1"


def test_transfer_raises_handoff_signal():
    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    result = _make_result()
    router._run_fn = AsyncMock(return_value=result)
    ctx = _make_ctx()

    with pytest.raises(HandoffSignal) as exc_info:
        asyncio.get_event_loop().run_until_complete(
            router.transfer("specialist", "escalate", ctx)
        )
    assert exc_info.value.result is result


def test_depth_exceeded_raises():
    router = DefaultAgentRouter(config={"max_delegation_depth": 1})
    router._run_fn = AsyncMock()
    ctx = _make_ctx(run_id="deep-run")
    # inject synthetic depth > limit
    router._run_depths["deep-run"] = 2

    with pytest.raises(DelegationDepthExceededError):
        asyncio.get_event_loop().run_until_complete(
            router.delegate("agent_b", "hello", ctx)
        )


def test_delegate_records_child_depth():
    router = DefaultAgentRouter(config={"max_delegation_depth": 5})
    child_result = _make_result(run_id="child-xyz")
    router._run_fn = AsyncMock(return_value=child_result)
    ctx = _make_ctx(run_id="parent-run")

    asyncio.get_event_loop().run_until_complete(
        router.delegate("b", "go", ctx, session_isolation="isolated")
    )
    # child depth must be recorded so grandchildren can check it
    assert "child-xyz" in router._run_depths
    assert router._run_depths["child-xyz"] == 1
```

- [ ] **Step 2: Run tests to see them fail**

```
uv run pytest tests/unit/test_agent_router.py -k "test_session_isolation or test_delegate or test_transfer or test_depth" -v
```
Expected: `ModuleNotFoundError: No module named 'openagents.plugins.builtin.agent_router'`

- [ ] **Step 3: Create the package marker**

```python
# openagents/plugins/builtin/agent_router/__init__.py
```
(empty file)

- [ ] **Step 4: Implement `DefaultAgentRouter`**

```python
# openagents/plugins/builtin/agent_router/default.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal, NoReturn
from uuid import uuid4

from openagents.interfaces.agent_router import (
    AgentRouterPlugin,
    DelegationDepthExceededError,
    HandoffSignal,
)
from openagents.interfaces.runtime import RunRequest

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunBudget, RunResult


class DefaultAgentRouter(AgentRouterPlugin):
    """Default agent_router seam implementation.

    _run_fn must be set to runtime.run_detailed by Runtime.__init__ after
    load_runtime_components() returns. depth tracking uses _run_depths keyed
    by run_id so nested delegation chains can enforce max_delegation_depth.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._max_depth: int = int(cfg.get("max_delegation_depth", 5))
        self._default_isolation: Literal["shared", "isolated", "forked"] = cfg.get(
            "default_session_isolation", "isolated"
        )
        self._run_fn: Callable | None = None
        self._run_depths: dict[str, int] = {}

    async def delegate(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] | None = None,
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> "RunResult":
        isolation = session_isolation if session_isolation is not None else self._default_isolation
        self._check_depth(ctx)
        if self._run_fn is None:
            raise RuntimeError(
                "DefaultAgentRouter._run_fn not set; Runtime wiring incomplete. "
                "Ensure Runtime.__init__ sets agent_router._run_fn = self.run_detailed."
            )
        child_request = RunRequest(
            agent_id=agent_id,
            session_id=self._resolve_session(ctx, isolation),
            input_text=input_text,
            parent_run_id=ctx.run_id,
            budget=budget,
            deps=deps if deps is not None else ctx.deps,
        )
        result = await self._run_fn(request=child_request)
        # record depth so children of this delegation can enforce limits
        parent_depth = self._run_depths.get(ctx.run_id, 0)
        self._run_depths[child_request.run_id] = parent_depth + 1
        return result

    async def transfer(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] | None = None,
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> NoReturn:
        result = await self.delegate(
            agent_id, input_text, ctx,
            session_isolation=session_isolation,
            budget=budget,
            deps=deps,
        )
        raise HandoffSignal(result)

    def _resolve_session(self, ctx: "RunContext", isolation: str) -> str:
        if isolation == "shared":
            return ctx.session_id
        if isolation == "forked":
            return f"{ctx.session_id}:fork:{ctx.run_id}"
        return f"child:{ctx.run_id}:{uuid4().hex[:8]}"

    def _check_depth(self, ctx: "RunContext") -> None:
        depth = self._run_depths.get(ctx.run_id, 0)
        if depth >= self._max_depth:
            raise DelegationDepthExceededError(depth=depth, limit=self._max_depth)
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add openagents/plugins/builtin/agent_router/ tests/unit/test_agent_router.py
git commit -m "feat: implement DefaultAgentRouter with delegate, transfer, session isolation, and depth tracking"
```

---

## Task 5: Registry + Loader

**Files:**
- Modify: `openagents/plugins/registry.py`
- Modify: `openagents/plugins/loader.py`
- Modify: `tests/unit/test_agent_router.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_agent_router.py`:

```python
from openagents.plugins.registry import get_builtin_plugin_class


def test_default_agent_router_in_registry():
    from openagents.plugins.builtin.agent_router.default import DefaultAgentRouter
    cls = get_builtin_plugin_class("agent_router", "default")
    assert cls is DefaultAgentRouter


def test_load_agent_router_plugin_returns_none_when_disabled():
    from openagents.plugins.loader import load_agent_router_plugin
    assert load_agent_router_plugin(None) is None


def test_load_agent_router_plugin_returns_router_when_enabled():
    from openagents.plugins.loader import load_agent_router_plugin
    from openagents.config.schema import MultiAgentConfig
    from openagents.plugins.builtin.agent_router.default import DefaultAgentRouter
    cfg = MultiAgentConfig(enabled=True, max_delegation_depth=3)
    router = load_agent_router_plugin(cfg)
    assert isinstance(router, DefaultAgentRouter)
    assert router._max_depth == 3
```

- [ ] **Step 2: Run tests to see them fail**

```
uv run pytest tests/unit/test_agent_router.py::test_default_agent_router_in_registry tests/unit/test_agent_router.py::test_load_agent_router_plugin_returns_none_when_disabled tests/unit/test_agent_router.py::test_load_agent_router_plugin_returns_router_when_enabled -v
```
Expected: `AssertionError` or `ImportError`

- [ ] **Step 3: Register in `registry.py`**

In `openagents/plugins/registry.py`:

Add import alongside the other builtin imports:
```python
from openagents.plugins.builtin.agent_router.default import DefaultAgentRouter
```

Add to `_BUILTIN_REGISTRY`:
```python
"agent_router": {
    "default": DefaultAgentRouter,
},
```

Add to `_DECORATOR_REGISTRY_MAP`:
```python
"agent_router": {},
```

- [ ] **Step 4: Add `load_agent_router_plugin` to `loader.py`**

In `openagents/plugins/loader.py`, add this function after `load_diagnostics_plugin`:

```python
def load_agent_router_plugin(config: Any) -> Any:
    """Create a DefaultAgentRouter from a MultiAgentConfig. Returns None when absent or disabled."""
    if config is None or not getattr(config, "enabled", False):
        return None
    from openagents.plugins.builtin.agent_router.default import DefaultAgentRouter
    return DefaultAgentRouter(config=config.model_dump())
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add openagents/plugins/registry.py openagents/plugins/loader.py tests/unit/test_agent_router.py
git commit -m "feat: register DefaultAgentRouter in plugin registry and add load_agent_router_plugin"
```

---

## Task 6: Wire into `DefaultRuntime`

**Files:**
- Modify: `openagents/plugins/builtin/runtime/default_runtime.py`
- Modify: `tests/unit/test_agent_router.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_agent_router.py`:

```python
def test_handoff_signal_caught_by_default_runtime():
    """DefaultRuntime.run() must catch HandoffSignal and return its result."""
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock
    from openagents.plugins.builtin.runtime.default_runtime import DefaultRuntime
    from openagents.interfaces.agent_router import HandoffSignal
    from openagents.interfaces.runtime import RunRequest, RunResult, StopReason

    child_result = RunResult(run_id="child", final_output="child output", stop_reason=StopReason.COMPLETED)

    mock_pattern = MagicMock()
    mock_pattern.execute = AsyncMock(side_effect=HandoffSignal(child_result))
    mock_pattern.setup = AsyncMock()
    mock_pattern.context = MagicMock()
    mock_pattern.context.scratch = {}

    mock_plugins = MagicMock()
    mock_plugins.pattern = mock_pattern
    mock_plugins.memory = MagicMock()
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

    result = asyncio.get_event_loop().run_until_complete(
        runtime.run(
            request=request,
            app_config=MagicMock(agents=[mock_agent]),
            agents_by_id={"test_agent": mock_agent},
            agent_plugins=mock_plugins,
        )
    )
    assert result.final_output == "child output"
    assert result.stop_reason == StopReason.COMPLETED.value
```

- [ ] **Step 2: Run test to see it fail**

```
uv run pytest tests/unit/test_agent_router.py::test_handoff_signal_caught_by_default_runtime -v
```
Expected: `FAILED` — `HandoffSignal` propagates as uncaught `BaseException`

- [ ] **Step 3: Add `_agent_router` field to `DefaultRuntime.__init__`**

In `openagents/plugins/builtin/runtime/default_runtime.py` inside `DefaultRuntime.__init__`, after `self._context_assembler: ContextAssemblerPlugin | None = None`:

```python
self._agent_router: Any | None = None
```

- [ ] **Step 4: Add `HandoffSignal` import**

At the top of `default_runtime.py`, add to the imports:

```python
from openagents.interfaces.agent_router import HandoffSignal
```

- [ ] **Step 5: Inject `agent_router` into context in `_setup_pattern`**

In `DefaultRuntime._setup_pattern()`, after the line `context.artifacts = artifacts`:

```python
context.agent_router = self._agent_router
```

- [ ] **Step 6: Catch `HandoffSignal` in `DefaultRuntime.run()`**

In `DefaultRuntime.run()`, add a new `except` clause BEFORE the existing `except Exception as exc:` (around line 1006). Find the line `except Exception as exc:` that is the outermost handler for the session block, and insert before it:

```python
        except HandoffSignal as sig:
            # transfer() was called: parent run ends with child's result as final output
            await self._event_bus.emit(
                RUN_COMPLETED,
                agent_id=request.agent_id,
                session_id=request.session_id,
                run_id=request.run_id,
                result=sig.result.final_output,
            )
            await self._event_bus.emit(
                "session.run.completed",
                agent_id=request.agent_id,
                session_id=request.session_id,
                run_id=request.run_id,
                stop_reason=RUN_STOP_COMPLETED,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            return RunResult(
                run_id=request.run_id,
                final_output=sig.result.final_output,
                stop_reason=RUN_STOP_COMPLETED,
                usage=usage,
                artifacts=list(artifacts),
                metadata={
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                    "handoff_from": sig.result.run_id,
                },
            )
```

Note: `HandoffSignal` is a `BaseException`, so it bypasses `except Exception`. It still triggers the `finally:` diagnostics cleanup block, which is correct.

- [ ] **Step 7: Run tests to verify they pass**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add openagents/plugins/builtin/runtime/default_runtime.py tests/unit/test_agent_router.py
git commit -m "feat: wire agent_router into DefaultRuntime context and catch HandoffSignal"
```

---

## Task 7: Wire in `Runtime` Facade

**Files:**
- Modify: `openagents/runtime/runtime.py`
- Modify: `tests/unit/test_agent_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_agent_router.py`:

```python
def test_runtime_injects_agent_router_when_enabled():
    from openagents.runtime.runtime import Runtime
    from openagents.plugins.builtin.agent_router.default import DefaultAgentRouter
    runtime = Runtime.from_dict({
        "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
        "multi_agent": {"enabled": True},
    })
    assert isinstance(runtime._runtime._agent_router, DefaultAgentRouter)
    assert runtime._runtime._agent_router._run_fn is not None


def test_runtime_no_agent_router_when_absent():
    from openagents.runtime.runtime import Runtime
    runtime = Runtime.from_dict({
        "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
    })
    assert runtime._runtime._agent_router is None


def test_runtime_no_agent_router_when_disabled():
    from openagents.runtime.runtime import Runtime
    runtime = Runtime.from_dict({
        "agents": [{"id": "a", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
        "multi_agent": {"enabled": False},
    })
    assert runtime._runtime._agent_router is None
```

- [ ] **Step 2: Run tests to see them fail**

```
uv run pytest tests/unit/test_agent_router.py::test_runtime_injects_agent_router_when_enabled tests/unit/test_agent_router.py::test_runtime_no_agent_router_when_absent tests/unit/test_agent_router.py::test_runtime_no_agent_router_when_disabled -v
```
Expected: `AssertionError: assert None is not None` (agent_router is None everywhere)

- [ ] **Step 3: Add wiring in `Runtime.__init__`**

In `openagents/runtime/runtime.py`, in `Runtime.__init__` after `self._diagnostics = components.diagnostics` (around line 60):

```python
        # Wire multi-agent router when configured and enabled
        from openagents.plugins.loader import load_agent_router_plugin
        _agent_router = load_agent_router_plugin(config.multi_agent)
        if _agent_router is not None:
            _agent_router._run_fn = self.run_detailed
            if hasattr(self._runtime, "_agent_router"):
                self._runtime._agent_router = _agent_router
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/test_agent_router.py -v
```
Expected: all pass

- [ ] **Step 5: Run full suite to check for regressions**

```
uv run pytest -q
```
Expected: all existing tests pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add openagents/runtime/runtime.py tests/unit/test_agent_router.py
git commit -m "feat: wire agent_router into Runtime facade post-construction"
```

---

## Task 8: Integration Tests

**Files:**
- Create: `tests/integration/test_multi_agent.py`

- [ ] **Step 1: Write integration tests**

```python
# tests/integration/test_multi_agent.py
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from openagents.runtime.runtime import Runtime
from openagents.interfaces.agent_router import DelegationDepthExceededError, HandoffSignal
from openagents.interfaces.runtime import RunResult, RunRequest, StopReason


_CONFIG = {
    "agents": [
        {"id": "orchestrator", "name": "Orchestrator",
         "memory": {"type": "buffer"}, "pattern": {"type": "react"}, "llm": {"provider": "mock"}},
        {"id": "specialist", "name": "Specialist",
         "memory": {"type": "buffer"}, "pattern": {"type": "react"}, "llm": {"provider": "mock"}},
    ],
    "multi_agent": {"enabled": True, "default_session_isolation": "isolated"},
}


def _make_child_result(output: str, run_id: str = "child-1") -> RunResult:
    return RunResult(run_id=run_id, final_output=output, stop_reason=StopReason.COMPLETED)


def _make_ctx(run_id="run-1", session_id="sess-1"):
    ctx = MagicMock()
    ctx.run_id = run_id
    ctx.session_id = session_id
    ctx.deps = None
    ctx.run_request = MagicMock(parent_run_id=None)
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
    ctx = _make_ctx(run_id="deep-run")
    router._run_depths["deep-run"] = 2  # simulate already-deep chain

    with pytest.raises(DelegationDepthExceededError) as exc_info:
        await router.delegate("specialist", "hi", ctx)
    assert exc_info.value.depth == 2
    assert exc_info.value.limit == 1


@pytest.mark.asyncio
async def test_child_depth_recorded_for_grandchild_checks():
    runtime = Runtime.from_dict(_CONFIG)
    router = runtime._runtime._agent_router
    child_result = _make_child_result("done", run_id="child-run-abc")
    router._run_fn = AsyncMock(return_value=child_result)
    ctx = _make_ctx(run_id="root-run")

    await router.delegate("specialist", "go", ctx)
    # child's depth should be recorded so its children can check
    assert router._run_depths.get("child-run-abc") == 1
```

- [ ] **Step 2: Run integration tests**

```
uv run pytest tests/integration/test_multi_agent.py -v
```
Expected: `6 passed`

- [ ] **Step 3: Run full suite + coverage**

```
uv run coverage run -m pytest && uv run coverage report
```
Expected: coverage >= 90%

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_multi_agent.py
git commit -m "test: add integration tests for multi-agent delegate, transfer, isolation modes, and depth limiting"
```
