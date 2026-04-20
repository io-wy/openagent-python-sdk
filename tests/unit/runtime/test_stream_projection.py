"""Tests for the runtime event → RunStreamChunk projection table."""

from __future__ import annotations

from openagents.interfaces.runtime import RunStreamChunkKind
from openagents.runtime.stream_projection import EVENT_TO_CHUNK_KIND, project_event


def test_mapping_covers_required_events():
    required = {
        "run.started",
        "llm.delta",
        "llm.succeeded",
        "tool.called",
        "tool.delta",
        "tool.succeeded",
        "tool.failed",
        "validation.retry",
    }
    assert required.issubset(EVENT_TO_CHUNK_KIND.keys())


def test_project_event_returns_none_for_unknown():
    assert project_event("totally.unknown", {}) is None


def test_project_event_maps_llm_delta():
    result = project_event("llm.delta", {"text": "hi", "model": "m"})
    assert result is not None
    kind, payload = result
    assert kind is RunStreamChunkKind.LLM_DELTA
    assert payload == {"text": "hi", "model": "m"}


def test_project_event_maps_tool_finished_from_both_success_and_failure():
    for name in ("tool.succeeded", "tool.failed"):
        result = project_event(name, {"tool_id": "t"})
        assert result is not None
        assert result[0] is RunStreamChunkKind.TOOL_FINISHED
