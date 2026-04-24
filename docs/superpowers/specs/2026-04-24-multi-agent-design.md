# Multi-Agent Support Design — OpenAgents Python SDK

**Date:** 2026-04-24  
**Status:** Approved  
**Approach:** Option A — New `agent_router` top-level seam

---

## Background & Constraints

OpenAgents is a single-agent runtime kernel. This spec adds multi-agent capability as a first-class kernel seam without pushing product semantics into the kernel. Confirmed constraints:

- Both **Orchestrator** (delegate — subagent result returns to parent) and **Handoff** (transfer — control moves permanently) patterns
- Implemented as a **new kernel-level seam** (9th seam alongside memory, pattern, tool, etc.)
- **Session isolation** is configurable per-call: `"shared"`, `"isolated"`, or `"forked"`
- **Handoff mode** is configurable: `"transfer"` or `"delegate"`

---

## Architecture

### New Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `AgentRouterPlugin` protocol | `openagents/interfaces/agent_router.py` | Seam contract |
| `HandoffSignal` exception | `openagents/interfaces/agent_router.py` | Transfer-mode signal, caught by `DefaultRuntime.run()` |
| `DefaultAgentRouter` | `openagents/plugins/builtin/agent_router/default.py` | Builtin implementation |
| `MultiAgentConfig` | `openagents/config/schema.py` | JSON schema extension |

### Runtime Integration (minimal touch)

```
Runtime.run_detailed()
  └── DefaultRuntime.run()
        ├── load_plugins → inject ctx.agent_router          ← new: router injection
        ├── pattern.execute(ctx)
        │     └── ctx.agent_router.delegate(...)            ← pattern call site
        │         or ctx.agent_router.transfer(...)         ← raises HandoffSignal
        └── except HandoffSignal as sig:                    ← new: catch point
              return sig.result                             ← child run result becomes final output
```

### Files Changed

| File | Change Type |
|------|------------|
| `openagents/interfaces/agent_router.py` | New |
| `openagents/plugins/builtin/agent_router/default.py` | New |
| `openagents/interfaces/capabilities.py` | Small edit — register agent_router capability check |
| `openagents/interfaces/run_context.py` | Small edit — add `agent_router: AgentRouterPlugin \| None` field |
| `openagents/plugins/registry.py` | Small edit — register `"default_agent_router"` type name |
| `openagents/plugins/loader.py` | Small edit — load `multi_agent` config block, inject router into ctx |
| `openagents/config/schema.py` | Small edit — add `MultiAgentConfig` Pydantic model |
| `openagents/runtime/runtime.py` | Small edit — add `HandoffSignal` catch in `DefaultRuntime.run()` |

---

## Interface

```python
# openagents/interfaces/agent_router.py

class HandoffSignal(Exception):
    """Raised by transfer() to terminate the parent run with the child's result."""
    def __init__(self, result: RunResult) -> None:
        self.result = result

class AgentRouterPlugin(Protocol):
    async def delegate(
        self,
        agent_id: str,
        input_text: str,
        ctx: RunContext,
        *,
        session_isolation: Literal["shared", "isolated", "forked"] = "isolated",
        budget: RunBudget | None = None,
        deps: Any = None,
    ) -> RunResult:
        """Delegate to a sub-agent and await its result before continuing."""

    async def transfer(
        self,
        agent_id: str,
        input_text: str,
        ctx: RunContext,
        *,
        session_isolation: Literal["shared", "isolated", "forked"] = "isolated",
        budget: RunBudget | None = None,
        deps: Any = None,
    ) -> NoReturn:
        """Transfer control permanently to another agent. Raises HandoffSignal."""
```

---

## Session Isolation

| Mode | `session_id` behavior | Use case |
|------|-----------------------|---------|
| `"isolated"` | New independent `session_id` | Default; child has fully independent history |
| `"shared"` | Inherits parent `session_id` | Child can see parent's full conversation history |
| `"forked"` | Copies parent history snapshot to new session | Child branches from current state, writes don't affect parent |

---

## DefaultAgentRouter Implementation

