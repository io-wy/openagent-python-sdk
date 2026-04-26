"""Every OpenAgentsError subclass must declare a unique dotted code and the correct retryable classification."""

from __future__ import annotations

import inspect
import re

import openagents.errors.exceptions as errors_mod
from openagents.errors.exceptions import OpenAgentsError

DOTTED = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

EXPECTED = {
    "OpenAgentsError": ("openagents.error", False),
    "ConfigError": ("config.error", False),
    "ConfigLoadError": ("config.load", False),
    "ConfigValidationError": ("config.validation", False),
    "PluginError": ("plugin.error", False),
    "PluginLoadError": ("plugin.load", False),
    "PluginConfigError": ("plugin.config", False),
    "ExecutionError": ("execution.error", False),
    "MaxStepsExceeded": ("execution.max_steps", False),
    "BudgetExhausted": ("execution.budget_exhausted", False),
    "OutputValidationError": ("execution.output_validation", False),
    "SessionError": ("session.error", False),
    "PatternError": ("pattern.error", False),
    "ToolError": ("tool.error", False),
    "RetryableToolError": ("tool.retryable", True),
    "PermanentToolError": ("tool.permanent", False),
    "ToolTimeoutError": ("tool.timeout", True),
    "ToolNotFoundError": ("tool.not_found", False),
    "ToolValidationError": ("tool.validation", False),
    "ToolAuthError": ("tool.auth", False),
    "ToolRateLimitError": ("tool.rate_limit", True),
    "ToolUnavailableError": ("tool.unavailable", True),
    "ToolCancelledError": ("tool.cancelled", False),
    "LLMError": ("llm.error", False),
    "LLMConnectionError": ("llm.connection", True),
    "LLMRateLimitError": ("llm.rate_limit", True),
    "LLMResponseError": ("llm.response", False),
    "ModelRetryError": ("llm.model_retry", False),
    "UserError": ("user.error", False),
    "InvalidInputError": ("user.invalid_input", False),
    "AgentNotFoundError": ("user.agent_not_found", False),
}


def _all_openagents_subclasses() -> list[type[OpenAgentsError]]:
    seen_ids: set[int] = set()
    result = []
    for _, cls in inspect.getmembers(errors_mod, inspect.isclass):
        if issubclass(cls, OpenAgentsError) and cls.__module__ == errors_mod.__name__:
            if id(cls) not in seen_ids:
                seen_ids.add(id(cls))
                result.append(cls)
    return result


def test_every_subclass_has_a_dotted_code():
    for cls in _all_openagents_subclasses():
        assert DOTTED.match(cls.code), f"{cls.__name__}.code '{cls.code}' is not dotted"


def test_codes_are_globally_unique():
    seen: dict[str, str] = {}
    for cls in _all_openagents_subclasses():
        assert cls.code not in seen, f"{cls.__name__} reuses code '{cls.code}' (also on {seen[cls.code]})"
        seen[cls.code] = cls.__name__


def test_codes_and_retryable_match_spec_table():
    for cls in _all_openagents_subclasses():
        expected = EXPECTED.get(cls.__name__)
        assert expected is not None, f"Unexpected exception class {cls.__name__} not in EXPECTED table"
        want_code, want_retryable = expected
        assert cls.code == want_code, f"{cls.__name__}.code {cls.code!r} != {want_code!r}"
        assert cls.retryable is want_retryable, f"{cls.__name__}.retryable {cls.retryable} != {want_retryable}"


def test_no_stale_entries_in_expected_table():
    """EXPECTED is a closed set — remove rows when classes are deleted."""
    live_names = {cls.__name__ for cls in _all_openagents_subclasses()}
    stale = set(EXPECTED) - live_names
    assert not stale, f"EXPECTED table has stale entries not in exceptions.py: {sorted(stale)}"


def test_every_subclass_explicitly_declares_classvars():
    """'Don't rely on inheritance for code' — enforce via __dict__ presence."""
    for cls in _all_openagents_subclasses():
        assert "code" in cls.__dict__, f"{cls.__name__} does not declare code explicitly"
        assert "retryable" in cls.__dict__, f"{cls.__name__} does not declare retryable explicitly"
