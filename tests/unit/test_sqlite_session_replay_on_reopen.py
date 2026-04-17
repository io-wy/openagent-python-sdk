"""Replay rebuilds full state when a fresh manager opens an existing db."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from openagents.interfaces.session import (
    SessionArtifact,
    _ARTIFACTS_KEY,
    _CHECKPOINTS_KEY,
    _TRANSCRIPT_KEY,
)
from openagents.plugins.builtin.session.sqlite_backed import SqliteSessionManager


@pytest.mark.asyncio
async def test_replay_reconstructs_transcript_artifacts_state(tmp_path: Path):
    db_path = tmp_path / "agent.db"

    m1 = SqliteSessionManager(config={"db_path": str(db_path)})
    await m1.append_message("s1", {"role": "user", "content": "first"})
    await m1.append_message("s1", {"role": "assistant", "content": "second"})
    await m1.save_artifact(
        "s1",
        SessionArtifact(name="r1", kind="text", payload="hi", metadata={}),
    )
    cp = await m1.create_checkpoint("s1", "cp-1")
    assert cp.transcript_length == 2
    async with m1.session("s1") as state:
        state["counter"] = 7
        await m1.set_state("s1", state)

    # Drop manager; reopen a fresh one pointing at the same db file.
    m2 = SqliteSessionManager(config={"db_path": str(db_path)})
    state = await m2.get_state("s1")
    transcript = state.get(_TRANSCRIPT_KEY, [])
    artifacts = state.get(_ARTIFACTS_KEY, [])
    checkpoints = state.get(_CHECKPOINTS_KEY, {})

    assert [m["content"] for m in transcript] == ["first", "second"]
    assert len(artifacts) == 1 and artifacts[0]["name"] == "r1"
    assert "cp-1" in checkpoints
    assert state.get("counter") == 7


@pytest.mark.asyncio
async def test_replay_skips_corrupt_payload_row(tmp_path: Path):
    """A row with non-JSON payload is logged and skipped on replay."""
    db_path = tmp_path / "agent.db"

    m1 = SqliteSessionManager(config={"db_path": str(db_path)})
    await m1.append_message("s1", {"role": "user", "content": "ok"})

    # Inject a corrupt row directly.
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO events(sid, type, payload, ts) VALUES (?, ?, ?, ?)",
            ("s1", "transcript", "not-json-at-all", "t-bad"),
        )
        await db.commit()

    await m1.append_message("s1", {"role": "assistant", "content": "y"})

    m2 = SqliteSessionManager(config={"db_path": str(db_path)})
    msgs = await m2.load_messages("s1")
    assert [m["content"] for m in msgs] == ["ok", "y"]
