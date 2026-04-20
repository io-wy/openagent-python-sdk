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
    return Console(file=target, force_terminal=True, highlight=False)


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


_MAX_STR_LEN = 4000


def _render_value(v: Any, depth: int = 0) -> Any:
    """Recursively render a payload value as a Rich renderable."""
    from rich.table import Table
    from rich.text import Text

    if isinstance(v, dict):
        if not v:
            return Text("{}", style="dim")
        tbl = Table.grid(padding=(0, 1))
        tbl.add_column(justify="right", style="cyan", no_wrap=True)
        tbl.add_column()
        for dk, dv in v.items():
            tbl.add_row(f"{dk}:", _render_value(dv, depth + 1))
        return tbl
    if isinstance(v, list):
        if not v:
            return Text("[]", style="dim")
        tbl = Table.grid(padding=(0, 0))
        tbl.add_column()
        for item in v:
            tbl.add_row(_render_value(item, depth + 1))
        return tbl
    if isinstance(v, str):
        display = v if len(v) <= _MAX_STR_LEN else v[:_MAX_STR_LEN] + f"\n… [{len(v) - _MAX_STR_LEN} chars truncated]"
        if "\n" in display:
            from rich.markdown import Markdown

            return Markdown(display)
        return Text(display)
    return Text(repr(v), style="dim")


def render_event_row(event: Any, *, show_payload: bool) -> Any:
    """Render a RuntimeEvent into a rich Renderable.

    - show_payload=False: single-line Text "ts  name  key=val ..."
    - show_payload=True: Panel with expanded per-field rows; dicts and
      lists are recursively expanded, strings rendered without repr quotes.
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
            line.append(repr(v) if not isinstance(v, str) else v[:120])
        return line

    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style="bold", no_wrap=True)
    table.add_column()
    for k, v in payload.items():
        table.add_row(f"{k} =", _render_value(v))
    return Panel(table, title=name, title_align="left")
