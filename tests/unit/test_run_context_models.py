from __future__ import annotations

from dataclasses import dataclass

import openagents.interfaces.runtime as runtime_mod
import openagents.interfaces.session as session_mod
import pytest


@dataclass
class DemoDeps:
    token: str


def test_run_context_keeps_typed_deps():
    from openagents.interfaces.run_context import RunContext

    stop_reason = getattr(runtime_mod, "StopReason")
    ctx = RunContext[DemoDeps](
        agent_id="assistant",
        session_id="demo",
        run_id="run-1",
        input_text="hello",
        deps=DemoDeps(token="abc"),
        event_bus=object(),
    )

    assert ctx.deps.token == "abc"
    assert stop_reason.COMPLETED.value == "completed"


def test_session_artifact_is_immutable():
    artifact = session_mod.SessionArtifact(name="note.txt", payload={"ok": True})

    with pytest.raises(Exception):
        artifact.name = "changed.txt"
