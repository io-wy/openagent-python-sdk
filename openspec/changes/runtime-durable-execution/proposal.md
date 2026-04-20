## Why

The SDK already ships every load-bearing primitive for durable execution — `SessionCheckpoint` dataclass, `SessionManager.create_checkpoint()` / `load_checkpoint()` implemented by all three session backends (`in_memory`, `jsonl_file`, `sqlite`), typed `LLMRateLimitError` / `LLMConnectionError` / retryable `ToolError` hierarchies, `RetryToolExecutor`, and a `parent_run_id` field on `RunRequest` — but `DefaultRuntime` never calls any of them. In practice this means a multi-step run that hits a transient 529/ReadTimeout after 30 LLM calls loses all progress and must restart from step 0. Industry peers (Claude Agent SDK's "2-hour task survives network blip", Pydantic AI's Durable Execution) treat this as table-stakes; our half-built path is a latent capability we should finish rather than rediscover.

## What Changes

- Add two fields to `RunRequest`: `durable: bool = False` (opt-in) and `resume_from_checkpoint: str | None = None` (explicit resume token).
- Extend `DefaultRuntime.run()` to **auto-checkpoint** after each pattern step (LLM + tool completion boundary) when `request.durable` is true, using the already-available `session.create_checkpoint()`.
- Extend `DefaultRuntime.run()` to wrap the pattern execution loop with a **resumable retry loop**: on retryable errors (`LLMRateLimitError`, `LLMConnectionError`, `ToolRateLimitError`, `ToolUnavailableError`), load the most recent checkpoint, re-seed `RunContext` from it, and continue. Permanent errors (`ConfigError`, `PermanentToolError`, `BudgetExhausted`, `ModelRetryError` after budget) propagate unchanged.
- When `request.resume_from_checkpoint` is set, skip pattern `setup()` initial state build and seed `RunContext.state` / `transcript` / `artifacts` / `usage` from the checkpoint before calling `pattern.execute()`.
- Declare three new events in the taxonomy: `run.checkpoint_saved`, `run.resume_attempted`, `run.resume_succeeded`.
- Add `RunBudget.max_resume_attempts: int | None = 3` — bounded retry so a persistently broken provider doesn't burn the whole process.
- No new seam. No new builtin plugin. No change to `PatternPlugin`, `ToolPlugin`, `ToolExecutorPlugin`, `ContextAssemblerPlugin`, or any `MemoryPlugin`.

No **BREAKING** changes: `durable` defaults to `False`, so current behavior (no checkpointing, failure = whole run fails) is preserved bit-for-bit.

## Capabilities

### New Capabilities
- `runtime-durable-execution`: Opt-in auto-checkpoint + resume-on-transient-failure for agent runs. Covers when `DefaultRuntime` persists a checkpoint, which errors are classified as retryable vs permanent, how `RunContext` is rehydrated from a checkpoint, how the run's event stream reflects checkpoint/resume boundaries, and how `max_resume_attempts` caps retry.

### Modified Capabilities
<!-- None — this change is additive. Existing session checkpoint behavior (created by explicit `session.create_checkpoint()` calls from app code) stays fully backward-compatible. -->

## Impact

- **Code**: `openagents/interfaces/runtime.py` gains two fields on `RunRequest` and one field on `RunBudget`. `openagents/plugins/builtin/runtime/default_runtime.py` gains `_maybe_checkpoint_after_step()` helper + a resumable outer loop around `pattern.execute()`. `openagents/interfaces/event_taxonomy.py` gains three event schemas.
- **Dependencies**: None. All required infrastructure (`SessionCheckpoint`, `create_checkpoint`, `load_checkpoint`, typed errors, `RetryToolExecutor`) already exists.
- **APIs**: `RunRequest` / `RunBudget` get new optional fields (additive, default-false/None → zero behavior change). No seam interface changes.
- **Docs**: `docs/api-reference.md` / `.en.md` get a "Durable runs" subsection. `docs/seams-and-extension-points.md` gets a one-line callout that durable runs are runtime-level (not a new seam). `docs/developer-guide.md` stays unchanged (sub-agent/multi-agent boundary is untouched — this is single-agent fault recovery).
- **Tests**: `tests/unit/test_runtime_durable_execution.py` covering checkpoint-cadence, resume-from-checkpoint rehydration, retryable-vs-permanent classification, `max_resume_attempts` budget, and event emission. One integration test using the mock provider configured to fail deterministically on the 2nd LLM call, verifying end-to-end resume. Coverage floor (92%) maintained.
- **Examples**: `examples/production_coding_agent/` gets a one-line `durable=True` addition to its `RunRequest` construction as the reference pattern; no other example needs touching.
- **Packaging**: No `pyproject.toml` changes.
