"""Skill plugin contract."""

from __future__ import annotations

from typing import Any

from .plugin import BasePlugin


class SkillPlugin(BasePlugin):
    """Base skill plugin.

    Skills contribute domain-specific instructions, metadata, and default tools
    without changing the single-agent runtime contract.
    """

    name: str = ""
    description: str = ""

    def get_system_prompt(self, context: Any | None = None) -> str:
        """Return a system prompt fragment to append to the pattern prompt."""
        return ""

    def get_tools(self) -> list[Any]:
        """Return tool refs or tool ids that should be added to the agent."""
        return []

    def get_metadata(self) -> dict[str, Any]:
        """Return metadata exposed on the execution context."""
        return {}

    def augment_context(self, context: Any) -> None:
        """Mutate execution context after memory injection and before execution."""
        return None

    def filter_tools(
        self,
        tools: dict[str, Any],
        context: Any | None = None,
    ) -> dict[str, Any]:
        """Filter or replace the tool mapping for a run."""
        return tools

    async def before_run(self, context: Any) -> None:
        """Hook that runs immediately before pattern execution."""
        return None

    async def after_run(self, context: Any, result: Any) -> Any:
        """Hook that runs immediately after pattern execution."""
        return result