```python
class DefaultAgentRouter:
    def __init__(self, runtime: Runtime, config: MultiAgentConfig) -> None:
        self._runtime = runtime
        self._config = config

    async def delegate(
        self, agent_id, input_text, ctx, *, session_isolation="isolated",
        budget=None, deps=None,
    ) -> RunResult:
        self._check_depth(ctx)
        child_request = RunRequest(
            agent_id=agent_id,
            session_id=self._resolve_session(ctx, session_isolation),
            input_text=input_text,
            parent_run_id=ctx.run_id,   # existing field
            budget=budget or self._config.default_child_budget,
            deps=deps if deps is not None else ctx.deps,
        )
        return await self._runtime.run_detailed(child_request)

    async def transfer(self, agent_id, input_text, ctx, **kwargs) -> NoReturn:
        result = await self.delegate(agent_id, input_text, ctx, **kwargs)
        raise HandoffSignal(result)

    def _resolve_session(self, ctx: RunContext, isolation: str) -> str:
        if isolation == "shared":
            return ctx.session_id
        if isolation == "forked":
            return f"{ctx.session_id}:fork:{ctx.run_id}"
        return f"child:{ctx.run_id}:{uuid4().hex[:8]}"

    def _check_depth(self, ctx: RunContext) -> None:
        depth = self._compute_depth(ctx.run_id)
        if depth >= self._config.max_delegation_depth:
            raise DelegationDepthExceededError(depth, self._config.max_delegation_depth)
```

---

## Configuration

```json
{
  "runtime": { "type": "default" },
  "multi_agent": {
    "enabled": true,
    "default_session_isolation": "isolated",
    "max_delegation_depth": 5
  },
  "agents": [
    { "id": "orchestrator", "pattern": { "type": "react" } },
    { "id": "billing_agent", "pattern": { "type": "react" } }
  ]
}
```

`MultiAgentConfig` Pydantic model:

```python
class MultiAgentConfig(BaseModel):
    enabled: bool = False
    default_session_isolation: Literal["shared", "isolated", "forked"] = "isolated"
    max_delegation_depth: int = 5
    default_child_budget: RunBudget | None = None
```

When `multi_agent` block is absent, `ctx.agent_router` is `None`. Calling it raises `ConfigurationError`, with no impact on existing single-agent applications.

---

## Error Handling

| Scenario | Behavior |
|----------|---------|
| Target `agent_id` not found | `AgentNotFoundError` raised; parent run ends with `FAILED` |
| `max_delegation_depth` exceeded | `DelegationDepthExceededError`; prevents infinite recursion |
| Child run fails (`FAILED` / `BUDGET_EXHAUSTED`) | `delegate()` returns the failed `RunResult`; pattern decides; `transfer()` wraps in `HandoffSignal` and propagates |
| `multi_agent` not configured, router called | `ConfigurationError("agent_router not configured")` |

Depth is tracked via `RunRequest.metadata["__openagents_delegation_depth__"]` (reserved key). Each call to `delegate()` sets the child's metadata to `parent_depth + 1`; `_check_depth` reads the current ctx's metadata. No `RunRequest` schema fields were added and the router holds no process-level depth state. See `openspec/changes/fix-multi-agent-p0-gaps/` for the implementation of this and the other P0 gap fixes landed 2026-04-24.

---

## Pattern Usage

```python
# Orchestrator pattern: delegate and use result
result = await ctx.agent_router.delegate(
    "billing_agent",
    "Process refund for order #1234",
    ctx,
    session_isolation="isolated",
)
if result.stop_reason == StopReason.COMPLETED:
    # continue with result.output_text
    ...

# Handoff pattern: transfer control permanently
await ctx.agent_router.transfer(
    "specialist_agent",
    "Escalate: requires specialist handling",
    ctx,
    session_isolation="forked",
)
# unreachable — parent run ends with specialist_agent's output
```

---

## Testing Strategy

- **Unit tests** (`tests/unit/test_agent_router.py`): `DefaultAgentRouter` with mock `Runtime`; verify session_id generation for all three isolation modes, depth check, `HandoffSignal` raising, child `RunRequest.parent_run_id` propagation.
- **Integration tests** (`tests/integration/test_multi_agent.py`): `MockPatternPlugin` + `MockLLMClient`; verify delegate result flows into parent Pattern; verify transfer causes parent run to end with child's output.
- **Edge cases**: depth overflow, child run failure propagation, absent `multi_agent` config degradation.
- Coverage target: `fail_under = 90` (consistent with project policy); `DefaultAgentRouter` counted separately.

---

## Deliberately Out of Scope

Per CLAUDE.md kernel boundary rules and the gap matrix conclusions:

- Multi-agent UI / approval UX — application layer
- Agent discovery / registry — application layer
- Broadcast / fan-out orchestration — application layer
- Persistent agent mailboxes — application layer
