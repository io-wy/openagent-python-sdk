"""Stage 5 wizard step — theme gallery + custom editor + memory capture."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult, Wizard
from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory

from ..state import DeckProject, ThemeCandidateList, ThemeSelection
from ._editors import edit_theme_custom
from ._layout import repaint


@dataclass
class ThemeWizardStep:
    runtime: Any
    title: str = "theme"
    description: str = "Pick palette, fonts, and style."
    layout: Any = None
    log_ring: Any = None

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        repaint(console, self.layout, project)
        bundle = await self._invoke_agent(project)
        if console is not None:
            with contextlib.suppress(Exception):
                self._render_gallery(console, bundle)

        pick_choices = [f"pick {i + 1}" for i in range(len(bundle.candidates))]
        pick_choices += ["regenerate", "custom editor", "abort"]
        action = await Wizard.select("Theme action?", choices=pick_choices, default=pick_choices[0])
        if action == "abort":
            return StepResult(status="aborted")
        if action == "regenerate":
            return StepResult(status="retry")

        if action == "custom editor":
            base = bundle.candidates[0]
            theme = await edit_theme_custom(base)
        else:
            # "pick N"
            idx = int(action.split(" ", maxsplit=1)[1]) - 1
            theme = bundle.candidates[idx]

        project.theme = theme
        project.stage = "slides"
        await self._maybe_capture_memory(theme)
        return StepResult(status="completed", data=theme)

    async def _maybe_capture_memory(self, theme: ThemeSelection) -> None:
        save = await Wizard.confirm("Save this theme as a preference?", default=False)
        if not save:
            return
        try:
            mem = MarkdownMemory(config={"memory_dir": "~/.config/pptx-agent/memory"})
            mem.capture(
                category="decisions",
                rule=(
                    f"theme primary=#{theme.palette.primary}; heading={theme.fonts.heading}; "
                    f"body={theme.fonts.body}; style={theme.style}"
                ),
                reason="confirmed at theme stage",
            )
        except Exception:
            pass

    async def _invoke_agent(self, project: DeckProject) -> ThemeCandidateList:
        result = await self.runtime.run(
            agent_id="theme-selector",
            session_id=project.slug,
            input_text="",
        )
        bundle = self._extract(result)
        if bundle is not None:
            return bundle
        raise RuntimeError("theme-selector returned no ThemeCandidateList")

    @staticmethod
    def _extract(result: Any) -> ThemeCandidateList | None:
        if isinstance(result, ThemeCandidateList):
            return result
        if isinstance(result, ThemeSelection):
            # Back-compat: wrap a single pick as a degenerate bundle of 3 copies
            return ThemeCandidateList(candidates=[result, result, result])
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, ThemeCandidateList):
            return parsed
        if isinstance(parsed, ThemeSelection):
            return ThemeCandidateList(candidates=[parsed, parsed, parsed])
        state = getattr(result, "state", None) or {}
        bundle_state = state.get("theme_candidates")
        if bundle_state is not None:
            try:
                return ThemeCandidateList.model_validate(bundle_state)
            except Exception:
                return None
        single = state.get("theme")
        if single is not None:
            with contextlib.suppress(Exception):
                ts = ThemeSelection.model_validate(single)
                return ThemeCandidateList(candidates=[ts, ts, ts])
        return None

    @staticmethod
    def _render_gallery(console: Any, bundle: ThemeCandidateList) -> None:
        from rich.columns import Columns
        from rich.panel import Panel

        panels = []
        for i, theme in enumerate(bundle.candidates, start=1):
            p = theme.palette
            body = (
                f"[bold]#{i}[/bold]\n"
                f"P #{p.primary}\nS #{p.secondary}\nA #{p.accent}\n"
                f"L #{p.light}\nB #{p.bg}\n"
                f"H: {theme.fonts.heading}\nB: {theme.fonts.body}\nCJK: {theme.fonts.cjk}\n"
                f"style: {theme.style}  badge: {theme.page_badge_style}"
            )
            panels.append(Panel(body, width=28, height=14))
        console.print(Columns(panels))
