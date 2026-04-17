"""Rich-powered console event bus wrapper."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from openagents.interfaces.events import (
    EVENT_EMIT,
    EVENT_HISTORY,
    EVENT_SUBSCRIBE,
    EventBusPlugin,
    RuntimeEvent,
)
from openagents.observability._rich import make_console, render_event_row
from openagents.observability.redact import redact

logger = logging.getLogger("openagents.events.rich_console")


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


class RichConsoleEventBus(EventBusPlugin):
    """Wraps another event bus and pretty-prints every matched event to the console.

    What:
        Forwards every emit to an inner bus first (so subscribers always
        run), then renders a rich Text/Panel to stdout/stderr. Filters by
        fnmatch globs (``include_events``/``exclude_events``); deny wins.
        Payload redaction via ``redact_keys`` and long-value truncation via
        ``max_value_length``. Render failures are logged and swallowed -
        event delivery is never disrupted.

    Usage:
        ``{"events": {"type": "rich_console", "config": {"inner":
        {"type": "async"}, "show_payload": true}}}``

    Depends on:
        - ``rich>=13.7.0`` (``pip install io-openagent-sdk[rich]``)
        - an inner event bus loaded via
          :func:`openagents.plugins.loader.load_plugin`
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        include_events: list[str] | None = None
        exclude_events: list[str] = Field(default_factory=list)
        redact_keys: list[str] = Field(
            default_factory=lambda: [
                "api_key",
                "authorization",
                "token",
                "secret",
                "password",
            ]
        )
        max_value_length: int = 500
        show_payload: bool = True
        stream: Literal["stdout", "stderr"] = "stderr"
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={EVENT_SUBSCRIBE, EVENT_EMIT, EVENT_HISTORY},
        )
        cfg = self.Config.model_validate(self.config)
        self._include = list(cfg.include_events) if cfg.include_events is not None else None
        self._exclude = list(cfg.exclude_events)
        self._redact_keys = list(cfg.redact_keys)
        self._max_value_length = cfg.max_value_length
        self._show_payload = cfg.show_payload
        self._console = make_console(cfg.stream)
        inner_ref = dict(cfg.inner)
        inner_cfg = dict(inner_ref.get("config") or {})
        inner_cfg.setdefault("max_history", cfg.max_history)
        inner_ref["config"] = inner_cfg
        self._inner = self._load_inner(inner_ref)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import EventBusRef
        from openagents.plugins.loader import load_plugin

        return load_plugin("events", EventBusRef(**ref), required_methods=("emit", "subscribe"))

    def _should_render(self, event_name: str) -> bool:
        if self._exclude and _matches_any(event_name, self._exclude):
            return False
        if self._include is None:
            return True
        return _matches_any(event_name, self._include)

    @property
    def history(self) -> list[RuntimeEvent]:
        """Transparent pass-through to the inner bus's history buffer."""
        return getattr(self._inner, "history", [])

    def subscribe(self, event_name: str, handler: Callable[[RuntimeEvent], Awaitable[None] | None]) -> None:
        self._inner.subscribe(event_name, handler)

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        event = await self._inner.emit(event_name, **payload)
        if self._should_render(event_name):
            try:
                redacted_payload = redact(
                    payload, keys=self._redact_keys, max_value_length=self._max_value_length
                )
                rendered_event = RuntimeEvent(name=event.name, payload=redacted_payload)
                renderable = render_event_row(rendered_event, show_payload=self._show_payload)
                self._console.print(renderable)
            except Exception as exc:
                logger.error("rich_console render failed: %s", exc, exc_info=True)
        return event

    async def get_history(self, event_name: str | None = None, limit: int | None = None) -> list[RuntimeEvent]:
        return await self._inner.get_history(event_name=event_name, limit=limit)

    async def clear_history(self) -> None:
        await self._inner.clear_history()
