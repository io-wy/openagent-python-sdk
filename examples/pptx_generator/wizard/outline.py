"""Stage 4 wizard step — outline with add / remove / reorder / edit-per-slide."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult

from ..state import DeckProject, SlideOutline
from ._editors import edit_outline
from ._layout import repaint


@dataclass
class OutlineWizardStep:
    runtime: Any
    title: str = "outline"
    description: str = "Plan the slide-by-slide structure."
    layout: Any = None
    log_ring: Any = None

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        repaint(console, self.layout, project)
        outline = await self._invoke_agent(project)
        if console is not None:
            with contextlib.suppress(Exception):
                self._render_table(console, outline)

        updated, action = await edit_outline(outline)
        if action == "abort":
            return StepResult(status="aborted")
        if action == "regenerate":
            return StepResult(status="retry")

        project.outline = updated
        project.stage = "theme"
        return StepResult(status="completed", data=updated)

    async def _invoke_agent(self, project: DeckProject) -> SlideOutline:
        result = await self.runtime.run(
            agent_id="outliner",
            session_id=project.slug,
            input_text="",
        )
        if isinstance(result, SlideOutline):
            return result
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, SlideOutline):
            return parsed
        state = getattr(result, "state", None) or {}
        return SlideOutline.model_validate(state.get("outline", {}))

    @staticmethod
    def _render_table(console: Any, outline: SlideOutline) -> None:
        from rich.table import Table

        table = Table(title="Outline")
        table.add_column("#")
        table.add_column("Type")
        table.add_column("Title")
        table.add_column("Key Points")
        for s in outline.slides:
            table.add_row(str(s.index), s.type, s.title, "; ".join(s.key_points))
        console.print(table)
