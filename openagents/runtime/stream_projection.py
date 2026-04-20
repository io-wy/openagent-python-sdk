"""Maps runtime event-bus events to RunStreamChunk kinds.

The mapping is intentionally written in kernel code (not a plugin) so stream
consumers see a stable, provider-agnostic surface. New emit points in the
runtime must be added to this table as well as the event bus itself.
"""

from __future__ import annotations

from typing import Any

from openagents.interfaces.runtime import RunStreamChunkKind

EVENT_TO_CHUNK_KIND: dict[str, RunStreamChunkKind] = {
    "run.started": RunStreamChunkKind.RUN_STARTED,
    "llm.delta": RunStreamChunkKind.LLM_DELTA,
    "llm.succeeded": RunStreamChunkKind.LLM_FINISHED,
    "tool.called": RunStreamChunkKind.TOOL_STARTED,
    "tool.delta": RunStreamChunkKind.TOOL_DELTA,
    "tool.succeeded": RunStreamChunkKind.TOOL_FINISHED,
    "tool.failed": RunStreamChunkKind.TOOL_FINISHED,
    "validation.retry": RunStreamChunkKind.VALIDATION_RETRY,
    "artifact.emitted": RunStreamChunkKind.ARTIFACT,
    "run.checkpoint_saved": RunStreamChunkKind.CHECKPOINT_SAVED,
    "run.resume_attempted": RunStreamChunkKind.RESUME_ATTEMPTED,
    "run.resume_succeeded": RunStreamChunkKind.RESUME_SUCCEEDED,
}


def project_event(
    event_name: str,
    payload: dict[str, Any],
) -> tuple[RunStreamChunkKind, dict[str, Any]] | None:
    """Project an event to a (chunk_kind, payload) tuple, or None if unmapped."""
    kind = EVENT_TO_CHUNK_KIND.get(event_name)
    if kind is None:
        return None
    return kind, dict(payload)
