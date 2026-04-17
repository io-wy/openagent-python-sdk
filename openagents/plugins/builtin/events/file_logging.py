"""File-logging event bus wrapper."""

from __future__ import annotations

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

logger = logging.getLogger("openagents.events.file_logging")


class FileLoggingEventBus(EventBusPlugin):
    """Wraps another event bus and appends every matched event to an NDJSON log.

    Use for audit trails or post-mortem debugging. File I/O is synchronous per
    ``emit`` call; if the log write fails, the error is logged but event delivery
    to subscribers is never disrupted.
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        log_path: str
        include_events: list[str] | None = None
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={EVENT_SUBSCRIBE, EVENT_EMIT, EVENT_HISTORY},
        )
        cfg = self.Config.model_validate(self.config)
        self._log_path = Path(cfg.log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._include = set(cfg.include_events) if cfg.include_events is not None else None
        self._inner = self._load_inner(cfg.inner)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import EventBusRef
        from openagents.plugins.loader import _load_plugin

        return _load_plugin("events", EventBusRef(**ref), required_methods=("emit", "subscribe"))

    def subscribe(self, event_name: str, handler: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        self._inner.subscribe(event_name, handler)

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        event = await self._inner.emit(event_name, **payload)
        if self._include is None or event_name in self._include:
            try:
                line = json.dumps(
                    {"name": event_name, "payload": payload, "ts": datetime.now(timezone.utc).isoformat()},
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
