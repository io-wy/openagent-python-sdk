## Context

`DefaultRuntime.run()` today executes `pattern.execute()` inside a single `async with session_manager.session(...)` block. If the pattern raises anything other than the narrow `ModelRetryError` validation-retry path, the whole run fails and all in-flight progress (transcript, tool results, partial usage) is lost. This is a problem for long-horizon runs (research agents, coding agents, PPT generators) where a single upstream 529/ReadTimeout wastes dozens of minutes of work. All four load-bearing pieces already exist:

- `SessionCheckpoint` dataclass (`interfaces/session.py:37`)
- `session_manager.create_checkpoint()` / `load_checkpoint()` implemented in every builtin backend (`in_memory`, `jsonl_file`, `sqlite`)
- Typed transient-vs-permanent error hierarchy (`LLMRateLimitError`, `LLMConnectionError`, `ToolRateLimitError`, `ToolUnavailableError` vs `PermanentToolError`, `ConfigError`, `BudgetExhausted`)
- `RunRequest.parent_run_id` field (currently unused — could eventually stitch resumed-from-external-checkpoint chains)

What's missing is **three pieces of glue**: (a) a way for the runtime to notice step boundaries, (b) a way to persist at each boundary, (c) a way to rehydrate on transient failure. This design wires those three pieces without introducing a new seam, a new builtin plugin, or any change to `PatternPlugin` / `ToolPlugin` / `ToolExecutorPlugin` / `MemoryPlugin` / `ContextAssemblerPlugin`.

## Goals / Non-Goals

**Goals:**
- Opt-in auto-checkpointing every pattern step when `durable=True`, backed by existing `session.create_checkpoint`.
- Automatic retry from the most recent checkpoint on the declared retryable error classes.
- Explicit resume entry point via `RunRequest.resume_from_checkpoint`, supporting "kill the process, restart tomorrow" workflows.
- Bounded retry via `RunBudget.max_resume_attempts`; no silent infinite loop on a broken provider.
- Zero behavior change when `durable=False`; zero change to existing seam interfaces.

**Non-Goals:**
- **Not** introducing a `durability` seam. This is a runtime-behavior decoration, not an extension point.
- **Not** making any `PatternPlugin` / `ToolPlugin` subclass aware of checkpoints. The boundary is purely observed by the runtime via existing `tool.succeeded` / `llm.succeeded` events.
- **Not** solving multi-agent / sub-agent resume. The `parent_run_id` field is out of scope (future, if ever).
- **Not** serializing non-state execution context (open MCP pool connections, open HTTP clients, in-flight async tasks). Only the declarative state that `SessionCheckpoint` already captures — transcript, artifacts, state dict, usage. Transient connections are re-established on resume.
- **Not** making memory writeback resumable mid-run. Writeback happens post-`execute()` and is not part of the checkpoint/resume loop.

## Decisions

### D1. Step boundary = `tool.succeeded` OR `llm.succeeded` event

**Decision**: The runtime subscribes an async handler to `tool.succeeded` and `llm.succeeded` events (filtered by `run_id == request.run_id`) when `request.durable` is True. Each matching event increments a per-run step counter and triggers `session.create_checkpoint(session_id, checkpoint_id=f"{run_id}:step:{n}")`.

**Alternatives considered**:
- *Wrap `PatternPlugin.call_llm` / `call_tool` with decorators at bind time.* Rejected: touches more surface; harder to enforce if a custom pattern overrides these methods.
- *Add a `step.completed` hook on `PatternPlugin`.* Rejected: that's a new API on an existing seam, defeats the "zero seam change" goal. The existing `tool.succeeded` / `llm.succeeded` events already fire at exactly the right moments, emitted from `PatternPlugin.call_tool` / `call_llm` on the happy path.
- *Checkpoint after every event.* Rejected: over-persistence. We want boundaries aligned to "state we'd want to rewind to."

**Rationale**: The happy-path emission in `PatternPlugin.call_tool` / `call_llm` is the natural semantic step boundary. The event bus is already on the critical path; all backends tolerate it.

### D2. Async event handler runs synchronously with emit

**Decision**: Require the default `AsyncEventBus` to invoke subscribed handlers in the emitting task's frame (i.e., `await self.emit(...)` completes only after all matching subscribers' `await handler(event)` calls return). This guarantees `create_checkpoint` executes while we still hold the session lock inside `async with session_manager.session(...)` — no stale-state lock race.

**Alternatives considered**:
- *Post-emit background handler* (queue then drain outside the session lock). Rejected: two session lock acquisitions per step is expensive; also widens the crash-between-tool-and-checkpoint window.
- *Dedicated non-bus callback registered on `DefaultRuntime`.* Rejected: duplicates the event-bus machinery.

