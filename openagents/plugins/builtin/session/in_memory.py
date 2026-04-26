"""In-memory session manager implementation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pydantic import BaseModel

from openagents.interfaces.session import (
    SessionManagerPlugin,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin
from openagents.plugins.builtin.session._reentry import reentrant_session


class InMemorySessionManager(TypedConfigPluginMixin, SessionManagerPlugin):
    """In-memory session manager with async locks.

    What:
        Stores transcript, artifacts, checkpoints, and free-form
        state per session in process memory. Acquires a per-session
        ``asyncio.Lock`` so concurrent ``runtime.run`` calls against
        the same session id serialize. Lost on process restart -
        use ``jsonl_file`` for persistence.

    Usage:
        ``{"session": {"type": "in_memory"}}`` (no config required).

    Depends on:
        - nothing (pure in-process state)
    """

    class Config(BaseModel):
        pass

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
        )
        self._init_typed_config()
        self._locks: dict[str, asyncio.Lock] = {}
        self._states: dict[str, dict] = {}

    @asynccontextmanager
    async def session(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Acquire and manage a session with a task-reentrant async lock."""
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with reentrant_session(lock, session_id):
            state = await self.get_state(session_id)
            yield state

    async def get_state(self, session_id: str) -> dict[str, Any]:
        """Get current session state."""
        state = self._states.get(session_id)
        if state is None:
            state = {}
            self._states[session_id] = state
        return state

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Set session state."""
        self._states[session_id] = state

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and its state."""
        self._states.pop(session_id, None)

    async def list_sessions(self) -> list[str]:
        """List all active session IDs."""
        return list(self._states.keys())

    async def fork_session(self, source_session_id: str, target_session_id: str) -> None:
        """Deep-copy source's state into a fresh target session."""
        import copy

        from openagents.errors.exceptions import SessionError

        target_existing = self._states.get(target_session_id)
        if target_existing:
            raise SessionError(
                f"in_memory_session: fork target '{target_session_id}' already exists",
                hint="use a fresh target_session_id or delete_session first",
            )
        source_lock = self._locks.setdefault(source_session_id, asyncio.Lock())
        async with reentrant_session(source_lock, source_session_id):
            source_state = self._states.get(source_session_id, {})
            self._states[target_session_id] = copy.deepcopy(source_state)
