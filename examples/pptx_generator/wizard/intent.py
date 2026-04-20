"""Stage 1 wizard step — intent analysis with per-field editing."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult, Wizard
from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory

from ..state import DeckProject, IntentReport
from ._editors import edit_intent
from ._layout import repaint


@dataclass
class IntentWizardStep:
    runtime: Any
    topic_hint: str | None = None
    title: str = "intent"
    description: str = "Understand what you want to present."
    layout: Any = None
    log_ring: Any = None

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        repaint(console, self.layout, project)
        report = await self._invoke_agent(project, topic_hint=self.topic_hint)
        while True:
            if console is not None:
                with contextlib.suppress(Exception):
                    console.print(Wizard.panel("Intent", self._format_report(report)))
            updated, action = await edit_intent(report)
            if action == "abort":
                return StepResult(status="aborted")
            if action == "regenerate":
                self.topic_hint = updated.topic
                report = await self._invoke_agent(project, topic_hint=updated.topic)
                continue
            # confirm
            project.intent = updated
            project.stage = "env"
            await self._maybe_capture_memory(updated)
            return StepResult(status="completed", data=updated)

    async def _maybe_capture_memory(self, report: IntentReport) -> None:
        save = await Wizard.confirm("Save these as long-term preferences?", default=False)
        if not save:
            return
        try:
            mem = MarkdownMemory(config={"memory_dir": "~/.config/pptx-agent/memory"})
            mem.capture(
                category="user_goals",
                rule=(f"tone={report.tone}; slide_count≈{report.slide_count_hint}; language={report.language}"),
                reason="confirmed at intent stage",
            )
        except Exception:
            # Memory failure must not break a successful generation.
            pass

    async def _invoke_agent(self, project: DeckProject, *, topic_hint: str | None) -> IntentReport:
        result = await self.runtime.run(
            agent_id="intent-analyst",
            session_id=project.slug,
            input_text=topic_hint or "",
        )
        if isinstance(result, IntentReport):
            return result
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, IntentReport):
            return parsed
        state = getattr(result, "state", None) or {}
        intent_dict = state.get("intent")
        if intent_dict is None:
            raise RuntimeError("intent-analyst returned no IntentReport")
        return IntentReport.model_validate(intent_dict)

    @staticmethod
    def _format_report(r: IntentReport) -> str:
        return "\n".join(
            [
                f"Topic:      {r.topic}",
                f"Audience:   {r.audience}",
                f"Purpose:    {r.purpose}",
                f"Tone:       {r.tone}",
                f"Slides:     {r.slide_count_hint}",
                f"Language:   {r.language}",
                f"Sections:   {', '.join(r.required_sections) or '(none)'}",
                f"Visuals:    {', '.join(r.visuals_hint) or '(none)'}",
                f"Research:   {', '.join(r.research_queries) or '(none)'}",
            ]
        )
