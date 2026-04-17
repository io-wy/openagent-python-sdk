"""File-logging event bus wrapper."""

from __future__ import annotations

import fnmatch
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from openagents.interfaces.events import (
    EVENT_EMIT,
    EVENT_HISTORY,
    EVENT_SUBSCRIBE,
    EventBusPlugin,
    RuntimeEvent,
)
from openagents.observability.redact import redact

logger = logging.getLogger("openagents.events.file_logging")


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


class FileLoggingEventBus(EventBusPlugin):
    """Wraps another event bus and appends every matched event to an NDJSON log.

    What:
        Forwards every emit to an inner bus first (so subscribers always
        run), then appends a JSON line to ``log_path``. Supports fnmatch
        glob filtering via ``include_events``/``exclude_events``, payload
        redaction via ``redact_keys``, and long-value truncation via
        ``max_value_length``. File-write failures are logged and swallowed -
        event delivery is never disrupted by IO errors.

    Usage:
        ``{"events": {"type": "file_logging", "config": {"log_path":
        ".logs/events.ndjson", "inner": {"type": "async"},
        "include_events": ["tool.*"], "redact_keys": ["api_key"]}}}``

    Depends on:
        - the local filesystem at ``log_path``
        - an inner event bus loaded via
          :func:`openagents.plugins.loader.load_plugin`
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        log_path: str
        include_events: list[str] | None = None
        exclude_events: list[str] = Field(default_factory=list)
        redact_keys: list[str] = Field(default_factory=list)
        max_value_length: int = 10_000
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={EVENT_SUBSCRIBE, EVENT_EMIT, EVENT_HISTORY},
        )
        cfg = self.Config.model_validate(self.config)
        self._log_path = Path(cfg.log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._include = list(cfg.include_events) if cfg.include_events is not None else None
        self._exclude = list(cfg.exclude_events)
        self._redact_keys = list(cfg.redact_keys)
        self._max_value_length = cfg.max_value_length
        inner_ref = dict(cfg.inner)
        inner_cfg = dict(inner_ref.get("config") or {})
        inner_cfg.setdefault("max_history", cfg.max_history)
        inner_ref["config"] = inner_cfg
        self._inner = self._load_inner(inner_ref)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import EventBusRef
        from openagents.plugins.loader import load_plugin

        return load_plugin("events", EventBusRef(**ref), required_methods=("emit", "subscribe"))

    def _should_log(self, event_name: str) -> bool:
        if self._exclude and _matches_any(event_name, self._exclude):
            return False
        if self._include is None:
            return True
        return _matches_any(event_name, self._include)

    def subscribe(self, event_name: str, handler: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        self._inner.subscribe(event_name, handler)

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        event = await self._inner.emit(event_name, **payload)
        if self._should_log(event_name):
            try:
                rendered_payload = redact(
                    payload,
                    keys=self._redact_keys,
                    max_value_length=self._max_value_length,
                )
                line = json.dumps(
                    {
                        "name": event_name,
                        "payload": rendered_payload,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    default=str,
                )
                with open(self._log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError as exc:
                logger.error("file_logging: append failed: %s", exc)
        return event

    async def get_history(self, event_name: str | None = None, limit: int | None = None) -> list[RuntimeEvent]:
        return await self._inner.get_history(event_name=event_name, limit=limit)

    async def clear_history(self) -> None:
        await self._inner.clear_history()
