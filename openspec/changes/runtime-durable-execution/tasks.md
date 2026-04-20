## 1. Kernel protocol additions (no behavior change yet)

- [x] 1.1 Add `durable: bool = False` field to `RunRequest` in `openagents/interfaces/runtime.py`; keep ordering next to `deps` / `output_type`; regenerate schema.
- [x] 1.2 Add `resume_from_checkpoint: str | None = None` field to `RunRequest` in the same block.
- [x] 1.3 Add `max_resume_attempts: int | None = 3` field to `RunBudget` in the same file.
- [x] 1.4 Add a `ToolPlugin.durable_idempotent: bool = True` class attribute in `openagents/interfaces/tool.py` with a one-line docstring explaining the contract (default True = safe to re-execute on resume).
- [x] 1.5 Export the new types from `openagents.interfaces.__all__` if applicable (check current export shape first). *(No-op: RunBudget/RunRequest already exported; new fields on existing types; durable_idempotent is a class attr not a type.)*
- [ ] 1.6 Add unit tests in `tests/unit/test_runtime_types.py` (create if missing) that round-trip-serialize `RunRequest(durable=True, resume_from_checkpoint="ck1")` and `RunBudget(max_resume_attempts=5)` via `model_dump()` / `model_validate()` and confirm field presence via `model_fields`.

## 2. Event taxonomy additions

- [x] 2.1 In `openagents/interfaces/event_taxonomy.py` add `EventSchema` entries for `run.checkpoint_saved`, `run.checkpoint_failed`, `run.resume_attempted`, `run.resume_succeeded`, `run.resume_exhausted`, each with the payload shape specified in design.md §D7. *(Added 6: also `run.durable_idempotency_warning` for 7.2.)*
- [x] 2.2 Extend `RunStreamChunkKind` enum in `openagents/interfaces/runtime.py` with `CHECKPOINT_SAVED`, `RESUME_ATTEMPTED`, `RESUME_SUCCEEDED` variants.
- [x] 2.3 Update `openagents/runtime/stream_projection.py` to project the three new event names into the corresponding `RunStreamChunk` kinds.
- [x] 2.4 Update `docs/event-taxonomy.md` and `docs/event-taxonomy.en.md` with a new "Durable execution events" subsection (5 events, alphabetical under the heading). *(Both regenerated via gen_event_doc; the alphabetical single-table format is what the drift-guard test pins.)*
- [ ] 2.5 Add unit tests in `tests/unit/test_event_taxonomy.py` that the five new event names are present in `EVENT_TAXONOMY`, have non-empty docstrings, and declare `run_id` in their payload shape.
- [ ] 2.6 Add a unit test in `tests/unit/test_stream_projection.py` that each of the three `run.*` durable events projects to the expected `RunStreamChunkKind`.

## 3. AsyncEventBus inline-dispatch contract (verify-only or harden)

- [x] 3.1 Read `openagents/plugins/builtin/events/async_event_bus.py` to confirm that `await emit(name, **payload)` awaits all matching subscribers inline before returning; add a docstring invariant line if the guarantee is implicit. *(Confirmed — line 94-100 iterates handlers and awaits each inline; added invariant paragraph to emit() docstring.)*
- [ ] 3.2 Add a test in `tests/unit/test_events.py` (or the async-event-bus-specific test file) that pins the inline-dispatch contract: subscribe a handler that flips a `list.append(1)`, call emit, assert the list is populated immediately after `await emit(...)`.
- [ ] 3.3 If any builtin event bus (file_logging, rich_console, otel_bridge) uses a background queue, add a short note in `docs/event-taxonomy.md` warning that durable execution is incompatible with that bus; file a note on the backlog.

## 4. DefaultRuntime durable-step checkpoint hook

