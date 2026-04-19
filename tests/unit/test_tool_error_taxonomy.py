"""Tests for the 5 new ToolError subclasses introduced in the tool-invocation enhancement."""

from __future__ import annotations

import pytest

from openagents.errors.exceptions import (
    PermanentToolError,
    RetryableToolError,
    ToolAuthError,
    ToolCancelledError,
    ToolError,
    ToolRateLimitError,
    ToolUnavailableError,
    ToolValidationError,
)


@pytest.mark.parametrize(
    "cls,expected_parent",
    [
        (ToolValidationError, PermanentToolError),
        (ToolAuthError, PermanentToolError),
        (ToolCancelledError, PermanentToolError),
        (ToolRateLimitError, RetryableToolError),
        (ToolUnavailableError, RetryableToolError),
    ],
)
def test_new_tool_errors_have_correct_parent(cls, expected_parent):
    exc = cls("oops", tool_name="mytool")
    assert isinstance(exc, expected_parent)
    assert isinstance(exc, ToolError)
    assert exc.tool_name == "mytool"


def test_tool_validation_error_is_permanent_not_retryable():
    assert issubclass(ToolValidationError, PermanentToolError)
    assert not issubclass(ToolValidationError, RetryableToolError)


def test_tool_rate_limit_error_is_retryable():
    assert issubclass(ToolRateLimitError, RetryableToolError)


def test_tool_cancelled_error_str_includes_hint():
    exc = ToolCancelledError("run cancelled", tool_name="x", hint="retry later")
    assert "retry later" in str(exc)
