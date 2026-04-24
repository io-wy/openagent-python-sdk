"""Tests for task-reentrant session locks across all builtin backends.

Covers fix-multi-agent-p0-gaps section 2: `shared` isolation mode must nest
`async with session(sid)` within one asyncio task without deadlocking, while
independent tasks still serialize.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from openagents.plugins.builtin.session._reentry import (
    _HELD_SESSIONS,
    reentrant_session,
)
from openagents.plugins.builtin.session.in_memory import InMemorySessionManager
from openagents.plugins.builtin.session.jsonl_file import JsonlFileSessionManager


def _make_managers():
    managers: list[tuple[str, object]] = [
        ("in_memory", InMemorySessionManager()),
    ]
    tmpdir = tempfile.mkdtemp(prefix="oa_session_reentry_")
    managers.append(("jsonl_file", JsonlFileSessionManager(config={"root_dir": tmpdir})))
    try:
        from openagents.plugins.builtin.session.sqlite_backed import SqliteSessionManager

        db_path = str(Path(tmpdir) / "reentry.db")
        managers.append(("sqlite", SqliteSessionManager(config={"db_path": db_path})))
    except Exception:  # noqa: BLE001 - aiosqlite extra may be absent; skip the backend
        pass
    return managers


MANAGERS = _make_managers()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,mgr", MANAGERS, ids=[m[0] for m in MANAGERS])
async def test_nested_same_task_does_not_deadlock(name, mgr):
    async def scenario():
        async with mgr.session("s-nested") as _outer:
            async with mgr.session("s-nested") as _inner:
                pass

    await asyncio.wait_for(scenario(), timeout=2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,mgr", MANAGERS, ids=[m[0] for m in MANAGERS])
async def test_cross_task_mutual_exclusion(name, mgr):
    """Independent tasks must still serialize on the same session_id."""
    order: list[str] = []

    async def worker(tag: str, hold_for: float) -> None:
        async with mgr.session("s-cross"):
            order.append(f"{tag}-enter")
            await asyncio.sleep(hold_for)
            order.append(f"{tag}-exit")

    # Start A first; give the scheduler a tick so A enters before B runs.
    task_a = asyncio.create_task(worker("A", 0.05))
    await asyncio.sleep(0)
    task_b = asyncio.create_task(worker("B", 0.01))
    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2.0)

    # A enters first, fully exits, THEN B enters - no interleaving.
    assert order == ["A-enter", "A-exit", "B-enter", "B-exit"], order


@pytest.mark.asyncio
@pytest.mark.parametrize("name,mgr", MANAGERS, ids=[m[0] for m in MANAGERS])
async def test_reentry_cleared_after_exit(name, mgr):
    """After the outer session exits, a fresh async with must still acquire."""
    async with mgr.session("s-re"):
        pass
    # Second independent entry should not believe we still hold the lock.
    async with mgr.session("s-re"):
        pass
    # And the contextvar should be back to empty in this task.
    assert _HELD_SESSIONS.get() == frozenset()


@pytest.mark.asyncio
async def test_reentrant_session_helper_flag():
    """The helper's yielded flag reports True on real acquire, False on re-entry."""
    lock = asyncio.Lock()
    async with reentrant_session(lock, "s") as acquired_outer:
        assert acquired_outer is True
        async with reentrant_session(lock, "s") as acquired_inner:
            assert acquired_inner is False
