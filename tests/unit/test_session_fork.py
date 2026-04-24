"""Contract tests for ``SessionManagerPlugin.fork_session`` across builtin backends.

Covers fix-multi-agent-p0-gaps section 3: the ``forked`` router isolation mode
requires a real history snapshot copy rather than just renaming the session id.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openagents.errors.exceptions import SessionError
from openagents.interfaces.session import SessionArtifact
from openagents.plugins.builtin.session.in_memory import InMemorySessionManager
from openagents.plugins.builtin.session.jsonl_file import JsonlFileSessionManager


def _make_managers():
    managers: list[tuple[str, object]] = [
        ("in_memory", InMemorySessionManager()),
    ]
    tmpdir = tempfile.mkdtemp(prefix="oa_session_fork_")
    managers.append(("jsonl_file", JsonlFileSessionManager(config={"root_dir": tmpdir})))
    try:
        from openagents.plugins.builtin.session.sqlite_backed import SqliteSessionManager

        db_path = str(Path(tmpdir) / "fork.db")
        managers.append(("sqlite", SqliteSessionManager(config={"db_path": db_path})))
    except Exception:  # noqa: BLE001 - aiosqlite extra may be absent
        pass
    return managers


MANAGERS = _make_managers()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,mgr", MANAGERS, ids=[m[0] for m in MANAGERS])
async def test_fork_copies_messages_and_artifacts(name, mgr):
    await mgr.append_message("src-a", {"role": "user", "content": "hi"})
    await mgr.append_message("src-a", {"role": "assistant", "content": "hello"})
    await mgr.save_artifact("src-a", SessionArtifact(name="a1", payload={"x": 1}))

    await mgr.fork_session("src-a", "dst-a")

    msgs = await mgr.load_messages("dst-a")
    assert [m["content"] for m in msgs] == ["hi", "hello"]
    arts = await mgr.list_artifacts("dst-a")
    assert [a.name for a in arts] == ["a1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,mgr", MANAGERS, ids=[m[0] for m in MANAGERS])
async def test_fork_rejects_existing_target(name, mgr):
    await mgr.append_message("src-b", {"role": "user", "content": "a"})
    await mgr.append_message("dst-b", {"role": "user", "content": "already there"})

    with pytest.raises(SessionError):
        await mgr.fork_session("src-b", "dst-b")


@pytest.mark.asyncio
@pytest.mark.parametrize("name,mgr", MANAGERS, ids=[m[0] for m in MANAGERS])
async def test_fork_isolated_writes(name, mgr):
    await mgr.append_message("src-c", {"role": "user", "content": "shared"})
    await mgr.fork_session("src-c", "dst-c")

    # Parent write after fork: does not reach child
    await mgr.append_message("src-c", {"role": "user", "content": "parent-only"})
    # Child write: does not reach parent
    await mgr.append_message("dst-c", {"role": "user", "content": "child-only"})

    src_msgs = [m["content"] for m in await mgr.load_messages("src-c")]
    dst_msgs = [m["content"] for m in await mgr.load_messages("dst-c")]
    assert src_msgs == ["shared", "parent-only"]
    assert dst_msgs == ["shared", "child-only"]
