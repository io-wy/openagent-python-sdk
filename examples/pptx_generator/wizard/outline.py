"""Stage 4 wizard step — outline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult, Wizard

from ..state import DeckProject, SlideOutline


@dataclass
class OutlineWizardStep:
    runtime: Any
    title: str = "outline"
    description: str = "Plan the slide-by-slide structure."

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        result = await self.runtime.run(
            agent_id="outliner",
            session_id=project.slug,
            input_text="",
        )
        outline = self._extract(result)

        if console is not None:
            try:
                self._render_table(console, outline)
            except Exception:
                pass

        action = await Wizard.select(
            "Outline action?",
            choices=["accept", "regenerate", "abort"],
            default="accept",
        )
        if action == "regenerate":
            return StepResult(status="retry")
        if action == "abort":
            return StepResult(status="aborted")

        project.outline = outline
        project.stage = "theme"
        return StepResult(status="completed")

    @staticmethod
    def _extract(result: Any) -> SlideOutline:
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