**Verification**: Add a unit test that asserts `AsyncEventBus.emit()` awaits subscribers inline; freeze this in a spec requirement on `events` capability if not already there (check during task 2.x).

### D3. Retryable vs permanent classification

**Decision**: Define a runtime-level constant `RETRYABLE_RUN_ERRORS: tuple[type[OpenAgentsError], ...]` containing `LLMRateLimitError`, `LLMConnectionError`, `ToolRateLimitError`, `ToolUnavailableError`. Any other `OpenAgentsError` subclass — notably `PermanentToolError`, `ConfigError`, `BudgetExhausted`, `OutputValidationError`, `PatternError` — is permanent. Non-`OpenAgentsError` exceptions (bare `RuntimeError`, etc.) are wrapped in `PatternError` by existing `except Exception` handler and become permanent.

**Alternatives considered**:
- *A `.retryable` instance attribute on every `OpenAgentsError`.* Rejected: requires touching the entire error hierarchy; no subclass today has strong "I'm retryable" semantics except the four already-identified.
- *User-configurable retry_on list on `RunBudget`.* Deferred: start with the hard-coded tuple; add configurability in a follow-up if users ask. Keep the surface small.

**Rationale**: The four error types were all introduced in the 0.4.x tool-invocation-enhancement spec series with clear transient semantics. Adding configurability now is premature.

### D4. Checkpoint state shape

**Decision**: At each step boundary, the runtime calls:

```python
await session_manager.create_checkpoint(
    session_id=request.session_id,
    checkpoint_id=f"{request.run_id}:step:{step_counter}",
)
```

The session backend implementation already persists the full `session_state` dict (which includes transcript, artifacts, run metadata) plus `transcript_length` / `artifact_count` cursors. Before creating the checkpoint, the runtime MUST flush the pattern's `RunContext.state` back into `session_state` (most patterns hold a reference already; verify this in `setup()` path). The runtime also MUST merge a minimal "durable metadata" blob into `session_state`:

```python
session_state["__durable__"] = {
    "run_id": request.run_id,
    "step_counter": step_counter,
    "usage": usage.model_dump(),
    "artifacts": [a.model_dump() for a in artifacts],
}
```

