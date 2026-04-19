"""Tests for the shared helpers under ``openagents.cli``:

* ``_rich.get_console`` — Rich when available, plain-text stub otherwise.
* ``_events.EventFormatter`` / ``format_event`` — deterministic output
  shape across all event kinds + turn slicing.
* ``_fallback.require_or_hint`` — import helper with one-shot stderr hint.
* ``_exit`` — stable exit-code constants.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from openagents.cli import _fallback
from openagents.cli._events import (
    EVENT_SCHEMA_VERSION,
    EventFormatter,
    default_excludes,
    event_to_jsonl_dict,
    format_event,
    iter_turns,
    matches_any,
)
from openagents.cli._exit import (
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USAGE,
    EXIT_VALIDATION,
)
from openagents.cli._rich import _PlainConsole, get_console


# --------------------------------------------------------------------- _exit

def test_exit_codes_have_stable_values():
    # Contract tested by downstream subcommand tests; lock the numbers.
    assert (EXIT_OK, EXIT_USAGE, EXIT_VALIDATION, EXIT_RUNTIME) == (0, 1, 2, 3)


# --------------------------------------------------------------------- _rich

def test_plain_console_writes_to_given_stream():
    buf = io.StringIO()
    c = _PlainConsole(buf)
    c.print("hello", "world")
    c.rule("section")
    out = buf.getvalue()
    assert "hello world" in out
    assert "section" in out


def test_plain_console_coerces_rich_like_objects():
    buf = io.StringIO()
    c = _PlainConsole(buf)

    class _FakeText:
        plain = "coerced"

    c.print(_FakeText())
    assert "coerced" in buf.getvalue()


def test_get_console_returns_plain_stub_when_rich_missing(monkeypatch):
    monkeypatch.setattr("openagents.cli._rich._rich_available", lambda: False)
    c = get_console("stdout")
    assert isinstance(c, _PlainConsole)
    assert c.file is sys.stdout


def test_get_console_returns_rich_when_available():
    # Rich is part of the dev extras, so this path should be exercised.
    pytest.importorskip("rich")
    c = get_console("stderr")
    # Rich console exposes .print / .rule like the stub; don't couple to class identity.
    assert hasattr(c, "print")
    assert hasattr(c, "rule")


# ------------------------------------------------------------------- _events

def test_default_excludes_returns_fresh_copy():
    a = default_excludes()
    b = default_excludes()
    assert a == b
    a.append("foo")
    assert "foo" not in default_excludes()


def test_matches_any_respects_fnmatch():
    assert matches_any("memory.injected", ["memory.*"])
    assert not matches_any("tool.called", ["memory.*"])


def test_event_formatter_plain_renders_every_event_kind():
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console, show_details=True)
    fmt.render("tool.called", {"tool_id": "t1", "params": {"query": "hello"}})
    fmt.render("tool.succeeded", {"tool_id": "t1", "result": {"results": []}})
    fmt.render("tool.failed", {"tool_id": "t1", "error": "boom"})
    fmt.render("llm.called", {"model": "m1"})
    fmt.render("llm.succeeded", {"model": "m1"})
    fmt.render("custom.event", {"k": "v"})
    out = buf.getvalue()
    assert "t1" in out
    assert "boom" in out
    assert "m1" in out
    assert "custom.event" in out


def test_format_event_runs_with_no_timing_state():
    buf = io.StringIO()
    console = _PlainConsole(buf)
    format_event(console, "llm.succeeded", {"model": "m1"})
    # No prior .called so elapsed is omitted, but output should still contain the model name.
    assert "m1" in buf.getvalue()


def test_event_formatter_ignores_render_errors_in_exotic_payload():
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console)
    # Non-str/dict/list primary param — should still render without raising.
    fmt.render("tool.called", {"tool_id": "t", "params": {"query": object()}})
    assert "t" in buf.getvalue()


def test_event_to_jsonl_dict_is_json_serializable():
    blob = event_to_jsonl_dict("tool.called", {"tool_id": "t", "params": {"q": "x"}})
    text = json.dumps(blob)
    parsed = json.loads(text)
    assert parsed["schema"] == EVENT_SCHEMA_VERSION
    assert parsed["name"] == "tool.called"
    assert parsed["payload"]["tool_id"] == "t"


def test_iter_turns_splits_on_run_started():
    events = [
        {"name": "memory.injected"},
        {"name": "run.started", "turn": 1},
        {"name": "tool.called"},
        {"name": "run.started", "turn": 2},
        {"name": "llm.called"},
    ]
    turns = list(iter_turns(events))
    assert len(turns) == 3
    assert turns[0][0]["name"] == "memory.injected"
    assert any(ev.get("turn") == 1 for ev in turns[1])
    assert any(ev.get("turn") == 2 for ev in turns[2])


def test_iter_turns_preserves_single_bucket_when_no_run_started():
    events = [{"name": "tool.called"}, {"name": "tool.succeeded"}]
    turns = list(iter_turns(events))
    assert len(turns) == 1
    assert len(turns[0]) == 2


# ---------------------------------------------------------------- _fallback

def test_require_or_hint_returns_module_when_present():
    _fallback.reset_hint_state()
    mod = _fallback.require_or_hint("json")
    assert mod is not None
    assert mod.__name__ == "json"


def test_require_or_hint_emits_hint_only_once(capsys):
    _fallback.reset_hint_state()
    first = _fallback.require_or_hint("definitely_not_a_module_xyz")
    second = _fallback.require_or_hint("also_not_a_module_abc")
    assert first is None
    assert second is None
    err = capsys.readouterr().err
    assert err.count("io-openagent-sdk[cli]") == 1


def test_reset_hint_state_allows_second_emission(capsys):
    _fallback.reset_hint_state()
    _fallback.require_or_hint("nope_first_xyz")
    capsys.readouterr()  # drain
    _fallback.reset_hint_state()
    _fallback.require_or_hint("nope_second_abc")
    err = capsys.readouterr().err
    assert "io-openagent-sdk[cli]" in err


# ----------------------------------------------------- _rich coverage gap

def test_plain_console_coerce_handles_none_and_generic_object():
    # Exercise _coerce's None, renderable-attr, and str-fallback branches.
    from openagents.cli._rich import _coerce

    assert _coerce(None) == ""

    class _WithRenderable:
        renderable = "from-renderable"

    assert _coerce(_WithRenderable()) == "from-renderable"

    class _OpaqueObj:
        def __str__(self) -> str:
            return "fallback"

    assert _coerce(_OpaqueObj()) == "fallback"


def test_plain_console_rule_without_title():
    buf = io.StringIO()
    c = _PlainConsole(buf)
    c.rule()
    assert "----" in buf.getvalue()


# --------------------------------------------------- _events coverage gap

def test_event_formatter_emits_elapsed_ms_for_matched_tool_and_llm_pairs():
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console, show_details=True)
    fmt.render("tool.called", {"tool_id": "t1", "params": {"query": "q", "extra": "e"}})
    fmt.render("tool.succeeded", {"tool_id": "t1", "result": {"k": "v"}})
    fmt.render("tool.called", {"tool_id": "t2", "params": {"query": "q2"}})
    fmt.render("tool.failed", {"tool_id": "t2", "error": "boom"})
    fmt.render("llm.called", {"model": "m1"})
    fmt.render("llm.succeeded", {"model": "m1"})
    out = buf.getvalue()
    # Elapsed annotations (" ms") appear on succeeded/failed.
    assert out.count(" ms") >= 3


def test_event_formatter_show_details_false_suppresses_extras():
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console, show_details=False)
    fmt.render("tool.called", {"tool_id": "t", "params": {"query": "q", "extra": "e"}})
    out = buf.getvalue()
    # With show_details=False, the "k=v" extras string is suppressed.
    assert "extra=" not in out


def test_event_formatter_generic_event_with_no_payload():
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console, show_details=True)
    fmt.render("tick", {})  # empty payload path
    assert "tick" in buf.getvalue()


def test_event_formatter_summarize_result_variants():
    # Exercise _summarize_result over None / list / long-string / big-dict.
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console)
    fmt.render("tool.succeeded", {"tool_id": "t", "result": None})
    fmt.render("tool.succeeded", {"tool_id": "t2", "result": [1, 2, 3]})
    fmt.render("tool.succeeded", {"tool_id": "t3", "result": "x" * 200})
    fmt.render(
        "tool.succeeded",
        {"tool_id": "t4", "result": {f"k{i}": i for i in range(6)}},
    )
    out = buf.getvalue()
    assert "[3 items]" in out
    # Long string was truncated; the sentinel "…" appears.
    assert "…" in out


def test_event_formatter_short_helper_reaches_dict_list_and_non_str():
    # Exercises _short's dict / list / non-str branches via show_details extras.
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console, show_details=True)
    fmt.render(
        "tool.called",
        {
            "tool_id": "t",
            "params": {
                "query": "primary",
                "num": 42,
                "lst": [1, 2, 3],
                "dct": {"a": 1},
            },
        },
    )
    out = buf.getvalue()
    assert "num=42" in out
    assert "list[3]" in out
    assert "dict[1]" in out


def test_event_formatter_pick_primary_falls_back_to_first_key():
    # No "preferred" key present → _pick_primary_param_key returns first.
    buf = io.StringIO()
    console = _PlainConsole(buf)
    fmt = EventFormatter(console)
    fmt.render("tool.called", {"tool_id": "t", "params": {"custom_field": "abc"}})
    assert "abc" in buf.getvalue()


def test_event_formatter_tavily_panel_branch_when_rich_available():
    pytest.importorskip("rich")
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, width=200)
    fmt = EventFormatter(console)
    fmt.render(
        "tool.succeeded",
        {
            "tool_id": "tavily",
            "result": {
                "results": [
                    {"title": "hello", "url": "https://example.com", "content": "body"},
                    "not-a-dict-skipped",
                    {"title": "world", "url": "https://example.org", "snippet": "s2"},
                ]
            },
        },
    )
    out = buf.getvalue()
    assert "hello" in out
    assert "example.com" in out


def test_event_formatter_tavily_panel_handles_more_than_five_results():
    pytest.importorskip("rich")
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, width=200)
    fmt = EventFormatter(console)
    fmt.render(
        "tool.succeeded",
        {
            "tool_id": "tavily",
            "result": {
                "results": [
                    {"title": f"t{i}", "url": f"https://u{i}", "content": "c"}
                    for i in range(7)
                ]
            },
        },
    )
    # Only first 5 are rendered into the table; the other 2 are dropped silently.
    assert "t0" in buf.getvalue()
    assert "t4" in buf.getvalue()


def test_try_rich_text_returns_none_when_rich_missing(monkeypatch):
    # Simulate rich being unimportable for the _try_rich_text branch.
    import builtins

    real_import = builtins.__import__

    def _no_rich_text(name, *args, **kwargs):
        if name == "rich.text":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_rich_text)
    from openagents.cli._events import _try_rich_text

    assert _try_rich_text() is None


def test_try_render_tavily_panel_returns_none_without_rich(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_rich(name, *args, **kwargs):
        if name in ("rich.panel", "rich.table", "rich.text"):
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_rich)
    from openagents.cli._events import _try_render_tavily_panel

    result = _try_render_tavily_panel(
        "header", {"results": [{"title": "a", "url": "u"}]}
    )
    assert result is None


def test_append_is_noop_on_plain_string():
    from openagents.cli._events import _append

    # No attribute error when ``line`` is a bare string (no .append).
    _append("plain", "more")  # must not raise
