"""Asyncio-task reentrant session lock helper.

Shared by the builtin session backends so `async with session(sid)` can be nested
within the same asyncio task (needed for `agent_router.delegate(..., session_isolation='shared')`),
while preserving mutual exclusion across independent tasks.

The reentrancy set is a `contextvars.ContextVar`, which inherits across `await`
boundaries within one task but is NOT shared between independently-scheduled
tasks - exactly the semantics we need.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

_HELD_SESSIONS: ContextVar[frozenset[str]] = ContextVar("_openagents_held_sessions", default=frozenset())


@asynccontextmanager
async def reentrant_session(lock: asyncio.Lock, session_id: str) -> AsyncIterator[bool]:
    """Acquire ``lock`` unless the current asyncio task already holds it for ``session_id``.

    Yields True when the lock was acquired in this frame, False when the call
    reentered an already-held lock. Callers normally ignore the flag; it's
    exposed for diagnostics and tests.
    """
    held = _HELD_SESSIONS.get()
    if session_id in held:
        yield False
        return
    await lock.acquire()
    token = _HELD_SESSIONS.set(held | {session_id})
    try:
        yield True
    finally:
        _HELD_SESSIONS.reset(token)
        lock.release()
