## ADDED Requirements

### Requirement: RunRequest exposes durable and resume_from_checkpoint fields

`openagents.interfaces.runtime.RunRequest` SHALL declare two additional optional fields that control durable execution behavior: `durable: bool` with default `False`, and `resume_from_checkpoint: str | None` with default `None`. Both fields MUST be pydantic-model fields (not context_hints entries) so they participate in schema validation, OpenAPI/JSON schema export, and IDE type checking. `RunBudget` SHALL declare `max_resume_attempts: int | None` with default `3`. When `durable=False` (the default), `DefaultRuntime` MUST NOT create any checkpoints automatically and MUST NOT catch transient errors — behavior MUST be bit-for-bit identical to the pre-change path so existing callers see zero regression.

#### Scenario: Default RunRequest does not opt into durable execution
- **WHEN** a caller constructs `RunRequest(agent_id="a", session_id="s", input_text="hi")`
- **THEN** `request.durable == False` and `request.resume_from_checkpoint is None` and `request.budget.max_resume_attempts == 3` when budget is defaulted

#### Scenario: Non-durable run matches pre-change behavior
- **WHEN** `DefaultRuntime.run()` executes a `RunRequest` with `durable=False` against a pattern that completes without error
- **THEN** zero calls to `session_manager.create_checkpoint(...)` are made for this run, and the emitted event sequence contains no `run.checkpoint_saved` / `run.resume_attempted` / `run.resume_succeeded` events

### Requirement: DefaultRuntime auto-checkpoints after each pattern step when durable

When `request.durable` is `True`, `DefaultRuntime` SHALL invoke `session_manager.create_checkpoint(session_id=request.session_id, checkpoint_id=...)` after each pattern step boundary. A "step boundary" is defined as either (a) successful completion of a `pattern.call_llm(...)` round, or (b) successful completion of a `pattern.call_tool(...)` invocation. The checkpoint_id MUST be deterministic and monotonically ordered within a single `run_id` (e.g., `f"{run_id}:step:{n}"` where `n` increments per step). Each `create_checkpoint` call MUST be followed by a `run.checkpoint_saved` event whose payload includes `run_id`, `checkpoint_id`, `step_index`, and `transcript_length`. If `create_checkpoint` raises, the runtime MUST emit `run.checkpoint_failed` and continue the run (checkpoint failure MUST NOT fail the run itself).

#### Scenario: Durable run emits one checkpoint per step
- **WHEN** a durable run executes a pattern that performs 1 LLM call then 1 tool call then 1 final LLM call
- **THEN** exactly 3 checkpoints are created with ordered `checkpoint_id`s and 3 `run.checkpoint_saved` events are emitted in order

#### Scenario: Checkpoint persistence failure does not fail the run
- **WHEN** a durable run is in progress and the session manager's `create_checkpoint` raises `OSError` on one step
- **THEN** the runtime emits `run.checkpoint_failed` with the error message and continues the pattern execution; the final `RunResult.stop_reason` is `COMPLETED` if the pattern itself completes

### Requirement: DefaultRuntime resumes from the most recent checkpoint on retryable errors

When `request.durable` is `True` and `pattern.execute()` raises one of the declared retryable error types — `LLMRateLimitError`, `LLMConnectionError`, `ToolRateLimitError`, `ToolUnavailableError`, or any exception type explicitly listed in the runtime-level retryable-classifier — `DefaultRuntime` SHALL attempt to resume by: (1) emitting `run.resume_attempted` with `run_id`, `checkpoint_id`, `error_type`, `attempt_index`; (2) calling `session_manager.load_checkpoint(session_id, checkpoint_id=<most recent>)`; (3) re-seeding the pattern's `RunContext.state`, `transcript`, `artifacts`, `usage` from the loaded checkpoint; (4) re-invoking `pattern.execute()`. Non-retryable errors — `ConfigError`, `PermanentToolError`, `BudgetExhausted`, `ModelRetryError` (after `max_validation_retries` exhausted), plus any `OpenAgentsError` subclass whose `.retryable` attribute is `False` — MUST propagate unchanged and terminate the run with `stop_reason=FAILED`. On successful resume, the runtime MUST emit `run.resume_succeeded` with `run_id`, `checkpoint_id`, `attempt_index`.

#### Scenario: LLMRateLimitError triggers resume from last checkpoint
- **WHEN** a durable run has saved 3 checkpoints and the 4th LLM call raises `LLMRateLimitError` after transport-layer retries are exhausted
- **THEN** the runtime emits `run.resume_attempted` with `checkpoint_id` matching the 3rd checkpoint, reloads state from that checkpoint, and re-invokes `pattern.execute()`; if the retry succeeds, `run.resume_succeeded` is emitted and the final `RunResult.stop_reason` is `COMPLETED`

