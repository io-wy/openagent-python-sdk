"""Stage 3 wizard step — research (Tavily MCP-driven)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult, Wizard
from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory

from ..state import DeckProject, ResearchFindings
from ._layout import repaint


@dataclass
class ResearchWizardStep:
    runtime: Any
    title: str = "research"
    description: str = "Gather facts via Tavily (MCP → REST fallback)."
    layout: Any = None
    log_ring: Any = None

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        repaint(console, self.layout, project)
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
            with contextlib.suppress(Exception):
                self._render_tree(console, findings)

        if findings.sources:
            chosen = await Wizard.multi_select(
                "Keep which sources? (enter with no selection to keep all)",
                choices=[s.title for s in findings.sources],
                min_selected=0,
            )
            if chosen:
                keep = set(chosen)
                findings = findings.model_copy(update={"sources": [s for s in findings.sources if s.title in keep]})

        project.research = findings
        project.stage = "outline"
        await self._maybe_capture_memory(findings)
        return StepResult(status="completed")

    @staticmethod
    async def _maybe_capture_memory(findings: ResearchFindings) -> None:
        if not findings.sources:
            return
        save = await Wizard.confirm("Save these sources as research references?", default=False)
        if not save:
            return
        try:
            mem = MarkdownMemory(config={"memory_dir": "~/.config/pptx-agent/memory"})
            top = findings.sources[:3]
            rule = "; ".join(f"{s.title} — {s.url}" for s in top)
            mem.capture(
                category="references",
                rule=rule,
                reason="confirmed at research stage",
            )
        except Exception:
            pass

    @staticmethod
    def _extract_findings(result: Any) -> ResearchFindings:
        if isinstance(result, ResearchFindings):
            return result
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
