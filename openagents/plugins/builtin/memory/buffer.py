"""Builtin buffer memory plugin."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_WRITEBACK
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class BufferMemory(TypedConfigPluginMixin, MemoryPlugin):
    """Append-only in-session memory with configurable projection.

    What:
        Stores every (input, tool_results, output) triple in a single
        list inside session state and projects the recent slice into
        ``context.memory_view``. Suitable as a default short-term
        memory for stateless agents.

    Usage:
        ``{"type": "buffer", "config": {"state_key": "memory_buffer",
        "view_key": "history", "max_items": 50}}`` (all keys
        optional; ``max_items`` defaults to unbounded).

    Depends on:
        - ``RunContext.state`` (writeable session state)
        - ``RunContext.memory_view`` (writeable inject target)
        - ``RunContext.input_text``, ``RunContext.tool_results``,
          ``RunContext.state['_runtime_last_output']``
    """

    class Config(BaseModel):
        state_key: str = "memory_buffer"
        view_key: str = "history"
        max_items: int | None = Field(default=None, gt=0)

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={MEMORY_INJECT, MEMORY_WRITEBACK},
        )
        self._init_typed_config()

    def _state_key(self) -> str:
        return self.cfg.state_key

    def _view_key(self) -> str:
        return self.cfg.view_key

    def _max_items(self) -> int | None:
        return self.cfg.max_items

    def _get_buffer(self, context: Any) -> list[dict[str, Any]]:
        state_key = self._state_key()
        current = context.state.get(state_key)
        if not isinstance(current, list):
            current = []
            context.state[state_key] = current
        return current

    def _slice_for_view(self, buffer: list[dict[str, Any]]) -> list[dict[str, Any]]:
        max_items = self._max_items()
        if max_items is None:
            return list(buffer)
        return list(buffer[-max_items:])

    def _trim_in_place(self, buffer: list[dict[str, Any]]) -> None:
        max_items = self._max_items()
        if max_items is None:
            return
        if len(buffer) > max_items:
            del buffer[:-max_items]

    async def inject(self, context: Any) -> None:
        buffer = self._get_buffer(context)
        context.memory_view[self._view_key()] = self._slice_for_view(buffer)

    async def compact(self, context: Any) -> None:
        """No-op: BufferMemory already trims in _trim_in_place during writeback."""

    async def writeback(self, context: Any) -> None:
        buffer = self._get_buffer(context)

        record: dict[str, Any] = {
            "input": context.input_text,
            "tool_results": list(context.tool_results),
        }
        if "_runtime_last_output" in context.state:
            record["output"] = context.state["_runtime_last_output"]

        buffer.append(record)
        self._trim_in_place(buffer)
        context.memory_view[self._view_key()] = self._slice_for_view(buffer)
