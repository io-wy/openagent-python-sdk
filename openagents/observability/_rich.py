"""Internal rich helpers.

All `rich` imports live here behind import-time guards. Public callers
(configure(), RichConsoleEventBus) use the factories exposed here and
receive RichNotInstalledError if rich is missing.
"""

from __future__ import annotations

from typing import Any, Literal

from openagents.observability.errors import RichNotInstalledError


def _require_rich() -> Any:
    try:
        import rich  # noqa: F401
    except ImportError as exc:
        raise RichNotInstalledError() from exc
    return rich


def make_console(stream: Literal["stdout", "stderr"] = "stderr") -> Any:
    """Return a rich.console.Console writing to the requested stream."""
    _require_rich()
    import sys

    from rich.console import Console

    target = sys.stderr if stream == "stderr" else sys.stdout
    return Console(file=target, force_terminal=None, highlight=False)


def make_rich_handler(*, stream: Literal["stdout", "stderr"], show_time: bool, show_path: bool) -> Any:
    """Return a configured rich.logging.RichHandler."""
    _require_rich()
    from rich.logging import RichHandler

    console = make_console(stream)
    handler = RichHandler(
        console=console,
        show_time=show_time,
        show_level=True,
        show_path=show_path,
        rich_tracebacks=True,
        markup=False,
    )
    handler._openagents_installed = True  # type: ignore[attr-defined]
    return handler


def render_event_row(event: Any, *, show_payload: bool) -> Any:
    """Render a RuntimeEvent into a rich Renderable.

    - show_payload=False: single-line Text "ts  name  key=val ..."
    - show_payload=True: Panel with per-field rows
    """
    _require_rich()
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    name = event.name
    payload = event.payload or {}

    if not show_payload:
        line = Text()
        line.append(f"{name}  ", style="bold")
        for i, (k, v) in enumerate(payload.items()):
            if i > 0:
                line.append(" ")
            line.append(f"{k}=")
            line.append(repr(v))
        return line

    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style="bold")
    table.add_column()
    for k, v in payload.items():
        table.add_row(f"{k} =", repr(v))
    return Panel(table, title=name, title_align="left")
