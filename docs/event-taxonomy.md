# Event Taxonomy

Events emitted by the SDK and built-in plugins. Schema is **advisory**: the
async event bus logs a warning when a declared event is emitted with
missing required payload keys, but never raises. Custom events not
present here are emitted unchanged with no validation.

The source of truth is
[`openagents/interfaces/event_taxonomy.py`](../openagents/interfaces/event_taxonomy.py).
Regenerate this file via::

    uv run python -m openagents.tools.gen_event_doc

| Event | Required payload | Optional payload | Description |
|---|---|---|---|
| `context.assemble.completed` | `transcript_size` | `artifact_count`, `duration_ms` | context_assembler.assemble() returned. |
| `context.assemble.started` | — | — | context_assembler.assemble() is about to run. |
| `context.compact.completed` | — | `transcript_size`, `duration_ms` | context_assembler.compact() returned. |
| `context.compact.started` | — | — | context_assembler.compact() is about to run. |
| `context.compact_failed` | `agent_id`, `session_id`, `error` | `error_details` | context_assembler.compact() raised; run continues depending on on_error config. |
| `context.compact_succeeded` | `agent_id`, `session_id` | — | context_assembler.compact() succeeded. |
| `llm.called` | `model` | — | Pattern is about to call an LLM. |
| `llm.failed` | `model` | `_metrics`, `error`, `error_details` | LLM call failed. Optional '_metrics' carries LLMCallMetrics timing data. |
| `llm.succeeded` | `model` | `_metrics` | LLM returned successfully. Optional '_metrics' carries LLMCallMetrics timing data. |
| `memory.compact.completed` | — | — | memory.compact() returned. |
| `memory.compact.started` | — | — | memory.compact() is about to run. |
| `memory.compact_failed` | `agent_id`, `session_id`, `error` | `error_details` | memory.compact() raised; run continues depending on on_error config. |
| `memory.compact_succeeded` | `agent_id`, `session_id` | — | memory.compact() succeeded. |
| `memory.inject.completed` | — | `view_size` | memory.inject() returned. |
| `memory.inject.started` | — | — | memory.inject() is about to run. |
| `memory.inject_failed` | `agent_id`, `session_id`, `error` | `error_details` | memory.inject() raised; run continues or fails depending on on_error config. |
| `memory.injected` | `agent_id`, `session_id` | — | memory.inject() succeeded. |
| `memory.writeback.completed` | — | — | memory.writeback() returned. |
| `memory.writeback.started` | — | — | memory.writeback() is about to run. |
| `memory.writeback_failed` | `agent_id`, `session_id`, `error` | `error_details` | memory.writeback() raised; run continues or fails depending on on_error config. |
| `memory.writeback_succeeded` | `agent_id`, `session_id` | — | memory.writeback() succeeded. |
| `pattern.phase` | `phase` | — | Pattern transitioned phases (e.g. planning, executing). |
| `pattern.plan_created` | `plan` | — | PlanExecutePattern produced its plan. |
| `pattern.step_finished` | `step`, `action` | — | Pattern completed an execution step. |
| `pattern.step_started` | `step` | `plan_step` | Pattern began an execution step. |
| `run.checkpoint_failed` | `run_id`, `checkpoint_id`, `error`, `error_type` | `error_details` | create_checkpoint raised during a durable run; the run continues. |
| `run.checkpoint_saved` | `run_id`, `checkpoint_id`, `step_index`, `transcript_length` | — | DefaultRuntime persisted a step checkpoint during a durable run. |
| `run.durable_idempotency_warning` | `run_id`, `tool_id` | `hint` | A tool declaring durable_idempotent=False was invoked inside a durable run (one-shot per run/tool). |
| `run.failed` | `agent_id`, `session_id`, `error` | `run_id`, `error_details` | Run terminated with an unhandled error. |
| `run.resume_attempted` | `run_id`, `checkpoint_id`, `error_type`, `attempt_index` | `error_code` | Durable run caught a retryable error and is about to load a checkpoint. |
| `run.resume_exhausted` | `run_id`, `attempt_index`, `error_type`, `limit` | `error_code` | Durable run exceeded max_resume_attempts; the last retryable error propagates. |
| `run.resume_succeeded` | `run_id`, `checkpoint_id`, `attempt_index` | — | Durable run successfully rehydrated from a checkpoint and continues. |
| `session.run.completed` | `agent_id`, `session_id`, `stop_reason` | `run_id`, `duration_ms` | Runtime finished a single run. |
| `session.run.started` | `agent_id`, `session_id` | `run_id`, `input_text` | Runtime begins a single run. |
| `tool.approval_needed` | `tool_id`, `call_id`, `params` | `reason` | Tool requires human approval; app must inject approvals[call_id] in next run. |
| `tool.background.completed` | `tool_id`, `call_id`, `job_id`, `status` | — | Background tool job reached terminal state (succeeded/failed/cancelled). |
| `tool.background.polled` | `tool_id`, `call_id`, `job_id`, `status` | `progress` | Background tool job was polled. |
| `tool.background.submitted` | `tool_id`, `call_id`, `job_id` | — | Background tool job was submitted; handle returned. |
| `tool.batch.completed` | `batch_id`, `successes`, `failures` | `duration_ms` | A batched tool invocation finished. |
| `tool.batch.started` | `batch_id`, `call_ids`, `concurrent_count` | — | A batched tool invocation started. |
| `tool.called` | `tool_id`, `params` | `call_id` | Pattern is about to invoke a tool. |
| `tool.cancelled` | `tool_id`, `call_id` | `reason` | Tool invocation was cancelled via cancel_event before completion. |
| `tool.failed` | `tool_id`, `error` | `call_id`, `error_details` | Tool raised; final after fallback. Use 'tool.retry_requested' for ModelRetry signal. |
| `tool.retry_requested` | `tool_id`, `attempt`, `error` | `call_id` | Pattern caught ModelRetryError and is retrying. |
| `tool.succeeded` | `tool_id`, `result` | `executor_metadata`, `call_id` | Tool returned successfully. |
| `usage.updated` | `usage` | — | RunUsage object was updated; emitted after every LLM call. |

## OpenTelemetry mapping

The optional `events.otel_bridge` builtin maps SDK events onto OpenTelemetry
spans without altering the inner event bus contract. The mapping is one-to-one
and stateless:

| SDK | OpenTelemetry |
|---|---|
| event_name | span name `openagents.<event_name>` (e.g. `openagents.tool.succeeded`) |
| `payload[key]` = `value` | span attribute `oa.<key>` with the string-coerced or JSON-serialized value |
| value longer than `max_attribute_chars` (default 4096) | truncated to that length plus the literal suffix
`...[truncated]` |
| `include_events` filter (fnmatch) | only matching events produce spans; non-matches still go through the inner bus |

Spans are one-shot: nothing happens inside the `with` block beyond setting
attributes, so `start_time` and `end_time` are nearly equal. Pairing
`session.run.started`/`session.run.completed` into a single parent span is
out of scope for the current bridge.

Configure a `TracerProvider` in the host process via `opentelemetry-sdk`
plus an exporter of your choice; without one the OTel API no-ops and the
bridge becomes essentially free.
