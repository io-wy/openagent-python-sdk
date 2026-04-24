"""SQLite-backed session manager (optional extra: 'sqlite')."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel

from openagents.errors.exceptions import PluginLoadError, SessionError
from openagents.interfaces.session import (
    _ARTIFACTS_KEY,
    _CHECKPOINTS_KEY,
    _TRANSCRIPT_KEY,
    SESSION_ARTIFACTS,
    SESSION_CHECKPOINTS,
    SESSION_MANAGE,
    SESSION_STATE,
    SESSION_TRANSCRIPT,
    SessionArtifact,
    SessionCheckpoint,
    SessionManagerPlugin,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin
from openagents.plugins.builtin.session._reentry import reentrant_session

try:
    import aiosqlite

    _HAS_AIOSQLITE = True
except ImportError:
    aiosqlite = None  # type: ignore[assignment]
    _HAS_AIOSQLITE = False

logger = logging.getLogger("openagents.session.sqlite")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts TEXT NOT NULL,
    FOREIGN KEY (sid) REFERENCES sessions(sid)
);

CREATE INDEX IF NOT EXISTS idx_events_sid_seq ON events(sid, seq);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteSessionManager(TypedConfigPluginMixin, SessionManagerPlugin):
    """SQLite-backed session manager.

    What:
        Each mutation INSERTs one row into ``events`` with type
        ('transcript' | 'artifact' | 'checkpoint' | 'state'), JSON
        payload, and ISO timestamp. WAL mode + per-session asyncio.Lock
        gives concurrent reads safely while serializing writes per
        session. On first access prior rows are replayed to rebuild
        in-memory state.

    Usage:
        ``{"session": {"type": "sqlite", "config": {"db_path":
        ".sessions/agent.db", "wal": true, "synchronous": "NORMAL"}}}``
        Requires the ``sqlite`` extra: ``uv sync --extra sqlite``.

    Depends on:
        - the optional ``aiosqlite`` PyPI package
        - filesystem at ``db_path`` (parent dir created on init)
    """

    class Config(BaseModel):
        db_path: str
        wal: bool = True
        synchronous: Literal["OFF", "NORMAL", "FULL"] = "NORMAL"
        busy_timeout_ms: int = 5_000

    def __init__(self, config: dict[str, Any] | None = None):
        if not _HAS_AIOSQLITE:
            raise PluginLoadError(
                "session 'sqlite' requires the 'aiosqlite' package",
                hint="Install the 'sqlite' extra: uv sync --extra sqlite",
            )
        super().__init__(
            config=config or {},
            capabilities={
                SESSION_MANAGE,
                SESSION_STATE,
                SESSION_TRANSCRIPT,
                SESSION_ARTIFACTS,
                SESSION_CHECKPOINTS,
            },
        )
        self._init_typed_config()
        self._db_path = Path(self.cfg.db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._loaded: set[str] = set()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_db(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return
            async with aiosqlite.connect(self._db_path) as db:
                await db.executescript(_SCHEMA)
                if self.cfg.wal:
                    await db.execute("PRAGMA journal_mode=WAL")
                await db.execute(f"PRAGMA synchronous = {self.cfg.synchronous}")
                await db.execute(f"PRAGMA busy_timeout = {self.cfg.busy_timeout_ms}")
                await db.commit()
            self._initialized = True

    async def _ensure_session_row(self, db: Any, sid: str) -> None:
        ts = _now()
        await db.execute(
            "INSERT OR IGNORE INTO sessions(sid, created_at, updated_at) VALUES (?, ?, ?)",
            (sid, ts, ts),
        )
        await db.execute(
            "UPDATE sessions SET updated_at = ? WHERE sid = ?",
            (ts, sid),
        )

    async def _insert_event(self, sid: str, kind: str, data: Any) -> None:
        await self._ensure_db()
        try:
            payload = json.dumps(data, ensure_ascii=False, default=str)
            async with aiosqlite.connect(self._db_path) as db:
                await self._ensure_session_row(db, sid)
                await db.execute(
                    "INSERT INTO events(sid, type, payload, ts) VALUES (?, ?, ?, ?)",
                    (sid, kind, payload, _now()),
                )
                await db.commit()
        except aiosqlite.Error as exc:
            raise SessionError(
                f"sqlite_session: insert failed: {exc}",
                hint="check disk space and write permissions on db_path",
            ) from exc

    async def _ensure_loaded(self, sid: str) -> dict[str, Any]:
        if sid in self._loaded:
            return self._states.setdefault(sid, {})
        await self._ensure_db()
        state = self._states.setdefault(sid, {})
        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT seq, type, payload FROM events WHERE sid = ? ORDER BY seq",
                    (sid,),
                )
                rows = await cursor.fetchall()
                await cursor.close()
        except aiosqlite.Error as exc:
            raise SessionError(
                f"sqlite_session: replay failed: {exc}",
                hint="check that db_path is readable and not locked by another writer",
            ) from exc
        for row in rows:
            seq, kind, payload = row[0], row[1], row[2]
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning(
                    "sqlite_session: skipped bad row seq=%d in %s",
                    seq,
                    self._db_path,
                )
                continue
            if kind == "transcript" and isinstance(data, dict):
                state.setdefault(_TRANSCRIPT_KEY, []).append(data)
            elif kind == "artifact" and isinstance(data, dict):
                state.setdefault(_ARTIFACTS_KEY, []).append(data)
            elif kind == "checkpoint" and isinstance(data, dict):
                checkpoints = state.setdefault(_CHECKPOINTS_KEY, {})
                checkpoints[data.get("checkpoint_id", f"anon-{seq}")] = data
            elif kind == "state" and isinstance(data, dict):
                for k, v in data.items():
                    if k in (_TRANSCRIPT_KEY, _ARTIFACTS_KEY, _CHECKPOINTS_KEY):
                        continue
                    state[k] = v
        self._loaded.add(sid)
        return state

    @asynccontextmanager
    async def session(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with reentrant_session(lock, session_id):
            state = await self._ensure_loaded(session_id)
            yield state

    async def get_state(self, session_id: str) -> dict[str, Any]:
        return await self._ensure_loaded(session_id)

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        await self._ensure_loaded(session_id)
        self._states[session_id] = state
        payload = {k: v for k, v in state.items() if k not in (_TRANSCRIPT_KEY, _ARTIFACTS_KEY, _CHECKPOINTS_KEY)}
        if payload:
            await self._insert_event(session_id, "state", payload)

    async def delete_session(self, session_id: str) -> None:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            await self._ensure_db()
            self._states.pop(session_id, None)
            self._loaded.discard(session_id)
            try:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute("DELETE FROM events WHERE sid = ?", (session_id,))
                    await db.execute("DELETE FROM sessions WHERE sid = ?", (session_id,))
                    await db.commit()
            except aiosqlite.Error as exc:
                raise SessionError(
                    f"sqlite_session: delete failed: {exc}",
                    hint="check disk space and write permissions on db_path",
                ) from exc

    async def list_sessions(self) -> list[str]:
        await self._ensure_db()
        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute("SELECT sid FROM sessions")
                rows = await cursor.fetchall()
                await cursor.close()
        except aiosqlite.Error as exc:
            raise SessionError(
                f"sqlite_session: list_sessions failed: {exc}",
                hint="check that db_path is readable",
            ) from exc
        disk = {row[0] for row in rows}
        return sorted(disk | set(self._states.keys()))

    async def fork_session(self, source_session_id: str, target_session_id: str) -> None:
        """Copy source's events to target in a single transaction."""
        import copy

        await self._ensure_db()
        source_lock = self._locks.setdefault(source_session_id, asyncio.Lock())
        async with reentrant_session(source_lock, source_session_id):
            try:
                async with aiosqlite.connect(self._db_path) as db:
                    cursor = await db.execute("SELECT 1 FROM events WHERE sid = ? LIMIT 1", (target_session_id,))
                    exists = await cursor.fetchone() is not None
                    await cursor.close()
                    if exists or target_session_id in self._states:
                        raise SessionError(
                            f"sqlite_session: fork target '{target_session_id}' already exists",
                            hint="use a fresh target_session_id or delete_session first",
                        )
                    await db.execute("BEGIN")
                    ts = _now()
                    await db.execute(
                        "INSERT OR IGNORE INTO sessions(sid, created_at, updated_at) VALUES (?, ?, ?)",
                        (target_session_id, ts, ts),
                    )
                    await db.execute(
                        "INSERT INTO events(sid, type, payload, ts) "
                        "SELECT ?, type, payload, ts FROM events WHERE sid = ? ORDER BY seq",
                        (target_session_id, source_session_id),
                    )
                    await db.commit()
            except aiosqlite.Error as exc:
                raise SessionError(
                    f"sqlite_session: fork failed: {exc}",
                    hint="check disk space and write permissions on db_path",
                ) from exc
            # Mirror in-memory state so target reads see the snapshot immediately.
            self._states[target_session_id] = copy.deepcopy(self._states.get(source_session_id, {}))
            self._loaded.add(target_session_id)

    async def append_message(self, session_id: str, message: dict[str, Any]) -> None:
        state = await self._ensure_loaded(session_id)
        transcript = list(state.get(_TRANSCRIPT_KEY, []))
        entry = dict(message)
        transcript.append(entry)
        state[_TRANSCRIPT_KEY] = transcript
        await self._insert_event(session_id, "transcript", entry)

    async def save_artifact(self, session_id: str, artifact: SessionArtifact) -> None:
        state = await self._ensure_loaded(session_id)
        artifacts = list(state.get(_ARTIFACTS_KEY, []))
        data = artifact.to_dict()
        artifacts.append(data)
        state[_ARTIFACTS_KEY] = artifacts
        await self._insert_event(session_id, "artifact", data)

    async def create_checkpoint(self, session_id: str, checkpoint_id: str) -> SessionCheckpoint:
        state = await self._ensure_loaded(session_id)
        transcript = list(state.get(_TRANSCRIPT_KEY, []))
        artifacts = list(state.get(_ARTIFACTS_KEY, []))
        checkpoints = dict(state.get(_CHECKPOINTS_KEY, {}))
        checkpoint = SessionCheckpoint(
            checkpoint_id=checkpoint_id,
            state=dict(state),
            transcript_length=len(transcript),
            artifact_count=len(artifacts),
        )
        data = checkpoint.to_dict()
        checkpoints[checkpoint_id] = data
        state[_CHECKPOINTS_KEY] = checkpoints
        await self._insert_event(session_id, "checkpoint", data)
        return checkpoint
