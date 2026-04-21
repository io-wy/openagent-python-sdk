"""Declared schema for events emitted by the SDK and built-in plugins.

Schema is **advisory**: ``EventBus.emit`` logs a warning when a declared
event name is emitted with missing required payload keys, but never
raises. Subscribers should not rely on the warning being present.

Custom user events not present in ``EVENT_SCHEMAS`` are emitted unchanged
with no validation.

To regenerate ``docs/event-taxonomy.md`` from this registry, run::

    uv run python -m openagents.tools.gen_event_doc
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventSchema:
    """Description of a single declared event name."""

    name: str
    required_payload: tuple[str, ...] = ()
    optional_payload: tuple[str, ...] = ()
    description: str = ""


EVENT_SCHEMAS: dict[str, EventSchema] = {
    # === existing events (carried over verbatim, no rename) ===
    "tool.called": EventSchema(
        "tool.called",
        ("tool_id", "params"),
        ("call_id",),
        "Pattern is about to invoke a tool.",
    ),
    "tool.succeeded": EventSchema(
        "tool.succeeded",
        ("tool_id", "result"),
        ("executor_metadata", "call_id"),
        "Tool returned successfully.",
    ),
    "tool.failed": EventSchema(
        "tool.failed",
        ("tool_id", "error"),
        ("call_id",),
        "Tool raised; final after fallback. Use 'tool.retry_requested' for ModelRetry signal.",
    ),
    "tool.retry_requested": EventSchema(
        "tool.retry_requested",
        ("tool_id", "attempt", "error"),
        ("call_id",),
        "Pattern caught ModelRetryError and is retrying.",
    ),
    "llm.called": EventSchema(
        "llm.called",
        ("model",),
        (),
        "Pattern is about to call an LLM.",
    ),
    "llm.succeeded": EventSchema(
        "llm.succeeded",
        ("model",),
        ("_metrics",),
        "LLM returned successfully. Optional '_metrics' carries LLMCallMetrics timing data.",
    ),
    "llm.failed": EventSchema(
        "llm.failed",
        ("model",),
        ("_metrics",),
        "LLM call failed. Optional '_metrics' carries LLMCallMetrics timing data.",
    ),
    "usage.updated": EventSchema(
        "usage.updated",
        ("usage",),
        (),
        "RunUsage object was updated; emitted after every LLM call.",
    ),
    "pattern.step_started": EventSchema(
        "pattern.step_started",
        ("step",),
        ("plan_step",),
        "Pattern began an execution step.",
    ),
    "pattern.step_finished": EventSchema(
        "pattern.step_finished",
        ("step", "action"),
        (),
        "Pattern completed an execution step.",
    ),
    "pattern.phase": EventSchema(
        "pattern.phase",
        ("phase",),
        (),
        "Pattern transitioned phases (e.g. planning, executing).",
    ),
    "pattern.plan_created": EventSchema(
        "pattern.plan_created",
        ("plan",),
        (),
        "PlanExecutePattern produced its plan.",
    ),
    # === new supplemental lifecycle events (Spec B WP2) ===
    "session.run.started": EventSchema(
        "session.run.started",
        ("agent_id", "session_id"),
        ("run_id", "input_text"),
        "Runtime begins a single run.",
    ),
    "session.run.completed": EventSchema(
        "session.run.completed",
        ("agent_id", "session_id", "stop_reason"),
        ("run_id", "duration_ms"),
        "Runtime finished a single run.",
    ),
    "context.assemble.started": EventSchema(
        "context.assemble.started",
        (),
        (),
        "context_assembler.assemble() is about to run.",
    ),
    "context.assemble.completed": EventSchema(
        "context.assemble.completed",
        ("transcript_size",),
        ("artifact_count", "duration_ms"),
        "context_assembler.assemble() returned.",
    ),
    "memory.inject.started": EventSchema(
        "memory.inject.started",
        (),
        (),
        "memory.inject() is about to run.",
    ),
    "memory.inject.completed": EventSchema(
        "memory.inject.completed",
        (),
        ("view_size",),
        "memory.inject() returned.",
    ),
    "memory.writeback.started": EventSchema(
        "memory.writeback.started",
        (),
        (),
        "memory.writeback() is about to run.",
    ),
    "memory.writeback.completed": EventSchema(
        "memory.writeback.completed",
        (),
        (),
        "memory.writeback() returned.",
    ),
    "tool.batch.started": EventSchema(
        "tool.batch.started",
        ("batch_id", "call_ids", "concurrent_count"),
        (),
        "A batched tool invocation started.",
    ),
    "tool.batch.completed": EventSchema(
        "tool.batch.completed",
        ("batch_id", "successes", "failures"),
        ("duration_ms",),
        "A batched tool invocation finished.",
    ),
    "tool.approval_needed": EventSchema(
        "tool.approval_needed",
        ("tool_id", "call_id", "params"),
        ("reason",),
        "Tool requires human approval; app must inject approvals[call_id] in next run.",
    ),
    "tool.cancelled": EventSchema(
        "tool.cancelled",
        ("tool_id", "call_id"),
        ("reason",),
        "Tool invocation was cancelled via cancel_event before completion.",
    ),
    "tool.background.submitted": EventSchema(
        "tool.background.submitted",
        ("tool_id", "call_id", "job_id"),
        (),
        "Background tool job was submitted; handle returned.",
    ),
    "tool.background.polled": EventSchema(
        "tool.background.polled",
        ("tool_id", "call_id", "job_id", "status"),
        ("progress",),
        "Background tool job was polled.",
    ),
    "tool.background.completed": EventSchema(
        "tool.background.completed",
        ("tool_id", "call_id", "job_id", "status"),
        (),
        "Background tool job reached terminal state (succeeded/failed/cancelled).",
    ),
    # === durable execution events ===
    "run.checkpoint_saved": EventSchema(
        "run.checkpoint_saved",
        ("run_id", "checkpoint_id", "step_index", "transcript_length"),
        (),
        "DefaultRuntime persisted a step checkpoint during a durable run.",
    ),
    "run.checkpoint_failed": EventSchema(
        "run.checkpoint_failed",
        ("run_id", "checkpoint_id", "error", "error_type"),
        (),
        "create_checkpoint raised during a durable run; the run continues.",
    ),
    "run.resume_attempted": EventSchema(
        "run.resume_attempted",
        ("run_id", "checkpoint_id", "error_type", "attempt_index"),
        (),
        "Durable run caught a retryable error and is about to load a checkpoint.",
    ),
    "run.resume_succeeded": EventSchema(
        "run.resume_succeeded",
        ("run_id", "checkpoint_id", "attempt_index"),
        (),
        "Durable run successfully rehydrated from a checkpoint and continues.",
    ),
    "run.resume_exhausted": EventSchema(
        "run.resume_exhausted",
        ("run_id", "attempt_index", "error_type", "limit"),
        (),
        "Durable run exceeded max_resume_attempts; the last retryable error propagates.",
    ),
    "run.durable_idempotency_warning": EventSchema(
        "run.durable_idempotency_warning",
        ("run_id", "tool_id"),
        ("hint",),
        "A tool declaring durable_idempotent=False was invoked inside a durable run (one-shot per run/tool).",
    ),
}