This blob is the canonical source of truth for `usage` and `artifacts` on resume (since those live on the runtime's stack, not in session state otherwise).

**Alternatives considered**:
- *Extend `SessionCheckpoint` with explicit `usage` / `artifacts` fields.* Rejected: that's a kernel protocol change; the existing `state: dict` is a perfectly fine escape hatch.
- *Store a compressed delta per step rather than full state.* Rejected: premature optimization; existing backends already handle full-state writes.

### D5. Resume rehydration sequence

**Decision**: When resuming (either from mid-run transient failure OR from an explicit `resume_from_checkpoint`), the runtime:

1. Calls `session_manager.load_checkpoint(session_id, checkpoint_id)`.
2. Reads the `__durable__` blob from `checkpoint.state` to reconstruct `usage` and `artifacts`.
3. Seeds `pattern.context.state`, `pattern.context.transcript` (truncated to `checkpoint.transcript_length`), `pattern.context.artifacts`, `pattern.context.usage` from the checkpoint.
4. Emits `run.resume_attempted` then `run.resume_succeeded` (after successful re-entry).
5. Re-invokes `pattern.execute()` within the same `async with session_manager.session(...)` block.

**Memory injection on resume**: **Skipped**. The transcript loaded from the checkpoint already contains the injected memory from the original run. Re-injecting risks duplication if the memory plugin isn't idempotent.

**MCP pool on resume**: MCP sessions (from `_mcp_coordinator`) are kept across resume attempts within the same process — they're keyed by `session_id` and the connection is orthogonal to run state. If the process died and `resume_from_checkpoint` is used from a fresh process, the MCP pool is naturally re-created cold.

### D6. Checkpoint for transient failure vs explicit resume

**Decision**: Same rehydration path for both. The only difference is the entry point:

- **Transient-failure path**: caught inside `DefaultRuntime.run()` outer loop after `pattern.execute()` raises; most-recent `step_counter` is read from `session_state["__durable__"]`.
- **Explicit-resume path**: `request.resume_from_checkpoint` is consulted before `pattern.setup()`; the checkpoint is loaded BEFORE `setup()`, and `setup()` receives the already-rehydrated `transcript` / `artifacts` / `usage` / `state` from the loaded checkpoint instead of from `context_assembler.assemble()`.

**Alternative considered**: *Fork `Runtime.run_resume(request, checkpoint_id)` as a distinct entrypoint.* Rejected: doubles the API surface for near-identical behavior; the field on `RunRequest` is cleaner.

### D7. Event taxonomy additions

**Decision**: Add five event names, four carrying `run_id`:

| Event | Payload | When |
|---|---|---|
| `run.checkpoint_saved` | `run_id, checkpoint_id, step_index, transcript_length` | After each successful `create_checkpoint` |
| `run.checkpoint_failed` | `run_id, checkpoint_id, error, error_type` | When `create_checkpoint` raises (run continues) |
| `run.resume_attempted` | `run_id, checkpoint_id, attempt_index, error_type` | Before `load_checkpoint` on transient failure |
| `run.resume_succeeded` | `run_id, checkpoint_id, attempt_index` | After state rehydration completes |
| `run.resume_exhausted` | `run_id, attempt_index, error_type, limit` | When `max_resume_attempts` reached |

Three of these (`checkpoint_saved`, `resume_attempted`, `resume_succeeded`) also get `RunStreamChunkKind` variants so `run_stream()` consumers can observe them.

## Risks / Trade-offs

- **[Risk] AsyncEventBus subscriber ordering is not spec'd.** The design (D2) requires that subscribed handlers run inline in the emitter's task. If a custom event bus plugin violates this (e.g., queues events), checkpoints would fire after the session lock released and the session-state dict would be stale.
  → **Mitigation**: Add an assertion-style test in the durable-execution test file that pins the inline-dispatch contract; if a custom bus is configured, gracefully emit a `run.checkpoint_skipped_unsupported_bus` event once and disable checkpointing for that run.

- **[Risk] Idempotency of partially-completed tool calls.** If a tool's `invoke()` succeeds but the subsequent `tool.succeeded` emit throws, we'd never checkpoint the result — on resume we re-run the tool. For side-effectful tools (write_file, http_request, shell_exec) this is a double-execute.
  → **Mitigation**: Document in `docs/api-reference.md` that tools with durable-run-unsafe side effects should declare `ToolPlugin.durable_idempotent = False` (new class attribute, default `True`). When a non-idempotent tool runs inside a `durable=True` run, the runtime emits a one-shot `run.durable_idempotency_warning` event. Do not block execution — user's choice.

- **[Risk] Checkpoint storm on high-fanout runs.** A pattern that does 200 parallel tool calls via `call_tool_batch` would produce 200 checkpoint writes, flooding the session backend.
  → **Mitigation**: The `tool.batch.completed` event (existing) is treated as a single step; the individual `tool.succeeded` events fired inside a batch do NOT trigger checkpoints. Detected via a per-run `__in_batch__` scratch flag set by `PatternPlugin.call_tool_batch`.

- **[Trade-off] Usage / artifacts live on runtime stack, not in session_state.** We shoehorn them into `state["__durable__"]` instead. This slightly couples durable execution to a private state key. If this grows, consider adding explicit fields to `SessionCheckpoint` in a future spec.
  → **Acceptance**: Fine for now — a single well-known key is cheap and reversible.

- **[Trade-off] No cross-session resume.** A checkpoint is bound to its original `session_id`. If the app deletes the session, resume fails.
  → **Acceptance**: Matches existing session model; cross-session resume would need a separate design.

- **[Risk] Coverage floor (92%) includes the new paths.** The new outer retry loop + resume path need thorough tests; the `run.checkpoint_failed` branch is the trickiest to cover.
  → **Mitigation**: Test plan (in tasks.md) includes deterministic failure-injection via a custom `SessionManager` fixture and via the existing `MockLLMClient` failure-mode hooks.

## Migration Plan

- **Step 1**: Ship with `durable=False` as default — zero behavior change for existing callers.
- **Step 2**: Update `examples/production_coding_agent/` to pass `durable=True` as the canonical reference.
- **Step 3**: In a future minor, consider flipping the default in long-form agent templates (`coding-agent`, `pptx-wizard`) while keeping `quickstart` default off.
- **Rollback**: Trivially revert — the retry loop is behind the `if request.durable:` guard; removing the commit leaves zero residue.

## Open Questions

1. **Should `resume_from_checkpoint` require the same `agent_id` as the checkpoint's origin?** Likely yes (the tools / pattern shape may be different across agents). Decision: **require match, raise `ConfigError` otherwise**; record in spec.
2. **Should cost budget resets on resume?** No — cost is cumulative across resumes because the LLM still charged for the pre-failure calls. `usage.cost_usd` is rehydrated from the checkpoint.
3. **Should `pattern.setup()` be called on resume-from-checkpoint?** Yes, but with the checkpoint-seeded state. `setup()` is idempotent in every builtin pattern (react/plan_execute/reflexion); verify during task 5.x.
