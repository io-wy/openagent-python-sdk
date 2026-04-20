"""Stage 2 wizard step — environment doctor (CLI-local)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult, Wizard
from openagents.utils.env_doctor import CheckStatus, EnvironmentDoctor

from ..state import DeckProject
from ._layout import repaint

_KEY_LIKE_PREFIXES = ("LLM_", "TAVILY_", "OPENAI_", "ANTHROPIC_")


def _is_key_like(name: str) -> bool:
    return any(name.startswith(p) for p in _KEY_LIKE_PREFIXES)


@dataclass
class EnvDoctorWizardStep:
    doctor: EnvironmentDoctor
    title: str = "env"
    description: str = "Check required binaries and API keys."
    layout: Any = None
    log_ring: Any = None

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        repaint(console, self.layout, project)
        report = await self.doctor.run()
        project.env_report = report
        if console is not None:
            try:
                self._print_table(console, report)
            except Exception:
                pass

        # Required missing — must be resolved or aborted
        for name in report.missing_required:
            check = next((c for c in report.checks if c.name == name), None)
            if check is None:
                continue
            if _is_key_like(name):
                url = check.get_url or "your provider"
                value = await Wizard.password(f"Enter {name} (get it at {url}): ")
                if not value:
                    return StepResult(status="aborted")
                self.doctor.persist_env(name, value, "user")
            else:
                hint = check.fix_hint or ""
                proceed = await Wizard.confirm(
                    f"{name} is missing: {check.detail}. {hint}\nProceed anyway?",
                    default=False,
                )
                if not proceed:
                    return StepResult(status="aborted")

        # Optional missing — offer to enable feature
        for name in report.missing_optional:
            check = next((c for c in report.checks if c.name == name), None)
            if check is None:
                continue
            enable = await Wizard.confirm(
                f"Optional {name} missing. Enable this feature by providing the key?",
                default=False,
            )
            if enable:
                url = check.get_url or "your provider"
                value = await Wizard.password(f"Enter {name} (get it at {url}): ")
                if value:
                    self.doctor.persist_env(name, value, "user")

        project.stage = "research"
        return StepResult(status="completed")

    @staticmethod
    def _print_table(console: Any, report: Any) -> None:
        from rich.table import Table

        table = Table(title="Environment Check")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Detail")
        for c in report.checks:
            color = {
                CheckStatus.OK: "green",
                CheckStatus.MISSING: "red",
                CheckStatus.OUTDATED: "yellow",
            }.get(c.status, "white")
            table.add_row(c.name, f"[{color}]{c.status.value}[/{color}]", c.detail)
        console.print(table)
