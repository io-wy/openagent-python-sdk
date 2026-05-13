"""Shared helpers for the CoreCoder agent unit tests.

Avoids pulling in the full :class:`RunContext` model just to set
``scratch`` and ``tool_results`` — a :class:`SimpleNamespace` with the right
attributes is enough for the tools we exercise here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def make_ctx(**overrides: Any) -> Any:
    """Build a minimal stub context for tool invocation tests."""
    base = {
        "scratch": {},
        "tool_results": [],
        "session_id": "test-session",
        "agent_id": "test-agent",
        "run_id": "test-run",
        "input_text": "",
        "tools": {},
        "transcript": [],
        "memory_view": {},
        "system_prompt_fragments": [],
        "state": {},
        "artifacts": [],
        "session_artifacts": [],
        "assembly_metadata": {},
        "llm_client": None,
        "usage": None,
        "run_request": None,
        "tool_executor": None,
        "event_bus": SimpleNamespace(emit=_noop_emit),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def _noop_emit(*args: Any, **kwargs: Any) -> None:
    return None
