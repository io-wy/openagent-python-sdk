"""Stage 6 wizard step — parallel slide generation with validate-retry-fallback."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from openagents.cli.wizard import StepResult, Wizard
from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory

from ..state import DeckProject
from ._layout import repaint
from ._slide_runner import LiveStatusTable, SlideRunRecord, SlideStatus, generate_slide


@dataclass
class SlideGeneratorWizardStep:
    runtime: Any
    concurrency: int = 3
    title: str = "slides"
    description: str = "Generate each slide via retry/fallback loop."
    max_retries: int = 2
    layout: Any = None
    log_ring: Any = None

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        repaint(console, self.layout, project)
        assert project.outline is not None, "SlideGeneratorWizardStep requires outline"
        sem = asyncio.Semaphore(self.concurrency)
        live_table = LiveStatusTable()

        def on_status(record: SlideRunRecord) -> None:
            live_table.update(record)

        async def run_one(spec: Any) -> SlideRunRecord:
            async with sem:
                return await generate_slide(
                    self.runtime,
                    spec,
                    project.theme,
                    session_id=project.slug,
                    max_retries=self.max_retries,
                    on_status=on_status,
                )

        records: list[SlideRunRecord] = list(await asyncio.gather(*(run_one(s) for s in project.outline.slides)))
        records.sort(key=lambda r: r.spec.index)

        slides = [r.ir for r in records if r.ir is not None]
        if not slides:
            raise RuntimeError("all slides failed to produce a SlideIR")
        project.slides = slides
        project.stage = "compile"

        if console is not None:
            with contextlib.suppress(Exception):
                self._print_summary(console, live_table, records)

        # Offer per-failed-index re-run
        failed_indices = [r.spec.index for r in records if r.status != SlideStatus.OK]
        if failed_indices:
            await self._maybe_rerun_failed(project, failed_indices, records, live_table, on_status)

        await self._maybe_capture_memory(project)
        return StepResult(status="completed")

    async def _maybe_rerun_failed(
        self,
        project: DeckProject,
        failed_indices: list[int],
        records: list[SlideRunRecord],
        live_table: LiveStatusTable,
        on_status: Any,
    ) -> None:
        choices = [f"rerun {i}" for i in failed_indices] + ["continue"]
        action = await Wizard.select("Re-run any failed slide?", choices=choices, default="continue")
        if action == "continue":
            return
        idx = int(action.split(" ", maxsplit=1)[1])
        target = next((r for r in records if r.spec.index == idx), None)
        if target is None:
            return
        new_record = await generate_slide(
            self.runtime,
            target.spec,
            project.theme,
            session_id=project.slug,
            max_retries=self.max_retries,
            on_status=on_status,
        )
        live_table.update(new_record)
        for i, r in enumerate(records):
            if r.spec.index == idx:
                records[i] = new_record
        if new_record.ir is not None:
            project.slides = [r.ir for r in records if r.ir is not None]

    async def _maybe_capture_memory(self, project: DeckProject) -> None:
        save = await Wizard.confirm(
            "Save any generation tweaks as a preference?",
            default=False,
        )
        if not save:
            return
        try:
            mem = MarkdownMemory(config={"memory_dir": "~/.config/pptx-agent/memory"})
            mem.capture(
                category="decisions",
                rule=f"deck {project.slug}: {len(project.slides)} slides generated",
                reason="confirmed at slides stage",
            )
        except Exception:
            pass

    @staticmethod
    def _print_summary(console: Any, live_table: LiveStatusTable, records: list[SlideRunRecord]) -> None:
        table = live_table.render()
        if table is not None:
            console.print(table)
        summary = live_table.summary()
        console.print(
            f"Generated {sum(summary.values())} slides: "
            f"[green]{summary['ok']} ok[/green], "
            f"[yellow]{summary['fallback']} fallback[/yellow], "
            f"[red]{summary['failed']} failed[/red]"
        )
