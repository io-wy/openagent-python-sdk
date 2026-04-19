"""Basic round-trip checks for SqliteSessionManager (extras-gated)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from openagents.interfaces.session import SessionArtifact
from openagents.plugins.builtin.session.sqlite_backed import SqliteSessionManager


def _mgr(tmp_path: Path) -> SqliteSessionManager:
    return SqliteSessionManager(config={"db_path": str(tmp_path / "agent.db")})


@pytest.mark.asyncio
async def test_append_message_roundtrip(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("s1", {"role": "user", "content": "hi"})
    await m.append_message("s1", {"role": "assistant", "content": "hello"})

    m2 = _mgr(tmp_path)
    msgs = await m2.load_messages("s1")
    assert len(msgs) == 2
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_artifact_roundtrip(tmp_path: Path):
    m = _mgr(tmp_path)
    art = SessionArtifact(
        name="report",
        kind="markdown",
        payload="# hi",
        metadata={"k": "v"},
    )
    await m.save_artifact("s1", art)

    m2 = _mgr(tmp_path)
    loaded = await m2.list_artifacts("s1")
    assert len(loaded) == 1
    assert loaded[0].name == "report"
    assert loaded[0].metadata["k"] == "v"


@pytest.mark.asyncio
async def test_checkpoint_roundtrip(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("s1", {"role": "user", "content": "x"})
    cp = await m.create_checkpoint("s1", "cp1")
    assert cp.transcript_length == 1

    m2 = _mgr(tmp_path)
    loaded = await m2.load_checkpoint("s1", "cp1")
    assert loaded is not None
    assert loaded.checkpoint_id == "cp1"


@pytest.mark.asyncio
async def test_set_state_roundtrip(tmp_path: Path):
    m = _mgr(tmp_path)
    async with m.session("s1") as state:
        state["custom_counter"] = 42
        await m.set_state("s1", state)

    m2 = _mgr(tmp_path)
    state2 = await m2.get_state("s1")
    assert state2.get("custom_counter") == 42


@pytest.mark.asyncio
async def test_list_sessions_returns_known_sids(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("sA", {"role": "user", "content": "."})
    await m.append_message("sB", {"role": "user", "content": "."})

    m2 = _mgr(tmp_path)
    ids = await m2.list_sessions()
    assert set(ids) >= {"sA", "sB"}


@pytest.mark.asyncio
async def test_delete_session_clears_rows(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("s1", {"role": "user", "content": "hi"})
    await m.delete_session("s1")

    m2 = _mgr(tmp_path)
    # Fresh manager has not touched s1, so it isn't in list_sessions
    # (which unions on-disk session rows with the in-memory _states keys).
    ids = await m2.list_sessions()
    assert "s1" not in ids
    # Loading messages on s1 returns nothing now that the rows are gone.
    msgs = await m2.load_messages("s1")
    assert msgs == []
