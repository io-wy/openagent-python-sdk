"""Shared Rich Layout chrome for the pptx-agent wizard."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

try:
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover - rich is a required extra
    Layout = None  # type: ignore[assignment,misc]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]


STAGES: list[tuple[str, str]] = [
    ("intent", "意图"),
    ("env", "环境"),
    ("research", "研究"),
    ("outline", "大纲"),
    ("theme", "主题"),
    ("slides", "切片"),
    ("compile", "编译QA"),
]


@dataclass
class LogRing:
    """Tail-ring that keeps only the last ``max_lines`` log lines."""

    max_lines: int = 5
    _lines: deque[str] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self._lines = deque(maxlen=self.max_lines)

    def append(self, line: str) -> None:
        self._lines.append(line)

    def snapshot(self) -> list[str]:
        return list(self._lines)

    def clear(self) -> None:
        self._lines.clear()


def _sidebar_glyph(project_stage: str, row_stage: str) -> str:
    stages = [s for s, _ in STAGES] + ["done"]
    try:
        project_idx = stages.index(project_stage)
        row_idx = stages.index(row_stage)
    except ValueError:
        return "○"
    if row_idx < project_idx:
        return "✓"
    if row_idx == project_idx and project_stage != "done":
        return "▶"
    return "○"


def _sidebar_stage_number(project_stage: str) -> int:
    stages = [s for s, _ in STAGES] + ["done"]
    try:
        idx = stages.index(project_stage)
    except ValueError:
        return 1
    if project_stage == "done":
        return len(STAGES)
    return min(idx + 1, len(STAGES))


def _format_elapsed(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02d}:{secs:02d}"


@dataclass
class LayoutRenderer:
    """Render the wizard's four-region Rich layout from current project state."""

    project: Any
    log: LogRing = field(default_factory=lambda: LogRing(max_lines=5))
    started_at: float = field(default_factory=time.monotonic)
    main_panel: Any = None

    def sidebar_entries(self) -> list[str]:
        lines: list[str] = []
        for i, (stage_key, label) in enumerate(STAGES, start=1):
            glyph = _sidebar_glyph(self.project.stage, stage_key)
            lines.append(f"{glyph} {i} {label}")
        return lines

    def status_bar_text(self) -> str:
        n = _sidebar_stage_number(self.project.stage)
        elapsed = _format_elapsed(time.monotonic() - self.started_at)
        total = len(STAGES)
        return f"pptx-agent · {self.project.slug} · stage {n}/{total} · {elapsed}"

    def set_main(self, renderable: Any) -> None:
        self.main_panel = renderable

    def build(self) -> Any:
        if Layout is None:
            return None
        layout = Layout()
        layout.split_column(
            Layout(name="status", size=3),
            Layout(name="body", ratio=1),
            Layout(name="logs", size=7),
        )
        layout["body"].split_row(
            Layout(name="sidebar", size=26),
            Layout(name="main", ratio=1),
        )
        layout["status"].update(Panel(Text(self.status_bar_text(), style="bold"), border_style="cyan"))
        sidebar_table = Table.grid(padding=(0, 1))
        sidebar_table.add_column()
        for line in self.sidebar_entries():
            sidebar_table.add_row(line)
        layout["sidebar"].update(Panel(sidebar_table, title="Steps", border_style="blue"))
        layout["main"].update(self.main_panel or Panel("", title="Main"))
        tail = self.log.snapshot() or ["(no log entries yet)"]
        layout["logs"].update(Panel("\n".join(tail), title="Log (tail)", border_style="dim"))
        return layout

    def render(self, project: Any = None) -> Any:
        if project is not None:
            self.project = project
        return self.build()


def repaint(console: Any, renderer: Any, project: Any) -> None:
    """Best-effort Layout repaint — no-op when disabled or Rich is missing.

    Stages call this at logical boundaries (stage entry, sub-step progress)
    to surface the current sidebar/status/log-tail state inside the Rich
    Layout without holding an open :class:`rich.live.Live` context across
    `questionary.ask_async()` calls (which corrupts Windows conhost).
    """
    if renderer is None or console is None:
        return
    try:
        rendered = renderer.render(project)
    except Exception:  # pragma: no cover - defensive
        return
    if rendered is None:
        return
    try:
        console.print(rendered)
    except Exception:  # pragma: no cover - defensive
        pass


class RingLogHandler(logging.Handler):
    """Push formatted log records into a :class:`LogRing`.

    The ring trims itself — this handler is a thin formatter bridge so the
    wizard can surface the tail of `logging.getLogger(...)` output inside
    the Rich layout without maintaining its own stderr capture.
    """

    def __init__(self, ring: LogRing, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._ring = ring

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover - defensive
            line = record.getMessage()
        self._ring.append(line)
