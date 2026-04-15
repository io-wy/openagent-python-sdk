"""Host-level skills component contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .plugin import BasePlugin


@dataclass
class SessionSkillSummary:
    """Lightweight skill metadata cached in session state."""

    name: str
    description: str
    display_name: str = ""
    default_prompt: str = ""
    references_loaded: list[str] = field(default_factory=list)
    last_result_summary: str | None = None


class SkillsPlugin(BasePlugin):
    """Top-level skill package manager.

    This component lives alongside runtime/session/events and is responsible for:
    - discovering skill packages
    - preloading lightweight descriptions into session state
    - loading references progressively
    - executing skill entrypoints on demand
    """

    async def prepare_session(
        self,
        *,
        session_id: str,
        session_manager: Any,
    ) -> dict[str, SessionSkillSummary]:
        raise NotImplementedError("SkillsPlugin.prepare_session must be implemented")

    async def load_references(
        self,
        *,
        session_id: str,
        skill_name: str,
        session_manager: Any,
    ) -> list[dict[str, str]]:
        raise NotImplementedError("SkillsPlugin.load_references must be implemented")

    async def run_skill(
        self,
        *,
        session_id: str,
        skill_name: str,
        payload: dict[str, Any],
        session_manager: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError("SkillsPlugin.run_skill must be implemented")