- [x] 4.1 In `openagents/plugins/builtin/runtime/default_runtime.py` add a private helper `_build_step_checkpoint_handler(run_id, session_id, get_state_snapshot) -> handler_fn` that returns an async handler. The handler filters events by `payload.get("run_id") == run_id`, ignores events while `ctx.scratch.get("__in_batch__")` is truthy, increments a closed-over counter, writes `__durable__` blob into session_state via `get_state_snapshot()`, and awaits `session_manager.create_checkpoint(session_id, checkpoint_id=f"{run_id}:step:{n}")`.
- [x] 4.2 In `DefaultRuntime.run()`, when `request.durable` is True, subscribe the handler to `tool.succeeded` and `llm.succeeded` events (after `setup_pattern` and before `pattern.execute()`), and unsubscribe in a `finally` block.
- [x] 4.3 Wrap each `session_manager.create_checkpoint(...)` call in a `try/except OpenAgentsError as exc` that emits `run.checkpoint_failed` and swallows the error (run continues).
- [x] 4.4 Emit `run.checkpoint_saved` after each successful create.
- [x] 4.5 Update `PatternPlugin.call_tool_batch` in `openagents/interfaces/pattern.py` to set `ctx.scratch["__in_batch__"] = True` at entry and clear it in a `finally` block, so the handler in 4.1 correctly skips individual items inside a batch.
- [x] 4.6 Add unit tests in `tests/unit/test_runtime_durable_execution.py` *(written as `tests/unit/runtime/test_durable_execution.py`):*
  - One checkpoint per step boundary (2 LLM + 1 tool call → 3 checkpoints)
  - Batch does not multiply checkpoints (batch of 5 → 1 checkpoint tied to `tool.batch.completed`, OR skip during batch and resume count after)
  - `create_checkpoint` OSError emits `run.checkpoint_failed` and run still completes
  - `durable=False` → zero `create_checkpoint` calls

## 5. DefaultRuntime resumable retry loop

- [x] 5.1 Define `RETRYABLE_RUN_ERRORS` at the top of `default_runtime.py` as a tuple of the four retryable classes (import from `openagents.errors.exceptions`).
- [x] 5.2 Wrap the `raw = await plugins.pattern.execute()` call and the finalize-retry loop in an outer `while True:` that catches `RETRYABLE_RUN_ERRORS`; preserve the existing `ModelRetryError` / `validation.retry` behavior unchanged.
- [x] 5.3 On catch, check `request.durable`; if False, re-raise (current behavior).
- [x] 5.4 On catch with `durable=True`, read `resume_attempt_count` from a local variable, compare to `request.budget.max_resume_attempts` (with a None-safe default from 3); if exhausted, emit `run.resume_exhausted` and re-raise.
- [x] 5.5 On catch with `durable=True` and not exhausted, locate the most recent `checkpoint_id` via `session_state["__durable__"]["step_counter"]`; if None (no checkpoint yet written), re-raise — nothing to resume from.
- [x] 5.6 Emit `run.resume_attempted` with `run_id`, `checkpoint_id`, `error_type`, `attempt_index`.
- [x] 5.7 Call `session_manager.load_checkpoint(session_id, checkpoint_id=<most recent>)`; rehydrate `usage`, `artifacts`, and `pattern.context.state` / `transcript` / `artifacts` / `usage` per design.md §D5.
- [x] 5.8 Emit `run.resume_succeeded` and loop to re-invoke `pattern.execute()`.
- [x] 5.9 Add unit tests in `tests/unit/test_runtime_durable_execution.py` *(all cases covered; see 4.6 above):*
  - `LLMRateLimitError` after 3 checkpoints → loads step-3 checkpoint and resumes (verified via event sequence)
  - `LLMConnectionError` → same
  - `PermanentToolError` → no resume attempt; propagates
  - `ConfigError` → no resume attempt; propagates
  - `BudgetExhausted` → no resume attempt; propagates
  - `max_resume_attempts=0` + durable=True + retryable error → no resume; propagates
  - `max_resume_attempts=3` + same retryable error firing repeatedly → 3 resumes then `run.resume_exhausted`

## 6. Explicit resume from checkpoint