#### Scenario: Non-retryable error propagates without resume
- **WHEN** a durable run raises `PermanentToolError` mid-execution
- **THEN** no `run.resume_attempted` event is emitted, the pattern exception propagates to `RunResult.exception`, and `stop_reason == FAILED`

#### Scenario: ModelRetryError after budget becomes permanent
- **WHEN** a durable run exhausts `max_validation_retries` and `PatternPlugin` raises `PermanentToolError` chained from `ModelRetryError`
- **THEN** the runtime MUST NOT treat this as retryable; it propagates as `stop_reason=FAILED`

### Requirement: max_resume_attempts caps durable retry

`DefaultRuntime` SHALL count resume attempts per-run and refuse a further resume once the count reaches `request.budget.max_resume_attempts` (default 3). When the cap is reached, the most recent retryable exception MUST be re-raised and surfaced as `RunResult.exception` with `stop_reason=FAILED`. Each cap-exceeded failure MUST emit a single `run.resume_exhausted` event with `run_id`, `attempt_index`, `error_type`, `limit`.

#### Scenario: Third consecutive rate-limit exhausts the budget
- **WHEN** `max_resume_attempts=3` and the same `LLMRateLimitError` re-fires on each resume attempt after the initial failure
- **THEN** the runtime resumes 3 times (attempts 1, 2, 3), then on the 4th failure emits `run.resume_exhausted` and returns `RunResult(stop_reason=FAILED, exception=<LLMRateLimitError>)`

#### Scenario: max_resume_attempts=0 disables resume even when durable=True
- **WHEN** `durable=True` and `max_resume_attempts=0` and the first LLM call raises `LLMConnectionError`
- **THEN** no `run.resume_attempted` event is emitted and the error propagates to `RunResult.stop_reason=FAILED`; checkpoints MAY still be written for observability

### Requirement: resume_from_checkpoint rehydrates a fresh RunRequest

When `request.resume_from_checkpoint` is a non-None string, `DefaultRuntime` SHALL load that checkpoint from the session manager before calling `pattern.setup()`, and SHALL seed the pattern's initial `RunContext.state`, `transcript`, `artifacts`, and `usage` from the checkpoint's `state` / `transcript_length` / `artifact_count` / recorded usage. If the checkpoint does not exist, the runtime MUST raise `ConfigError` with a hint listing the available checkpoint_ids for that session (via `session_manager.list_checkpoints` when available, otherwise a message directing the caller to check `session.dump()`). The rehydrated run MUST use the original `run_id` stored in the checkpoint's `state["__run_id__"]` when present, so downstream events and transcripts remain stitched to the original run; otherwise it uses the new `request.run_id`.

#### Scenario: Explicit resume continues from mid-run state
- **WHEN** a process calls `runtime.run_detailed(request=RunRequest(..., resume_from_checkpoint="r1:step:5"))` and checkpoint `r1:step:5` exists for that session
- **THEN** `pattern.setup()` receives the transcript / artifacts / state / usage reconstructed from that checkpoint, and `pattern.execute()` continues from that point

#### Scenario: Unknown checkpoint raises actionable ConfigError
- **WHEN** `resume_from_checkpoint="does-not-exist"` is passed and the session has no such checkpoint
- **THEN** `Runtime.run_detailed(...)` raises `ConfigError` whose `hint` contains at least one example of an existing checkpoint_id for that session, or a clear message that no checkpoints exist

### Requirement: Event taxonomy declares durable-execution events

`openagents/interfaces/event_taxonomy.py` SHALL declare four new event schemas, each carrying a `run_id` field and following the existing taxonomy conventions (`EventSchema` with topic string + payload field list + docstring): `run.checkpoint_saved`, `run.checkpoint_failed`, `run.resume_attempted`, `run.resume_succeeded`, `run.resume_exhausted`. Existing events (`run.started`, `run.succeeded`, `run.failed`, `tool.*`, `llm.*`, etc.) MUST NOT change shape. The `RunStreamChunkKind` enum SHALL gain `CHECKPOINT_SAVED`, `RESUME_ATTEMPTED`, `RESUME_SUCCEEDED` variants, and `runtime/stream_projection.py` SHALL project the corresponding events into `RunStreamChunk`s so `run_stream()` consumers can observe durable-execution boundaries.

#### Scenario: Durable run's event stream includes checkpoint boundaries
- **WHEN** a consumer iterates `runtime.run_stream(request=<durable request>)` on a run that completes without error after 3 steps
- **THEN** the consumer observes 3 `RunStreamChunk(kind=CHECKPOINT_SAVED)` chunks interleaved with `LLM_*` / `TOOL_*` chunks, in source order

#### Scenario: Event taxonomy validator accepts the new events
- **WHEN** the test suite validates the event taxonomy against emitted event names
- **THEN** the five new `run.*` event names are present in `EVENT_TAXONOMY` and have non-empty docstrings
