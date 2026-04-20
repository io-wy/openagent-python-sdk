"""Async event bus implementation - in-memory with history."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from openagents.interfaces.event_taxonomy import EVENT_SCHEMAS
from openagents.interfaces.events import (
    EVENT_EMIT,
    EVENT_HISTORY,
    EVENT_SUBSCRIBE,
    EventBusPlugin,
    RuntimeEvent,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin

logger = logging.getLogger("openagents")


class AsyncEventBus(TypedConfigPluginMixin, EventBusPlugin):
    """Async in-memory event bus with history.

    What:
        Stores events in a bounded ring buffer and dispatches to
        per-name and ``*`` wildcard subscribers. Performs the
        advisory schema check from
        :data:`openagents.interfaces.event_taxonomy.EVENT_SCHEMAS`
        on each emit and logs a warning on missing required keys.
        Default for single-instance deployments and test suites.

    Usage:
        ``{"events": {"type": "async", "config": {"max_history":
        10000}}}``

    Depends on:
        - :data:`openagents.interfaces.event_taxonomy.EVENT_SCHEMAS`
          for the advisory payload check
    """

    class Config(BaseModel):
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={EVENT_SUBSCRIBE, EVENT_EMIT, EVENT_HISTORY},
        )
        self._init_typed_config()
        self._subscribers: dict[str, list[Callable[[RuntimeEvent], Awaitable[None] | None]]] = {}
        self._history: list[RuntimeEvent] = []
        self._max_history: int = self.cfg.max_history

    @property
    def history(self) -> list[RuntimeEvent]:
        """Get all events (backward compatibility)."""
        return self._history

    def subscribe(self, event_name: str, handler: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        """Subscribe to an event."""
        self._subscribers.setdefault(event_name, []).append(handler)

    def unsubscribe(
        self,
        event_name: str,
        handler: Callable[[RuntimeEvent], Awaitable[None] | None],
    ) -> None:
        """Remove a previously registered handler. Missing entries are ignored."""
        handlers = self._subscribers.get(event_name)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        """Emit an event.

        For declared events (see
        :data:`openagents.interfaces.event_taxonomy.EVENT_SCHEMAS`),
        missing required payload keys produce a ``logger.warning``;
        delivery to subscribers proceeds unchanged. Custom event
        names not present in the taxonomy are emitted without checks.

        Invariant: handlers are dispatched **inline** within this call —
        every matching subscriber (including wildcard ``*`` handlers) is
        awaited before ``emit()`` returns. Durable execution relies on this
        to ensure step checkpoints are written while the runtime still
        holds the session lock.
        """
        schema = EVENT_SCHEMAS.get(event_name)
        if schema is not None:
            missing = [k for k in schema.required_payload if k not in payload]
            if missing:
                logger.warning(
                    "event '%s' missing required payload keys %s (declared in event_taxonomy.EVENT_SCHEMAS)",
                    event_name,
                    missing,
                )
        event = RuntimeEvent(name=event_name, payload=payload)
        self._history.append(event)

        # Trim history if needed
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

        handlers = []
        handlers.extend(self._subscribers.get(event_name, []))
        handlers.extend(self._subscribers.get("*", []))  # Wildcard handlers
        for handler in handlers:
            try:
                result = handler(event)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                logger.error("Event handler failed for %s: %s", event_name, exc, exc_info=True)

        return event

    async def get_history(
        self,
        event_name: str | None = None,
        limit: int | None = None,
    ) -> list[RuntimeEvent]:
        """Get event history."""
        history = self._history
        if event_name:
            history = [e for e in history if e.name == event_name]
        if limit:
            history = history[-limit:]
        return history

    async def clear_history(self) -> None:
        """Clear event history."""
        self._history.clear()