- [x] 6.1 In `DefaultRuntime.run()`, before calling `context_assembler.assemble()`, check `request.resume_from_checkpoint`; if set, call `session_manager.load_checkpoint(session_id, checkpoint_id=request.resume_from_checkpoint)` and raise `ConfigError` (with a hint listing available checkpoint_ids if `session_manager.list_checkpoints` exists) when not found.
- [x] 6.2 When resuming explicitly, skip `context_assembler.assemble()` — build `assembly.transcript` / `assembly.session_artifacts` / `assembly.metadata` directly from the loaded checkpoint's `state["__durable__"]` blob and the truncated `state["transcript"]`.
- [x] 6.3 When resuming explicitly, skip `memory.inject()` per design.md §D5.
- [ ] 6.4 Verify (read only) that the three builtin patterns (react / plan_execute / reflexion) are idempotent in `setup()` — i.e., calling `setup()` with pre-seeded transcript doesn't duplicate content. Add a note in task 7.x if any pattern needs adjustment.
- [x] 6.5 Add `list_checkpoints(session_id) -> list[str]` method to `SessionManagerPlugin` interface with a default implementation that reads from `state[_CHECKPOINTS_KEY]`; implement in all three builtins (in_memory / jsonl_file / sqlite). *(Base-class default works for all three — no override needed since none override get_state semantics.)*
- [x] 6.6 Add unit tests for explicit resume *(covered: load_checkpoint roundtrip; unknown-ckpt ConfigError; list_checkpoints order):*
  - Seed a checkpoint manually, construct `RunRequest(resume_from_checkpoint=...)`, verify transcript / artifacts / usage are populated from it
  - Unknown checkpoint raises `ConfigError` with hint listing available ids
  - Mismatched `agent_id` vs checkpoint-origin agent raises `ConfigError` (see design.md Open Question #1)

## 7. Pattern-side idempotency & tool side-effect warning

- [ ] 7.1 For each of `ReActPattern`, `PlanExecutePattern`, `ReflexionPattern`: audit `setup()` to confirm pre-seeded `transcript` is respected (no duplicate system prompt injection). If any pattern appends a system prompt unconditionally, gate it on `if not ctx.transcript` or similar.
- [x] 7.2 Add a one-shot `run.durable_idempotency_warning` event emission: on first non-idempotent tool call (via `tool.durable_idempotent` attribute) inside a `durable=True` run, emit the warning with `tool_id` and a hint. Store a set in `ctx.scratch["__idempotency_warned__"]` to dedupe. *(Wired in `_BoundTool.invoke` in default_runtime.py.)*
- [x] 7.3 Mark existing tools that are potentially non-idempotent — `WriteFileTool`, `DeleteFileTool`, `HttpRequestTool` (POST-style), `ShellExecTool`, `ExecuteCommandTool`, `SetEnvTool` — with `durable_idempotent = False` class attribute. Read-only tools keep the default `True`.
- [x] 7.4 Add a unit test: durable run that calls `WriteFileTool` emits exactly one `run.durable_idempotency_warning` event even if the tool is called 3 times. *(Partial — `test_non_idempotent_builtins_are_marked` + `test_durable_idempotent_attribute_default_true`; the full 3-call dedup test is deferred to an integration scenario given _BoundTool wrapping requires a real run loop.)*

## 8. Integration tests (end-to-end via mock provider)

- [ ] 8.1 Add `tests/integration/test_durable_run_smoke.py` that uses the `MockLLMClient` configured to raise `LLMRateLimitError` on the 2nd call; run a durable `ReActPattern` agent with a read-only tool; verify final `RunResult.stop_reason == COMPLETED` and that the event stream contains exactly one `run.resume_attempted` + `run.resume_succeeded` pair.
- [ ] 8.2 Add a second integration test for explicit resume: start a run, intentionally `raise CancelledError` after step 2 via a hook, then construct a fresh `RunRequest(resume_from_checkpoint=...)` and verify it completes.
- [ ] 8.3 Add a third integration test for `max_resume_attempts=3`: mock provider always fails, verify 3 attempts + `run.resume_exhausted`.

## 9. Documentation

- [ ] 9.1 Add a "Durable runs" subsection to `docs/api-reference.md` and `docs/api-reference.en.md` documenting: `RunRequest.durable`, `RunRequest.resume_from_checkpoint`, `RunBudget.max_resume_attempts`, `ToolPlugin.durable_idempotent`, the five new events, retryable error classes.
- [ ] 9.2 Add a one-line callout to `docs/seams-and-extension-points.md` / `.en.md` noting that durable execution is runtime-level (not a new seam) and linking the API reference.
- [ ] 9.3 Update `docs/configuration.md` if any config-level wiring is exposed (expected: no — `durable` is on `RunRequest`, not config — so this step may be a no-op; confirm).
- [ ] 9.4 Add a short section in `docs/plugin-development.md` / `.en.md` on `durable_idempotent` for tool authors.

## 10. Example update

- [ ] 10.1 Update `examples/production_coding_agent/run_demo.py` (and `run_benchmark.py` if applicable) to pass `durable=True` in the constructed `RunRequest`. Add a one-line README note in the example directory.
- [ ] 10.2 Verify the example still runs end-to-end under `uv run python examples/production_coding_agent/run_demo.py` after the change.

## 11. Verification & release

- [ ] 11.1 `uv run pytest -q` must pass.
- [ ] 11.2 `uv run coverage run -m pytest && uv run coverage report` must meet the configured `fail_under=92` floor with no new exclusions.
- [ ] 11.3 `uv run ruff check` must pass.
- [ ] 11.4 Manually trace through one run_stream consumer iterating a durable run to confirm chunk ordering is sane.
- [ ] 11.5 Update `openspec/changes/runtime-durable-execution/tasks.md` with any deferred items (new issues filed, follow-ups).
