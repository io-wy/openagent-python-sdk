"""Shared pretty-printing helpers for CLI commands that surface runtime events.

These helpers are consumed by the future ``run``, ``chat``, and ``replay``
subcommands and also by the pptx example's ``PrettyEventBus``. The goal
is one canonical formatter so a transcript looks identical regardless of
whether it came from a live run, an interactive chat turn, or a replay.

The API is intentionally small and stateless-per-event:

* :func:`format_event(console, name, payload)` — render a single event
  (tool-called / tool-succeeded / tool-failed / llm-called /
  llm-succeeded / generic) to *console*.

* :func:`iter_turns(events)` — group a flat list of events into
  per-turn boundaries so ``replay --turn N`` can slice.

The module intentionally has no dependency on any plugin base class so
it can be imported from non-plugin code paths.

JSONL event-stream schema is versioned by :data:`EVENT_SCHEMA_VERSION`;
downstream parsers should assert on additive-only changes to the shape
emitted by :func:`event_to_jsonl_dict`.
"""

from __future__ import annotations

import fnmatch
import time
from typing import Any, Iterable, Iterator

EVENT_SCHEMA_VERSION = 1
"""Version of the JSONL event shape produced by ``openagents run --format
events`` and consumed by ``openagents replay``. Incremented when the
wire shape changes in a backwards-incompatible way; additive changes
(new optional fields) do NOT bump this number."""

_DEFAULT_EXCLUDES: list[str] = [
    "memory.*",
    "context.*",
    "session.*",
    "run.*",
    "usage.*",
]

_TOOL_ICON = "🔧"
_TOOL_OK_ICON = "✓"
_TOOL_FAIL_ICON = "✗"
_LLM_ICON = "🧠"
_LLM_FAIL_ICON = "✗"


def default_excludes() -> list[str]:
    """Return a fresh copy of the default noise-suppression pattern list."""
    return list(_DEFAULT_EXCLUDES)


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    """Return True if *name* matches any ``fnmatch``-style pattern."""
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


