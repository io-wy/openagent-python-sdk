from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from openagents.interfaces.session import SessionArtifact
from openagents.plugins.builtin.session.jsonl_file import JsonlFileSessionManager
from openagents.plugins.registry import get_builtin_plugin_class


def _mgr(tmp_path: Path) -> JsonlFileSessionManager:
    return JsonlFileSessionManager(config={"root_dir": str(tmp_path / "sessions")})


@pytest.mark.asyncio
async def test_append_and_reload_transcript(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("s1", {"role": "user", "content": "hi"})
    await m.append_message("s1", {"role": "assistant", "content": "hello"})

    m2 = _mgr(tmp_path)
    msgs = await m2.load_messages("s1")
    assert len(msgs) == 2
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_artifacts_round_trip(tmp_path: Path):
    m = _mgr(tmp_path)
    art = SessionArtifact(name="report", kind="markdown", payload="# hi", metadata={"k": "v"})
    await m.save_artifact("s1", art)
    m2 = _mgr(tmp_path)
    loaded = await m2.list_artifacts("s1")
    assert len(loaded) == 1
    assert loaded[0].name == "report"
    assert loaded[0].metadata["k"] == "v"


@pytest.mark.asyncio
async def test_checkpoint_round_trip(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("s1", {"role": "user", "content": "x"})
    cp = await m.create_checkpoint("s1", "cp1")
    assert cp.transcript_length == 1
    m2 = _mgr(tmp_path)
    loaded = await m2.load_checkpoint("s1", "cp1")
    assert loaded is not None and loaded.checkpoint_id == "cp1"


@pytest.mark.asyncio
async def test_corrupted_line_skipped(tmp_path: Path):
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    (root / "s1.jsonl").write_text(
        '{"type":"transcript","data":{"role":"user","content":"ok"},"ts":"t0"}\n'
        "not-json-at-all\n"
        '{"type":"transcript","data":{"role":"assistant","content":"y"},"ts":"t1"}\n',
        encoding="utf-8",
    )
    m = JsonlFileSessionManager(config={"root_dir": str(root)})
    msgs = await m.load_messages("s1")
    assert [msg["content"] for msg in msgs] == ["ok", "y"]


@pytest.mark.asyncio
async def test_delete_session_removes_file(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("s1", {"role": "user", "content": "hi"})
    assert (tmp_path / "sessions" / "s1.jsonl").exists()
    await m.delete_session("s1")
    assert not (tmp_path / "sessions" / "s1.jsonl").exists()


@pytest.mark.asyncio
async def test_list_sessions_scans_dir(tmp_path: Path):
    m = _mgr(tmp_path)
    await m.append_message("sA", {"role": "user", "content": "."})
    await m.append_message("sB", {"role": "user", "content": "."})
    m2 = _mgr(tmp_path)
    ids = await m2.list_sessions()
    assert set(ids) >= {"sA", "sB"}


@pytest.mark.asyncio
async def test_set_state_persists_custom_keys(tmp_path: Path):
    m = _mgr(tmp_path)
    async with m.session("s1") as state:
        state["custom_counter"] = 42
        await m.set_state("s1", state)
    m2 = _mgr(tmp_path)
    state2 = await m2.get_state("s1")
    assert state2.get("custom_counter") == 42


@pytest.mark.asyncio
async def test_delete_session_serialized_with_writes(tmp_path: Path):
    """delete_session must hold the per-session lock so concurrent append cannot
    resurrect the file after unlink."""
    import asyncio as _asyncio

    m = _mgr(tmp_path)
    # Prime the session so the file and lock exist.
    await m.append_message("s1", {"role": "user", "content": "seed"})

    async def do_delete():
        await _asyncio.sleep(0)
        await m.delete_session("s1")

    async def do_append():
        await _asyncio.sleep(0)
        await m.append_message("s1", {"role": "user", "content": "late"})

    # Fire concurrently; they will be serialized by the per-session lock.
    await _asyncio.gather(do_delete(), do_append())
    # After gather, regardless of order, either:
    #   (a) delete ran first, append recreated the file with ONE "late" line,
    #   (b) append ran first, delete removed everything.
    # Both states are acceptable — the race we are preventing is *interleaving*
    # that corrupts the file or leaves dangling in-memory state.
    sessions = await m.list_sessions()
    if "s1" in sessions:
        msgs = await m.load_messages("s1")
        assert msgs == [{"role": "user", "content": "late"}]
    else:
        # Fresh manager should see nothing persistent for s1.
        m2 = _mgr(tmp_path)
        ids = await m2.list_sessions()
        assert "s1" not in ids


@pytest.mark.asyncio
async def test_append_recreates_root_dir_if_removed_after_init(tmp_path: Path):
    m = _mgr(tmp_path)
    sessions_root = tmp_path / "sessions"
    shutil.rmtree(sessions_root)

    await m.append_message("s1", {"role": "user", "content": "hi"})

    assert (sessions_root / "s1.jsonl").exists()
    m2 = _mgr(tmp_path)
    assert await m2.load_messages("s1") == [{"role": "user", "content": "hi"}]


def test_registered_as_builtin():
    assert get_builtin_plugin_class("session", "jsonl_file") is JsonlFileSessionManager
