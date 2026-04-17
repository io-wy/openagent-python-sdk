# Plugin Interface Seam Consolidation

**Date:** 2026-04-18  
**Status:** Draft

## Problem

The SDK currently has 11 top-level seams wired by the plugin loader. Three of them —
`execution_policy`, `followup_resolver`, `response_repair_policy` — are conceptually
subordinate to existing plugins rather than truly independent extension points. This
inflates the seam count, adds three config keys, complicates `pattern.setup()`, and
signals false equivalence between them and genuinely independent seams like `memory`
and `context_assembler`.

Note: `followup_resolver` and `response_repair_policy` are currently wired into
`RunContext` but are never dispatched by any builtin pattern. This spec consolidates
them and activates them in the process.

## Decision

Absorb the three subordinate seams into their natural owner plugins as ordinary
override methods with sensible defaults. Remove them as top-level loader slots and
config keys. Use the existing Python method-override idiom — no new hook registry,
no callback chain, no new terminology.

## Interface Changes

### `ToolExecutorPlugin` — add `evaluate_policy`

```python
class ToolExecutorPlugin(BasePlugin):
    async def evaluate_policy(
        self, request: ToolExecutionRequest
    ) -> PolicyDecision:
        """Override to restrict tool execution. Default: allow all."""
        return PolicyDecision(allowed=True)

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=f"policy denied: {decision.reason}",
            )
        # ... existing execution logic
```

Policy is always global (applies to all tools going through the executor). Per-tool
metadata that informs policy decisions continues to live in `ToolPlugin.execution_spec()`.

### `PatternPlugin` — add `resolve_followup` and `repair_empty_response`

```python
class PatternPlugin(BasePlugin):
    async def resolve_followup(
        self, *, context: RunContext[Any]
    ) -> FollowupResolution | None:
        """Override to answer follow-ups locally. Return None to abstain (call LLM)."""
        return None

    async def repair_empty_response(
        self,
        *,
        context: RunContext[Any],
        messages: list[dict[str, Any]],
        assistant_content: list[dict[str, Any]],
        stop_reason: str | None,
        retries: int,
    ) -> ResponseRepairDecision | None:
        """Override to handle bad LLM responses. Return None to abstain (propagate)."""
        return None
```

Method names match the existing conventions exactly (`repair_empty_response` not
`repair_response`). Return `None` to abstain, return a typed decision object to act.

Pattern is the natural owner because it has full visibility into what happened during
the run (transcript, tool results, usage). These methods will be called by all three
builtin pattern classes (`ReActPattern`, `ReflexionPattern`, `PlanExecutePattern`) at
the same points where the old separate plugins were intended to be called (previously
wired but never dispatched).

### `PatternPlugin.setup()` — remove three parameters

Remove `followup_resolver`, `response_repair_policy`, and `execution_policy` from the
`setup()` signature. `tool_executor` stays because the executor is still independently
substitutable.

## Dispatch Path Changes

### `_BoundTool` / `_bind_tools` in `DefaultRuntime`

Currently `DefaultRuntime._bind_tools()` creates `_BoundTool` wrappers that hold
references to both `executor` and `execution_policy` as separate objects, calling
`policy.evaluate()` before `executor.execute()`. After this change:

- `_BoundTool` no longer holds a `policy` reference
- `_BoundTool.invoke()` calls `executor.execute(request)` directly
- The policy check moves inside `executor.execute()` via `self.evaluate_policy()`
- `_bind_tools()` no longer receives or wires an `execution_policy` argument

### Builtin pattern activation of followup and repair

All three builtin patterns (`ReActPattern`, `ReflexionPattern`, `PlanExecutePattern`)
must call `self.resolve_followup(context=ctx)` and `self.repair_empty_response(...)`
at the appropriate points in their loops. These were previously wired as separate
plugin references that were never invoked; they now become direct method calls on
`self`.

## `RunContext` Cleanup

`RunContext` currently declares `execution_policy`, `followup_resolver`, and
`response_repair_policy` as fields. After this change these fields are removed.
This is a kernel protocol change; it is acceptable here because these three fields
were effectively dead (wired but not dispatched). Removing them is a net simplification
of the protocol surface.

## Public API Changes

The following symbols in `openagents/__init__.py` and `__all__` will be removed:
- Decorators: `@execution_policy`, `@followup_resolver`, `@response_repair_policy`
- Registry accessors: `get_execution_policy`, `get_followup_resolver`, `get_response_repair_policy`
- Registry listers: `list_followup_resolvers`

CLI commands in `cli/list_plugins_cmd.py` and `cli/validate_cmd.py` that reference
these seam names must be updated.

## Builtin Plugin Migration

The three concrete `ExecutionPolicyPlugin` subclasses in `openagents/plugins/builtin/execution_policy/`
(`FilesystemExecutionPolicy`, `NetworkAllowlistExecutionPolicy`, `CompositeExecutionPolicy`)
are migrated as follows:

- `FilesystemExecutionPolicy` and `NetworkAllowlistExecutionPolicy` become standalone
  helper classes (not `BasePlugin` subclasses). They expose an `evaluate(request) -> PolicyDecision`
  method and are intended to be called from within a custom `ToolExecutorPlugin.evaluate_policy()`
  override. They are no longer registered in the plugin registry.
- `CompositeExecutionPolicy` (`composite.py`) currently uses `load_plugin("execution_policy", ...)`
  internally to load child policies. After removal of the seam, this becomes a utility
  class that composes the same helper classes directly. The `load_plugin` call is removed.
- The `agent-builder` skill (`skills/openagent-agent-builder/src/.../render.py`) currently
  emits `execution_policy:` config keys. It must be updated to emit an `executor:` config
  with the policy embedded, or omit the policy key entirely if the default allow-all is sufficient.

User-defined standalone `execution_policy` / `followup_resolver` / `response_repair_policy`
plugins can be adapted by subclassing the relevant plugin and delegating to the old
plugin object. Each adapter is under 10 lines.

## Seam Inventory After Change

| Seam | Status | Reason |
|---|---|---|
| `pattern` | keep | core loop |
| `memory` | keep | independent lifecycle (inject / writeback) |
| `context_assembler` | keep | runs before pattern, independent lifecycle |
| `tool_executor` | keep | independently substitutable engine |
| `events` | keep | cross-cutting infrastructure |
| `runtime` / `session` / `skills` | keep | app infrastructure |
| `execution_policy` | **removed** | absorbed into `ToolExecutorPlugin.evaluate_policy()` |
| `followup_resolver` | **removed** | absorbed into `PatternPlugin.resolve_followup()` |
| `response_repair_policy` | **removed** | absorbed into `PatternPlugin.repair_response()` |

Seam count: 11 → 8.

## Config Impact

Remove three keys from agent config schema:

```diff
- execution_policy: ...
- followup_resolver: ...
- response_repair_policy: ...
```

## Documentation Update

`docs/seams-and-extension-points.md` section 9 (anti-patterns) lists
`follow-up fallback → followup_resolver` as the *correct* refactor away from
`Pattern.execute()`. That guidance is superseded by this spec. The doc will be
updated to reflect that `resolve_followup` and `repair_response` are now override
methods on `PatternPlugin`, not separate seams.

## What This Is Not

- Not a hook system. No registration, no pre/post chain, no new terminology.
- Not removing any capability. Every behavior expressible before is still expressible.
- Not touching `memory`, `context_assembler`, or any app infrastructure seam.
