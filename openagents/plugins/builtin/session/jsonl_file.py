"""JSONL-file backed session manager."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import BaseModel

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

logger = logging.getLogger("openagents.session.jsonl_file")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonlFileSessionManager(SessionManagerPlugin):
    """Append-only NDJSON persistence for sessions.

    What:
        Each mutation appends one line of the form
        ``{"type": "transcript|artifact|checkpoint|state", "data":
        ..., "ts": ISO}`` under ``root_dir/<session_id>.jsonl``. On
        first access prior lines are replayed to rebuild in-memory
        state. Per-session ``asyncio.Lock`` serializes writers so
        concurrent appends preserve order.

    Usage:
        ``{"session": {"type": "jsonl_file", "config": {"root_dir":
        ".sessions", "fsync": false}}}``. Set ``fsync=true`` when
        durability across power loss matters.

    Depends on:
        - the local filesystem at ``root_dir`` (created on init)
        - synchronous ``open`` / ``write`` on the event loop thread;
          fine for typical transcripts, swap in a purpose-built
          backend for heavy concurrent writes
    """

    class Config(BaseModel):
        root_dir: str
        fsync: bool = False

    def __init__(self, config: dict[str, Any] | None = None):
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
        cfg = self.Config.model_validate(self.config)
        self._root = Path(cfg.root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._fsync = cfg.fsync
        self._locks: dict[str, asyncio.Lock] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._loaded: set[str] = set()

    def _path(self, sid: str) -> Path:
        return self._root / f"{sid}.jsonl"

    def _append(self, sid: str, event: dict[str, Any]) -> None:
        path = self._path(sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            if self._fsync:
                fh.flush()
                os.fsync(fh.fileno())

    def _ensure_loaded(self, sid: str) -> dict[str, Any]:
        if sid in self._loaded:
            return self._states.setdefault(sid, {})
        state = self._states.setdefault(sid, {})
        path = self._path(sid)
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                for idx, line in enumerate(fh, start=1):
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "jsonl_file: skipped bad line %d in %s "
                            "(hint: inspect or back up this file; replay-skip continues)",
                            idx,
                            path,
                        )
                        continue
                    kind = event.get("type")
                    data = event.get("data")
                    if kind == "transcript" and isinstance(data, dict):
                        state.setdefault(_TRANSCRIPT_KEY, []).append(data)
                    elif kind == "artifact" and isinstance(data, dict):
                        state.setdefault(_ARTIFACTS_KEY, []).append(data)
                    elif kind == "checkpoint" and isinstance(data, dict):
                        checkpoints = state.setdefault(_CHECKPOINTS_KEY, {})
                        checkpoints[data.get("checkpoint_id", f"anon-{idx}")] = data
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
        await lock.acquire()
        try:
            state = self._ensure_loaded(session_id)
            yield state
        finally:
            lock.release()

    async def get_state(self, session_id: str) -> dict[str, Any]:
        return self._ensure_loaded(session_id)

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        self._ensure_loaded(session_id)
        self._states[session_id] = state
        payload = {k: v for k, v in state.items() if k not in (_TRANSCRIPT_KEY, _ARTIFACTS_KEY, _CHECKPOINTS_KEY)}
        if payload:
            self._append(session_id, {"type": "state", "data": payload, "ts": _now()})

    async def delete_session(self, session_id: str) -> None:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            self._states.pop(session_id, None)
            self._loaded.discard(session_id)
            path = self._path(session_id)
            if path.exists():
                path.unlink()

    async def list_sessions(self) -> list[str]:
        # `Path.stem` strips only the final suffix, so "sess.1.jsonl" -> "sess.1".
        # Session IDs containing dots round-trip correctly here and in `_path`.
        disk = {p.stem for p in self._root.glob("*.jsonl")}
        return sorted(disk | set(self._states.keys()))

    async def append_message(self, session_id: str, message: dict[str, Any]) -> None:
        state = self._ensure_loaded(session_id)
        transcript = list(state.get(_TRANSCRIPT_KEY, []))
        entry = dict(message)
        transcript.append(entry)
        state[_TRANSCRIPT_KEY] = transcript
        self._append(session_id, {"type": "transcript", "data": entry, "ts": _now()})

    async def save_artifact(self, session_id: str, artifact: SessionArtifact) -> None:
        state = self._ensure_loaded(session_id)
        artifacts = list(state.get(_ARTIFACTS_KEY, []))
        data = artifact.to_dict()
        artifacts.append(data)
        state[_ARTIFACTS_KEY] = artifacts
        self._append(session_id, {"type": "artifact", "data": data, "ts": _now()})

    async def create_checkpoint(self, session_id: str, checkpoint_id: str) -> SessionCheckpoint:
        state = self._ensure_loaded(session_id)
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
        self._append(session_id, {"type": "checkpoint", "data": data, "ts": _now()})
        return checkpoint
