from openagents.interfaces.runtime import (
    RunResult,
    RunStreamChunk,
    RunStreamChunkKind,
)


def test_stream_chunk_kind_values():
    assert RunStreamChunkKind.RUN_STARTED.value == "run.started"
    assert RunStreamChunkKind.LLM_DELTA.value == "llm.delta"
    assert RunStreamChunkKind.LLM_FINISHED.value == "llm.finished"
    assert RunStreamChunkKind.TOOL_STARTED.value == "tool.started"
    assert RunStreamChunkKind.TOOL_DELTA.value == "tool.delta"
    assert RunStreamChunkKind.TOOL_FINISHED.value == "tool.finished"
    assert RunStreamChunkKind.ARTIFACT.value == "artifact"
    assert RunStreamChunkKind.VALIDATION_RETRY.value == "validation.retry"
    assert RunStreamChunkKind.RUN_FINISHED.value == "run.finished"


def test_stream_chunk_roundtrip():
    chunk = RunStreamChunk(
        kind=RunStreamChunkKind.LLM_DELTA,
        run_id="r",
        session_id="s",
        agent_id="a",
        sequence=1,
        timestamp_ms=1000,
        payload={"text": "hi"},
    )
    assert chunk.kind is RunStreamChunkKind.LLM_DELTA
    assert chunk.result is None

    dump = chunk.model_dump()
    assert dump["payload"]["text"] == "hi"


def test_stream_chunk_carries_result_only_on_finished():
    terminal = RunStreamChunk(
        kind=RunStreamChunkKind.RUN_FINISHED,
        run_id="r",
        session_id="s",
        agent_id="a",
        sequence=9,
        timestamp_ms=9999,
        result=RunResult(run_id="r"),
    )
    assert terminal.result is not None
    assert terminal.result.run_id == "r"
