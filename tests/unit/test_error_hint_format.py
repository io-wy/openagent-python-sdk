"""WP1: hint / docs_url plumbing on OpenAgentsError and key subclasses."""

from __future__ import annotations

import pytest

from openagents.errors.exceptions import (
    BudgetExhausted,
    ModelRetryError,
    OpenAgentsError,
    OutputValidationError,
    PluginLoadError,
    ToolError,
)


def test_str_no_hint_or_docs_is_single_line():
    err = OpenAgentsError("plain message")
    assert str(err) == "plain message"


def test_str_with_hint_appends_indented_line():
    err = OpenAgentsError("bad config", hint="run with --check")
    assert str(err) == "bad config\n  hint: run with --check"


def test_str_with_docs_url_appends_indented_line():
    err = OpenAgentsError("see manual", docs_url="https://example.com/x")
    assert str(err) == "see manual\n  docs: https://example.com/x"


def test_str_with_hint_and_docs_includes_both():
    err = OpenAgentsError("nope", hint="try foo", docs_url="https://e.com/y")
    text = str(err)
    assert text.startswith("nope")
    assert "  hint: try foo" in text
    assert "  docs: https://e.com/y" in text


def test_attributes_are_accessible():
    err = OpenAgentsError("x", hint="y", docs_url="z")
    assert err.hint == "y"
    assert err.docs_url == "z"


def test_default_hint_and_docs_url_are_none():
    err = OpenAgentsError("x")
    assert err.hint is None
    assert err.docs_url is None


def test_plugin_load_error_inherits_hint_kwarg():
    err = PluginLoadError("unknown plugin", hint="Did you mean 'buffer'?")
    assert err.hint == "Did you mean 'buffer'?"
    assert "hint: Did you mean 'buffer'?" in str(err)


def test_budget_exhausted_threads_hint():
    err = BudgetExhausted("over budget", kind="cost", current=1.0, limit=0.5, hint="raise the cost cap")
    assert err.hint == "raise the cost cap"
    assert err.kind == "cost"
    assert "hint: raise the cost cap" in str(err)


def test_output_validation_error_threads_hint_and_docs():
    err = OutputValidationError(
        "schema mismatch",
        attempts=3,
        hint="check Pydantic model",
        docs_url="https://docs.example/output",
    )
    assert err.hint == "check Pydantic model"
    assert err.docs_url == "https://docs.example/output"
    assert err.attempts == 3


def test_model_retry_error_threads_hint():
    err = ModelRetryError("retry", hint="LLM should rephrase")
    assert err.hint == "LLM should rephrase"


def test_tool_error_threads_hint():
    err = ToolError("oops", "calc", hint="check operands")
    assert err.hint == "check operands"
    assert err.tool_name == "calc"
    assert err.tool_id == "calc"


def test_with_context_preserves_hint_and_docs():
    err = OpenAgentsError("base", hint="h", docs_url="d").with_context(agent_id="a")
    assert err.agent_id == "a"
    assert err.hint == "h"
    assert err.docs_url == "d"


def test_first_line_unchanged_for_log_scrapers():
    """str(exc) without hint must be identical to the message - back-compat."""
    err = OpenAgentsError("the message")
    first_line = str(err).split("\n", 1)[0]
    assert first_line == "the message"
