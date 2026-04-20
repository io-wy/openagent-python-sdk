"""Pretty-printing event bus for the pptx-agent CLI.

Wraps an inner ``async`` bus (standard delivery) and renders a curated
subset of events as Rich renderables tuned for tool calls and LLM
activity. Noise events (memory.*, context.*, session.*, run.*) are
suppressed by default so the wizard UI stays readable.

Formatting of individual events delegates to
:class:`openagents.cli._events.EventFormatter` so a transcript rendered
here matches what ``openagents run`` / ``openagents replay`` produce.

Tavily-aware result formatting: when a tool returns a dict with a
``results`` list, each result is shown as a compact table of
title/url/snippet instead of dumping raw JSON.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from openagents.cli._events import EventFormatter, default_excludes, matches_any
from openagents.interfaces.events import (
    EVENT_EMIT,
    EVENT_HISTORY,
    EVENT_SUBSCRIBE,
    EventBusPlugin,
    RuntimeEvent,
)
from openagents.observability._rich import make_console


class PrettyEventBus(EventBusPlugin):
    """Rich event bus with tool/LLM-aware formatting.

    Config:
        inner: dict                -- inner bus ref (default async)
        include_events: list[str]? -- if set, only render matching names
        exclude_events: list[str]  -- deny-list (wins over include); defaults
                                      to a sensible noise suppression set
        stream: "stdout" | "stderr" (default "stderr")
        show_details: bool         -- include per-field breakdown (default True)
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        include_events: list[str] | None = None
        exclude_events: list[str] = Field(default_factory=default_excludes)
        stream: Literal["stdout", "stderr"] = "stderr"
        show_details: bool = True
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={EVENT_SUBSCRIBE, EVENT_EMIT, EVENT_HISTORY},
        )
        cfg = self.Config.model_validate(self.config)
        self._include = list(cfg.include_events) if cfg.include_events is not None else None
        self._exclude = list(cfg.exclude_events)
        self._console = make_console(cfg.stream)
        inner_ref = dict(cfg.inner)
        inner_cfg = dict(inner_ref.get("config") or {})
        inner_cfg.setdefault("max_history", cfg.max_history)
        inner_ref["config"] = inner_cfg
        self._inner = self._load_inner(inner_ref)
        self._formatter = EventFormatter(self._console, show_details=cfg.show_details)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import EventBusRef
        from openagents.plugins.loader import load_plugin

        return load_plugin("events", EventBusRef(**ref), required_methods=("emit", "subscribe"))

    def _should_render(self, event_name: str) -> bool:
        if self._exclude and matches_any(event_name, self._exclude):
            return False
        if self._include is None:
            return True
        return matches_any(event_name, self._include)

    @property
    def history(self) -> list[RuntimeEvent]:
        return getattr(self._inner, "history", [])

    def subscribe(
        self,
        event_name: str,
        handler: Callable[[RuntimeEvent], Awaitable[None] | None],
    ) -> None:
        self._inner.subscribe(event_name, handler)

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        event = await self._inner.emit(event_name, **payload)
        if self._should_render(event_name):
            try:
                self._formatter.render(event_name, payload)
            except Exception:
                # never disrupt event delivery; swallow render errors
                pass
        return event

    async def get_history(self, event_name: str | None = None, limit: int | None = None) -> list[RuntimeEvent]:
        return await self._inner.get_history(event_name=event_name, limit=limit)

    async def clear_history(self) -> None:
        await self._inner.clear_history()