class EventFormatter:
    """Stateful helper that tracks per-tool / per-LLM timing across events.

    Separate from a plugin so it can be used in pure-Python contexts
    (``replay``, subprocess tests). ``console`` must expose a
    ``.print(obj)`` method — :class:`rich.console.Console` or the
    ``_rich._PlainConsole`` stub both satisfy this.
    """

    def __init__(self, console: Any, *, show_details: bool = True):
        self._console = console
        self._show_details = show_details
        self._tool_start_ns: dict[str, int] = {}
        self._llm_start_ns: dict[str, int] = {}

    def render(self, name: str, payload: dict[str, Any]) -> None:
        if name == "tool.called":
            self._render_tool_called(payload)
        elif name == "tool.succeeded":
            self._render_tool_succeeded(payload)
        elif name == "tool.failed":
            self._render_tool_failed(payload)
        elif name == "tool.batch.started":
            self._render_generic(name, payload)
        elif name == "tool.batch.completed":
            self._render_generic(name, payload)
        elif name == "tool.approval_needed":
            self._render_generic(name, payload)
        elif name == "tool.cancelled":
            self._render_generic(name, payload)
        elif name == "tool.background.submitted":
            self._render_generic(name, payload)
        elif name == "tool.background.polled":
            self._render_generic(name, payload)
        elif name == "tool.background.completed":
            self._render_generic(name, payload)
        elif name == "llm.called":
            self._render_llm_called(payload)
        elif name == "llm.succeeded":
            self._render_llm_succeeded(payload)
        elif name == "llm.failed":
            self._render_llm_failed(payload)
        else:
            self._render_generic(name, payload)

    # --------------------------------------------------------- tool events
    def _render_tool_called(self, payload: dict[str, Any]) -> None:
        tool_id = str(payload.get("tool_id") or "?")
        params = payload.get("params") or {}
        self._tool_start_ns[tool_id] = time.monotonic_ns()
        line = self._build_line(prefix=f"{_TOOL_ICON} ", prefix_style="bold cyan", text=tool_id, text_style="bold")
        if params:
            primary_key = _pick_primary_param_key(params)
            if primary_key is not None:
                _append(line, "  ")
                _append(line, str(params[primary_key])[:120], style="yellow")
            extra = {k: v for k, v in params.items() if k != primary_key}
            if extra and self._show_details:
                _append(
                    line,
                    "  " + ", ".join(f"{k}={_short(v)}" for k, v in extra.items()),
                    style="dim",
                )
        self._console.print(line)

    def _render_tool_succeeded(self, payload: dict[str, Any]) -> None:
        tool_id = str(payload.get("tool_id") or "?")
        result = payload.get("result")
        elapsed_ms = _pop_elapsed(self._tool_start_ns, tool_id)
        header = self._build_line(
            prefix=f"{_TOOL_OK_ICON} ",
            prefix_style="bold green",
            text=tool_id,
            text_style="bold",
        )
        if elapsed_ms is not None:
            _append(header, f"  {elapsed_ms} ms", style="dim")

        panel = _try_render_tavily_panel(header, result)
        if panel is not None:
            self._console.print(panel)
            return

        summary = _summarize_result(result)
        if summary:
            _append(header, "  ")
            _append(header, summary, style="dim")
        self._console.print(header)

    def _render_tool_failed(self, payload: dict[str, Any]) -> None:
        tool_id = str(payload.get("tool_id") or "?")
        err = str(payload.get("error") or "")
        elapsed_ms = _pop_elapsed(self._tool_start_ns, tool_id)
        line = self._build_line(
            prefix=f"{_TOOL_FAIL_ICON} ",
            prefix_style="bold red",
            text=tool_id,
            text_style="bold red",
        )
        if elapsed_ms is not None:
            _append(line, f"  {elapsed_ms} ms", style="dim")
        if err:
            _append(line, "  ")
            _append(line, err[:200], style="red")
        self._console.print(line)

    # ---------------------------------------------------------- llm events
    def _render_llm_called(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model") or "?")
        self._llm_start_ns[model] = time.monotonic_ns()
        line = self._build_line(
            prefix=f"{_LLM_ICON} ",
            prefix_style="bold magenta",
            text=model,
            text_style="bold",
        )
        _append(line, "  thinking…", style="dim italic")
        self._console.print(line)

    def _render_llm_succeeded(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model") or "?")
        elapsed_ms = _pop_elapsed(self._llm_start_ns, model)
        line = self._build_line(
            prefix=f"{_LLM_ICON} ",
            prefix_style="bold magenta",
            text=model,
            text_style="bold",
        )
        if elapsed_ms is not None:
            _append(line, f"  {elapsed_ms} ms", style="dim")
        self._console.print(line)

    def _render_llm_failed(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model") or "?")
        elapsed_ms = _pop_elapsed(self._llm_start_ns, model)
        err = ""
        metrics = payload.get("_metrics")
        if metrics is not None:
            err = str(getattr(metrics, "error", None) or "")
        if not err:
            err = str(payload.get("error") or "")
        line = self._build_line(
            prefix=f"{_LLM_FAIL_ICON} ",
            prefix_style="bold red",
            text=model,
            text_style="bold red",
        )
        if elapsed_ms is not None:
            _append(line, f"  {elapsed_ms} ms", style="dim")
        if err:
            _append(line, "  ")
            _append(line, err[:200], style="red")
        self._console.print(line)

    # ------------------------------------------------------------- generic
    def _render_generic(self, name: str, payload: dict[str, Any]) -> None:
        line = self._build_line(prefix="·  ", prefix_style="dim", text=name, text_style="bold dim")
        if payload and self._show_details:
            bits = [f"{k}={_short(v)}" for k, v in payload.items()]
            _append(line, "  ")
            _append(line, " ".join(bits)[:200], style="dim")
        self._console.print(line)

    # -------------------------------------------------- Rich / plain glue
    def _build_line(
        self,
        *,
        prefix: str,
        prefix_style: str,
        text: str,
        text_style: str,
    ) -> Any:
        rich_text = _try_rich_text()
        if rich_text is None:
            return f"{prefix}{text}"
        line = rich_text()
        line.append(prefix, style=prefix_style)
        line.append(text, style=text_style)
        return line


def format_event(console: Any, name: str, payload: dict[str, Any]) -> None:
    """Convenience wrapper that constructs a transient formatter.

    Loses cross-event timing. Use :class:`EventFormatter` directly if you
    need elapsed-ms annotations on ``*.succeeded`` / ``*.failed`` events.
    """
    EventFormatter(console).render(name, payload)


def event_to_jsonl_dict(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize an event into the stable JSONL shape.

    Schema version is attached so downstream parsers can assert on
    compatibility. Unknown fields on the payload pass through untouched.
    """
    return {
        "schema": EVENT_SCHEMA_VERSION,
        "name": name,
        "payload": dict(payload),
    }


def iter_turns(events: Iterable[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    """Group a flat event list into per-turn chunks.

    A "turn" boundary is any event named ``run.started``; events before
    the first ``run.started`` (if any) form an implicit prelude turn so
    no event is silently dropped.
    """
    bucket: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("name") == "run.started" and bucket:
            yield bucket
            bucket = []
        bucket.append(ev)
    if bucket:
        yield bucket


# --------------------------------------------------------------------- utils


def _pick_primary_param_key(params: dict[str, Any]) -> str | None:
    for preferred in ("query", "command", "path", "url", "rule", "input_text"):
        if preferred in params:
            return preferred
    return next(iter(params), None)


def _short(value: Any) -> str:
    if isinstance(value, str):
        return value if len(value) <= 40 else value[:37] + "…"
    if isinstance(value, (dict, list)):
        return f"{type(value).__name__}[{len(value)}]"
    return str(value)


def _summarize_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, dict):
        keys = list(result.keys())[:4]
        return "{" + ", ".join(keys) + ("…}" if len(result) > 4 else "}")
    if isinstance(result, list):
        return f"[{len(result)} items]"
    s = str(result)
    return s if len(s) <= 80 else s[:77] + "…"


def _pop_elapsed(store: dict[str, int], key: str) -> int | None:
    start = store.pop(key, None)
    if start is None:
        return None
    return (time.monotonic_ns() - start) // 1_000_000


def _try_rich_text() -> Any:
    try:
        from rich.text import Text
    except ImportError:
        return None
    return Text


def _try_render_tavily_panel(header: Any, result: Any) -> Any:
    if not (isinstance(result, dict) and isinstance(result.get("results"), list) and result["results"]):
        return None
    try:
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        return None

    table = Table(show_header=True, header_style="bold", expand=False, pad_edge=False)
    table.add_column("#", width=3, style="dim")
    table.add_column("Title", overflow="ellipsis", max_width=42)
    table.add_column("URL", overflow="ellipsis", max_width=48, style="blue")
    table.add_column("Snippet", overflow="ellipsis", max_width=60, style="dim")
    for i, item in enumerate(result["results"][:5], start=1):
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(i),
            str(item.get("title") or ""),
            str(item.get("url") or ""),
            str(item.get("content") or item.get("snippet") or "")[:120],
        )
    title_renderable: Any = header if isinstance(header, Text) else Text(str(header))
    return Panel(table, title=title_renderable, title_align="left", border_style="green")


def _append(line: Any, text: str, *, style: str | None = None) -> None:
    """Append to a Rich ``Text`` if available; otherwise no-op on str."""
    append = getattr(line, "append", None)
    if append is None:
        return
    if style is None:
        append(text)
    else:
        append(text, style=style)
