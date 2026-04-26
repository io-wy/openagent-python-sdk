"""OpenTelemetry tracing bridge for SDK events (optional extra: 'otel')."""

from __future__ import annotations

import fnmatch
import json
import logging
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from openagents.errors.exceptions import PluginLoadError
from openagents.interfaces.events import (
    EventBusPlugin,
    RuntimeEvent,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin

try:
    from opentelemetry import trace as otel_trace

    _HAS_OTEL = True
except ImportError:
    otel_trace = None  # type: ignore[assignment]
    _HAS_OTEL = False

logger = logging.getLogger("openagents.events.otel_bridge")


class OtelEventBusBridge(TypedConfigPluginMixin, EventBusPlugin):
    """OpenTelemetry tracing bridge for SDK events.

    What:
        Wraps another event bus. For each emit, creates a one-shot
        OTel span named ``openagents.<event_name>`` with payload
        flattened into span attributes (``oa.<key>=<json-or-str>``).
        Long values are truncated to ``max_attribute_chars``. Inner
        bus.emit always runs first, so subscribers and other
        wrappers (file_logging) are unaffected by OTel failures.

    Usage:
        ``{"events": {"type": "otel_bridge", "config": {"inner":
        {"type": "async"}, "tracer_name": "openagents",
        "include_events": ["tool.*", "llm.*"], "max_attribute_chars":
        4096}}}``. Requires the ``otel`` extra and that the host
        process has already configured an OTel TracerProvider (see
        opentelemetry-sdk docs). If no TracerProvider is configured,
        the OTel API no-ops and this bridge becomes free.

    Depends on:
        - the optional ``opentelemetry-api`` PyPI package
        - a globally configured OTel TracerProvider in the host
          process (provided by the user via opentelemetry-sdk)
        - inner event bus loaded via load_plugin
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        tracer_name: str = "openagents"
        include_events: list[str] | None = None
        max_attribute_chars: int = 4_096
        max_history: int = 10_000

    def __init__(self, config: dict[str, Any] | None = None):
        if not _HAS_OTEL:
            raise PluginLoadError(
                "events 'otel_bridge' requires the 'opentelemetry-api' package",
                hint="Install the 'otel' extra: uv sync --extra otel; "
                "also configure a TracerProvider via opentelemetry-sdk",
            )
        super().__init__(
            config=config or {},
        )
        self._init_typed_config()
        self._tracer = otel_trace.get_tracer(self.cfg.tracer_name)
        inner_ref = dict(self.cfg.inner)
        inner_cfg = dict(inner_ref.get("config") or {})
        inner_cfg.setdefault("max_history", self.cfg.max_history)
        inner_ref["config"] = inner_cfg
        self._inner = self._load_inner(inner_ref)

    def _load_inner(self, ref: dict[str, Any]) -> Any:
        from openagents.config.schema import EventBusRef
        from openagents.plugins.loader import load_plugin

        return load_plugin(
            "events",
            EventBusRef(**ref),
            required_methods=("emit", "subscribe"),
        )

    def _matches_include(self, name: str) -> bool:
        if self.cfg.include_events is None:
            return True
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.cfg.include_events)

    def _flatten_attribute(self, key: str, value: Any) -> tuple[str, str]:
        if isinstance(value, (str, int, float, bool)) or value is None:
            v = str(value)
        else:
            try:
                v = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                v = repr(value)
        if len(v) > self.cfg.max_attribute_chars:
            v = v[: self.cfg.max_attribute_chars] + "...[truncated]"
        return f"oa.{key}", v

    def subscribe(
        self,
        event_name: str,
        handler: Callable[[RuntimeEvent], Awaitable[None] | None],
    ) -> None:
        self._inner.subscribe(event_name, handler)

    async def emit(self, event_name: str, **payload: Any) -> RuntimeEvent:
        # Inner bus always runs first; it must not be blocked by OTel issues.
        event = await self._inner.emit(event_name, **payload)

        if not self._matches_include(event_name):
            return event

        try:
            with self._tracer.start_as_current_span(f"openagents.{event_name}") as span:
                for k, v in payload.items():
                    attr_k, attr_v = self._flatten_attribute(k, v)
                    span.set_attribute(attr_k, attr_v)
        except Exception as exc:  # noqa: BLE001 - OTel SDK exceptions vary
            logger.error("otel_bridge: failed to emit span for %s: %s", event_name, exc)

        return event

    async def get_history(
        self,
        event_name: str | None = None,
        limit: int | None = None,
    ) -> list[RuntimeEvent]:
        return await self._inner.get_history(event_name=event_name, limit=limit)

    async def clear_history(self) -> None:
        await self._inner.clear_history()
