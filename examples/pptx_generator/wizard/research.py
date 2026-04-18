"""Stage 3 wizard step — research (Tavily MCP-driven)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult, Wizard

from ..state import DeckProject, ResearchFindings


@dataclass
class ResearchWizardStep:
    runtime: Any
    title: str = "research"
    description: str = "Gather facts via Tavily (MCP → REST fallback)."

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        assert project.intent is not None, "ResearchWizardStep requires intent"
        if not project.intent.research_queries:
            project.research = ResearchFindings()
            project.stage = "outline"
            return StepResult(status="skipped")

        result = await self.runtime.run(
            agent_id="research-agent",
            session_id=project.slug,
            input_text="",
        )
        findings = self._extract_findings(result)

        if console is not None:
            try:
                self._render_tree(console, findings)
            except Exception:
                pass

        if findings.sources:
            chosen = await Wizard.multi_select(
                "Keep which sources? (enter with no selection to keep all)",
                choices=[s.title for s in findings.sources],
                min_selected=0,
            )
            if chosen:
                keep = set(chosen)
                findings = findings.model_copy(update={
                    "sources": [s for s in findings.sources if s.title in keep]
                })

        project.research = findings
        project.stage = "outline"
        return StepResult(status="completed")

    @staticmethod
    def _extract_findings(result: Any) -> ResearchFindings:
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, ResearchFindings):
            return parsed
        state = getattr(result, "state", None) or {}
        data = state.get("research")
        if data is None:
            return ResearchFindings()
        return ResearchFindings.model_validate(data)

    @staticmethod
    def _render_tree(console: Any, findings: ResearchFindings) -> None:
        from rich.tree import Tree

        tree = Tree("Research Findings")
        for src in findings.sources:
            tree.add(f"[bold]{src.title}[/bold]  {src.url}\n   {src.snippet}")
        console.print(tree)
