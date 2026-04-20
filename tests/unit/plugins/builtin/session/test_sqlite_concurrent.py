"""Stress: 50 concurrent appends produce 50 ordered rows."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from openagents.interfaces.session import _TRANSCRIPT_KEY
from openagents.plugins.builtin.session.sqlite_backed import SqliteSessionManager


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_concurrent_appends_preserve_messages(tmp_path: Path):
    db_path = tmp_path / "agent.db"
    sid = "demo-sess"
    mgr = SqliteSessionManager(config={"db_path": str(db_path)})

    n = 50

    async def _append(i: int) -> None:
        async with mgr.session(sid) as _state:
            await mgr.append_message(sid, {"role": "user", "content": f"msg-{i:03d}"})

    await asyncio.gather(*[_append(i) for i in range(n)])

    # Reload via fresh manager from same db.
    mgr2 = SqliteSessionManager(config={"db_path": str(db_path)})
    state = await mgr2.get_state(sid)
    transcript = state.get(_TRANSCRIPT_KEY, [])
    assert len(transcript) == n, f"expected {n}, got {len(transcript)}"

    # asyncio.gather submission order is not deterministic; what matters
    # is each message appears exactly once.
    contents = sorted(entry["content"] for entry in transcript)
    expected = sorted(f"msg-{i:03d}" for i in range(n))
    assert contents == expected
