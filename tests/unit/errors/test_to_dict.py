from __future__ import annotations

from openagents.errors.exceptions import (
    OpenAgentsError,
    PluginLoadError,
    ToolTimeoutError,
)


def test_to_dict_basic_fields():
    exc = PluginLoadError(
        "could not import xyz",
        hint="check PYTHONPATH",
        docs_url="docs/plugin-development.md",
        agent_id="assistant",
        session_id="s1",
        run_id="r1",
    )
    data = exc.to_dict()
    assert data["code"] == "plugin.load"
    assert data["message"] == "could not import xyz"
    assert data["hint"] == "check PYTHONPATH"
    assert data["docs_url"] == "docs/plugin-development.md"
    assert data["retryable"] is False
    assert data["context"]["agent_id"] == "assistant"
    assert data["context"]["session_id"] == "s1"
    assert data["context"]["run_id"] == "r1"


def test_to_dict_retryable_flag_is_class_level():
    exc = ToolTimeoutError("slow", tool_name="search")
    data = exc.to_dict()
    assert data["retryable"] is True
    assert data["code"] == "tool.timeout"
    assert data["context"]["tool_id"] == "search"


def test_to_dict_does_not_include_cause_key():
    """to_dict() owns field serialization only; cause chain is ErrorDetails.from_exception's job."""
    exc = OpenAgentsError("boom")
    assert "cause" not in exc.to_dict()


def test_message_strips_hint_and_docs_tail_lines():
    exc = OpenAgentsError("headline", hint="do X", docs_url="url")
    # str(exc) includes hint/docs tail lines; to_dict().message is just the first line.
    assert "\n" in str(exc)
    assert exc.to_dict()["message"] == "headline"
