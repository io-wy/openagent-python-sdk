# Error Handling Tier 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Tier 1 of the error-handling overhaul: dotted error codes, class-level `retryable` attribute, `ErrorDetails` serialization model replacing `RunResult.error`/`exception`, jitter + `Retry-After` propagation, and error reference docs.

**Architecture:** No new seams. Attach `code: ClassVar[str]` and `retryable: ClassVar[bool]` to every `OpenAgentsError` subclass; add `to_dict()` on the root class; introduce `ErrorDetails` pydantic model on `RunResult`; replace two stringly-typed retry classification mechanisms with attribute reads; propagate `retry_after_ms` from the HTTP transport up to the typed error; add `docs/errors.md` as the single source of truth.

**Tech Stack:** Python 3.10+, pydantic v2, pytest, `uv` for environment.

**Spec:** `docs/superpowers/specs/2026-04-22-error-handling-tier1-design.md`

**Conventions:**
- TDD for every code change: write failing test → run it (verify red) → minimal impl → run it (verify green) → commit.
- Use `rtk git` and `rtk uv` shortcuts per repo convention (falls back to plain `git`/`uv` if unavailable).
- Commit messages follow Conventional Commits (`feat`, `refactor`, `test`, `docs`, `chore`).
- Co-author trailer omitted in commit command blocks for brevity; add the repo's standard trailer if you maintain one.
- Follow @superpowers:test-driven-development and @superpowers:verification-before-completion.

---

## File Structure

| File | Responsibility |
|---|---|
| `openagents/errors/exceptions.py` | Source of truth for `OpenAgentsError` tree, `code`/`retryable` ClassVars, `to_dict()`, `retry_after_ms` fields |
| `openagents/errors/__init__.py` | Re-export `ErrorDetails` alongside existing error classes |
| `openagents/interfaces/runtime.py` | `ErrorDetails` model + modified `RunResult` |
| `openagents/interfaces/diagnostics.py` | `ErrorSnapshot.error_code` field |
| `openagents/interfaces/event_taxonomy.py` | Declare `error_details` / `error_code` payload fields |
| `openagents/plugins/builtin/runtime/default_runtime.py` | Remove `RETRYABLE_RUN_ERRORS`; build `ErrorDetails` in failure branch; emit `error_details` in payloads |
| `openagents/plugins/builtin/tool_executor/retry.py` | Remove `retry_on`/`retry_on_timeout`; jitter; read `exc.retryable` + `exc.retry_after_ms` |
| `openagents/plugins/builtin/diagnostics/{phoenix,langfuse,rich}_plugin.py` | Write `snapshot.error_code` into trace attributes |
| `openagents/llm/providers/_http_base.py` | Thread `retry_after_ms` into `LLMRateLimitError` on budget exhaustion |
| `openagents/llm/providers/litellm_client.py` | Best-effort read of LiteLLM exception `retry_after` |
| `docs/errors.md` | Chinese error reference manual (primary) |
| `docs/errors.en.md` | English mirror |
| `docs/migration-0.3-to-0.4.md` | Breaking change migration guide |
| `docs/developer-guide.md` / `.en.md` | Link to errors manual + migration guide |
| `tests/unit/errors/test_codes.py` | New — code/retryable coverage |
| `tests/unit/errors/test_to_dict.py` | New — to_dict serialization coverage |
| `tests/unit/errors/test_retry_after.py` | New — retry_after_ms field coverage |
| `tests/unit/interfaces/test_run_result.py` | New — `RunResult.error_details`, `ErrorDetails.from_exception` |
| `tests/unit/runtime/test_error_details_emission.py` | New — runtime failure path emits error_details |
| `tests/unit/runtime/test_durable_resume_retryable_attribute.py` | New — durable resume reads `exc.retryable` |
| `tests/unit/llm/providers/test_retry_after_propagation.py` | New — 429 + `Retry-After` → `retry_after_ms` |
| `tests/unit/docs/test_errors_md_coverage.py` | New — docs/errors.md drift gate (both zh + en) |
| existing test files | Modified — migrate `.error` / `.exception` reads to `.error_details` |

---

## Task 1: Attach `code` and `retryable` ClassVars to every exception

**Files:**
- Modify: `openagents/errors/exceptions.py`
- Test: `tests/unit/errors/test_codes.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/errors/test_codes.py
"""Every OpenAgentsError subclass must declare a unique dotted code and the correct retryable classification."""

from __future__ import annotations

import inspect
import re

import openagents.errors.exceptions as errors_mod
from openagents.errors.exceptions import OpenAgentsError

DOTTED = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

EXPECTED = {
    "OpenAgentsError":        ("openagents.error",          False),
    "ConfigError":            ("config.error",              False),
    "ConfigLoadError":        ("config.load",               False),
    "ConfigValidationError":  ("config.validation",         False),
    "PluginError":            ("plugin.error",              False),
    "PluginLoadError":        ("plugin.load",               False),
    "PluginCapabilityError":  ("plugin.capability",         False),
    "PluginConfigError":      ("plugin.config",             False),
    "ExecutionError":         ("execution.error",           False),
    "MaxStepsExceeded":       ("execution.max_steps",       False),
    "BudgetExhausted":        ("execution.budget_exhausted", False),
    "OutputValidationError":  ("execution.output_validation", False),
    "SessionError":           ("session.error",             False),
    "PatternError":           ("pattern.error",             False),
    "ToolError":              ("tool.error",                False),
    "RetryableToolError":     ("tool.retryable",            True),
    "PermanentToolError":     ("tool.permanent",            False),
    "ToolTimeoutError":       ("tool.timeout",              True),
    "ToolNotFoundError":      ("tool.not_found",            False),
    "ToolValidationError":    ("tool.validation",           False),
    "ToolAuthError":          ("tool.auth",                 False),
    "ToolRateLimitError":     ("tool.rate_limit",           True),
    "ToolUnavailableError":   ("tool.unavailable",          True),
    "ToolCancelledError":     ("tool.cancelled",            False),
    "LLMError":               ("llm.error",                 False),
    "LLMConnectionError":     ("llm.connection",            True),
    "LLMRateLimitError":      ("llm.rate_limit",            True),
    "LLMResponseError":       ("llm.response",              False),
    "ModelRetryError":        ("llm.model_retry",           False),
    "UserError":              ("user.error",                False),
    "InvalidInputError":      ("user.invalid_input",        False),
    "AgentNotFoundError":     ("user.agent_not_found",      False),
}


def _all_openagents_subclasses() -> list[type[OpenAgentsError]]:
    return [
        cls
        for _, cls in inspect.getmembers(errors_mod, inspect.isclass)
        if issubclass(cls, OpenAgentsError) and cls.__module__ == errors_mod.__name__
    ]


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/errors/test_codes.py -v`
Expected: FAIL — `code` / `retryable` attributes missing on the base class.

- [ ] **Step 3: Add ClassVars in `openagents/errors/exceptions.py`**

For `OpenAgentsError`, add two lines after the docstring:

```python
class OpenAgentsError(Exception):
    code: ClassVar[str] = "openagents.error"
    retryable: ClassVar[bool] = False
    # ... existing fields unchanged
```

Import `ClassVar` at top: `from typing import Any, ClassVar, Literal, TypeVar`.

For each subclass, add two lines right after `class X(Y):` / docstring (see spec §1.2 table for exact values). Example:

```python
class ToolTimeoutError(RetryableToolError):
    """Raised when a tool execution times out."""
    code: ClassVar[str] = "tool.timeout"
    retryable: ClassVar[bool] = True
```

`RetryableToolError` gets `code="tool.retryable"`, `retryable=True`. `PermanentToolError` gets `code="tool.permanent"`, `retryable=False`. Subclasses override both; don't rely on inheritance for `code`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/errors/test_codes.py -v`
Expected: PASS (all 3 tests green).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/errors/exceptions.py tests/unit/errors/test_codes.py
rtk git commit -m "feat(errors): attach dotted code and retryable ClassVar to every exception"
```

---

## Task 2: Implement `OpenAgentsError.to_dict()`

**Files:**
- Modify: `openagents/errors/exceptions.py`
- Test: `tests/unit/errors/test_to_dict.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/errors/test_to_dict.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/errors/test_to_dict.py -v`
Expected: FAIL — `AttributeError: 'OpenAgentsError' object has no attribute 'to_dict'`.

- [ ] **Step 3: Add `to_dict` on `OpenAgentsError`**

In `openagents/errors/exceptions.py`, inside `OpenAgentsError`:

```python
    def to_dict(self) -> dict[str, Any]:
        """Serialize to a stable shape for HTTP / SSE / trace exporters.

        Cause chain is intentionally excluded — ``ErrorDetails.from_exception``
        owns that recursion so callers cannot get the same walk in two places.
        """
        message = super().__str__() or ""
        return {
            "code": type(self).code,
            "message": message.splitlines()[0] if message else "",
            "hint": self.hint,
            "docs_url": self.docs_url,
            "retryable": type(self).retryable,
            "context": {
                "agent_id": self.agent_id,
                "session_id": self.session_id,
                "run_id": self.run_id,
                "tool_id": self.tool_id,
                "step_number": self.step_number,
            },
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/errors/test_to_dict.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/errors/exceptions.py tests/unit/errors/test_to_dict.py
rtk git commit -m "feat(errors): OpenAgentsError.to_dict for stable cross-process serialization"
```

---

## Task 3: Add `retry_after_ms` to `ToolRateLimitError` and `LLMRateLimitError`

**Files:**
- Modify: `openagents/errors/exceptions.py`
- Test: `tests/unit/errors/test_retry_after.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/errors/test_retry_after.py
from __future__ import annotations

from openagents.errors.exceptions import LLMRateLimitError, ToolRateLimitError


def test_tool_rate_limit_carries_retry_after_ms():
    exc = ToolRateLimitError("slow down", tool_name="api", retry_after_ms=5_000)
    assert exc.retry_after_ms == 5_000
    assert exc.to_dict()["context"]["retry_after_ms"] == 5_000


def test_tool_rate_limit_defaults_none():
    exc = ToolRateLimitError("slow down", tool_name="api")
    assert exc.retry_after_ms is None
    assert exc.to_dict()["context"]["retry_after_ms"] is None


def test_llm_rate_limit_carries_retry_after_ms():
    exc = LLMRateLimitError("429", retry_after_ms=2_500)
    assert exc.retry_after_ms == 2_500
    assert exc.to_dict()["context"]["retry_after_ms"] == 2_500
    assert exc.to_dict()["retryable"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/errors/test_retry_after.py -v`
Expected: FAIL — `retry_after_ms` not a known kwarg; attribute missing.

- [ ] **Step 3: Override `__init__` on both classes and surface the field in `to_dict()`**

In `openagents/errors/exceptions.py`:

```python
class ToolRateLimitError(RetryableToolError):
    """Third-party rate-limited us. Retryable with backoff."""
    code: ClassVar[str] = "tool.rate_limit"
    retryable: ClassVar[bool] = True

    retry_after_ms: int | None

    def __init__(
        self,
        message: str,
        tool_name: str = "",
        *,
        retry_after_ms: int | None = None,
        hint: str | None = None,
        docs_url: str | None = None,
    ) -> None:
        super().__init__(message, tool_name=tool_name, hint=hint, docs_url=docs_url)
        self.retry_after_ms = retry_after_ms

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["context"]["retry_after_ms"] = self.retry_after_ms
        return data


class LLMRateLimitError(LLMError):
    """Raised when a provider rate-limits a request."""
    code: ClassVar[str] = "llm.rate_limit"
    retryable: ClassVar[bool] = True

    retry_after_ms: int | None

    def __init__(
        self,
        message: str = "",
        *,
        retry_after_ms: int | None = None,
        hint: str | None = None,
        docs_url: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        tool_id: str | None = None,
        step_number: int | None = None,
    ) -> None:
        super().__init__(
            message,
            hint=hint,
            docs_url=docs_url,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            tool_id=tool_id,
            step_number=step_number,
        )
        self.retry_after_ms = retry_after_ms

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["context"]["retry_after_ms"] = self.retry_after_ms
        return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/errors/test_retry_after.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/errors/exceptions.py tests/unit/errors/test_retry_after.py
rtk git commit -m "feat(errors): retry_after_ms on ToolRateLimitError and LLMRateLimitError"
```

---

## Task 4: `ErrorDetails` model + `from_exception`

**Files:**
- Modify: `openagents/interfaces/runtime.py`
- Modify: `openagents/errors/__init__.py` (re-export)
- Test: `tests/unit/interfaces/test_run_result.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/interfaces/test_run_result.py
from __future__ import annotations

from openagents.errors import ErrorDetails
from openagents.errors.exceptions import (
    OpenAgentsError,
    PatternError,
    ToolRateLimitError,
)


def test_error_details_from_openagents_error():
    exc = ToolRateLimitError("429", tool_name="api", retry_after_ms=3000, hint="slow down")
    details = ErrorDetails.from_exception(exc)
    assert details.code == "tool.rate_limit"
    assert details.message == "429"
    assert details.hint == "slow down"
    assert details.retryable is True
    assert details.context["retry_after_ms"] == 3000
    assert details.cause is None


def test_error_details_from_non_openagents_error():
    details = ErrorDetails.from_exception(ValueError("bad input"))
    assert details.code == "error.unknown"
    assert details.message == "bad input"
    assert details.retryable is False
    assert details.cause is None


def test_error_details_walks_cause_up_to_three_layers():
    root = ValueError("layer 3")
    mid = PatternError("layer 2")
    mid.__cause__ = root
    top = OpenAgentsError("layer 1")
    top.__cause__ = mid

    details = ErrorDetails.from_exception(top)
    assert details.message == "layer 1"
    assert details.cause is not None
    assert details.cause.code == "pattern.error"
    assert details.cause.cause is not None
    assert details.cause.cause.code == "error.unknown"
    assert details.cause.cause.cause is None  # cut at depth 3


def test_error_details_stops_at_depth_limit():
    deepest = OpenAgentsError("l5")
    l4 = OpenAgentsError("l4"); l4.__cause__ = deepest
    l3 = OpenAgentsError("l3"); l3.__cause__ = l4
    l2 = OpenAgentsError("l2"); l2.__cause__ = l3
    l1 = OpenAgentsError("l1"); l1.__cause__ = l2

    details = ErrorDetails.from_exception(l1)
    # depth 0 = l1, depth 1 = l2, depth 2 = l3, depth 3 = l4; l5 dropped
    assert details.cause.cause.cause.message == "l4"
    assert details.cause.cause.cause.cause is None


def test_error_details_cycle_safe():
    a = OpenAgentsError("a")
    a.__cause__ = a  # self-cycle
    details = ErrorDetails.from_exception(a)
    assert details.cause is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/interfaces/test_run_result.py -v`
Expected: FAIL — `cannot import name 'ErrorDetails' from 'openagents.errors'`.

- [ ] **Step 3: Add `ErrorDetails` model in `openagents/interfaces/runtime.py`**

Insert between `class RunArtifact` and `class RunUsage`:

```python
class ErrorDetails(BaseModel):
    """Stable cross-process serialization of a run-ending error.

    Built by :meth:`from_exception`. Replaces ``RunResult.error`` (str) and
    ``RunResult.exception`` (live object) with a single structured field that
    HTTP / SSE / trace exporters can consume without touching internal SDK types.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: str
    message: str
    hint: str | None = None
    docs_url: str | None = None
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)
    cause: "ErrorDetails | None" = None

    @classmethod
    def from_exception(cls, exc: BaseException, *, _depth: int = 0) -> "ErrorDetails":
        from openagents.errors.exceptions import OpenAgentsError

        _MAX_DEPTH = 3
        if isinstance(exc, OpenAgentsError):
            data = exc.to_dict()
            details = cls(
                code=data["code"],
                message=data["message"],
                hint=data["hint"],
                docs_url=data["docs_url"],
                retryable=data["retryable"],
                context=dict(data["context"]),
            )
        else:
            text = str(exc)
            message = text.splitlines()[0] if text else type(exc).__name__
            details = cls(code="error.unknown", message=message)

        cause = getattr(exc, "__cause__", None)
        if cause is not None and cause is not exc and _depth < _MAX_DEPTH:
            details.cause = cls.from_exception(cause, _depth=_depth + 1)
        return details


ErrorDetails.model_rebuild()
```

In `openagents/errors/__init__.py`, add to imports and `__all__`:

```python
from openagents.interfaces.runtime import ErrorDetails

__all__ = [
    "AgentNotFoundError",
    ...  # existing entries
    "ErrorDetails",
    ...
]
```

Note: `ErrorDetails` lives in `interfaces/runtime.py` (co-located with `RunResult`) but is also re-exported from `openagents.errors` for discoverability. Import in `errors/__init__.py` is bottom-of-file to avoid circular import with `exceptions.py` (which `interfaces/runtime.py` already imports).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/interfaces/test_run_result.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/interfaces/runtime.py openagents/errors/__init__.py tests/unit/interfaces/test_run_result.py
rtk git commit -m "feat(runtime): ErrorDetails model with from_exception cause-chain walker"
```

---

## Task 5: Replace `RunResult.error`/`exception` with `error_details`

**Files:**
- Modify: `openagents/interfaces/runtime.py` (`RunResult` definition)
- Test: `tests/unit/interfaces/test_run_result.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/interfaces/test_run_result.py`:

```python
import pytest

from openagents.interfaces.runtime import RunResult, StopReason


def test_run_result_has_error_details_not_error_or_exception():
    result = RunResult(run_id="r1")
    assert result.error_details is None
    # Breaking: old fields removed outright.
    with pytest.raises(AttributeError):
        _ = result.error  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        _ = result.exception  # type: ignore[attr-defined]


def test_run_result_error_details_roundtrip():
    details = ErrorDetails(code="tool.timeout", message="slow", retryable=True)
    result = RunResult(run_id="r1", stop_reason=StopReason.FAILED, error_details=details)
    assert result.error_details.code == "tool.timeout"
    dumped = result.model_dump()
    assert dumped["error_details"]["code"] == "tool.timeout"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/interfaces/test_run_result.py -v`
Expected: FAIL — `RunResult.error_details` does not exist; old fields still accessible.

- [ ] **Step 3: Replace `RunResult` fields**

In `openagents/interfaces/runtime.py`, locate `class RunResult(BaseModel, Generic[OutputT])`. Remove:

```python
    error: str | None = None
    exception: OpenAgentsError | None = None
```

Add:

```python
    error_details: ErrorDetails | None = None
```

Remove the now-unused `from openagents.errors.exceptions import OpenAgentsError` import at the top of the file if no other usage remains (it was only for the annotation).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/interfaces/test_run_result.py -v`
Expected: PASS (both new tests plus the 5 `ErrorDetails` tests from Task 4).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/interfaces/runtime.py tests/unit/interfaces/test_run_result.py
rtk git commit -m "refactor(runtime)!: replace RunResult.error/exception with error_details

BREAKING CHANGE: RunResult.error (str) and RunResult.exception (OpenAgentsError)
are removed. Consumers must read RunResult.error_details (ErrorDetails model)."
```

---

## Task 6: Runtime failure path builds `ErrorDetails`; event payloads carry it

**Files:**
- Modify: `openagents/plugins/builtin/runtime/default_runtime.py`
- Modify: `openagents/interfaces/pattern.py` (for `tool.failed` / `llm.failed` emits)
- Test: `tests/unit/runtime/test_error_details_emission.py` (new)

> **Line numbers in the impl steps below are HEAD-relative at plan-write time. Earlier tasks in a chained session can shift them. Use string anchors (`RUN_FAILED`, `"memory.inject.failed"`, `"tool.failed"`, `"llm.failed"`, `"run.checkpoint_failed"`, `"run.resume_attempted"`, `"run.resume_exhausted"`) for locating the edit sites.**

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runtime/test_error_details_emission.py
"""DefaultRuntime failure branch populates ErrorDetails and emits it in event payloads."""

from __future__ import annotations

import pytest

from openagents.errors.exceptions import ToolTimeoutError
from openagents.interfaces.runtime import RunRequest, StopReason
from openagents.plugins.builtin.runtime.default_runtime import DefaultRuntime

pytestmark = pytest.mark.asyncio


async def test_run_failed_emits_error_details_and_populates_run_result(
    failing_tool_runtime_factory,  # shared fixture: runtime + agent whose tool raises ToolTimeoutError
):
    """Wire up via existing factory; harness captures emitted events."""
    runtime, agent, events = await failing_tool_runtime_factory(
        exc=ToolTimeoutError("slow", tool_name="search"),
    )
    request = RunRequest(agent_id=agent.id, session_id="s1", input_text="go")
    result = await runtime.run(request=request, app_config=agent.app_config,
                               agents_by_id={agent.id: agent})

    assert result.stop_reason == StopReason.FAILED.value
    assert result.error_details is not None
    assert result.error_details.code == "tool.timeout"
    assert result.error_details.retryable is True

    failed = [e for e in events if e.name == "run.failed"]
    assert failed, "run.failed not emitted"
    payload = failed[-1].payload
    assert "error_details" in payload
    assert payload["error_details"]["code"] == "tool.timeout"
    # Legacy string field still present for backward compat.
    assert payload["error"].startswith("slow")
```

The fixture `failing_tool_runtime_factory` may already exist under `tests/unit/runtime/conftest.py`; check first. If not, add a minimal helper that reuses patterns from `tests/unit/runtime/test_core.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/runtime/test_error_details_emission.py -v`
Expected: FAIL — `KeyError: 'error_details'` in payload (runtime doesn't emit the field yet).

- [ ] **Step 3: Modify the failure branch of `DefaultRuntime.run`**

In `default_runtime.py` around line 1016-1072 (the `except Exception as exc:` block):

Replace the lines that currently build `run_result`:

```python
            run_result = RunResult(
                run_id=request.run_id,
                stop_reason=stop_reason,
                usage=usage,
                artifacts=list(artifacts),
                error=str(wrapped_exc),
                exception=wrapped_exc,
                metadata={...},
            )
```

with:

```python
            from openagents.interfaces.runtime import ErrorDetails

            details = ErrorDetails.from_exception(wrapped_exc)
            run_result = RunResult(
                run_id=request.run_id,
                stop_reason=stop_reason,
                usage=usage,
                artifacts=list(artifacts),
                error_details=details,
                metadata={
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                },
            )
```

And update the `RUN_FAILED` emit to include `error_details` alongside the legacy `error` string:

```python
            await self._event_bus.emit(
                RUN_FAILED,
                agent_id=request.agent_id,
                session_id=request.session_id,
                run_id=request.run_id,
                error=str(wrapped_exc),
                error_details=details.model_dump(),
            )
```

Do the same pattern for the `validation_exhausted` branch above (around line 935): emit `error_details=ErrorDetails.from_exception(validation_exhausted).model_dump()` alongside `error=str(validation_exhausted)`.

Also touch the `memory.inject.failed` / `memory.writeback.failed` emits (lines ~1625, 1667): add `error_details=ErrorDetails.from_exception(exc).model_dump()`.

For `tool.failed` and `llm.failed` emits (in `openagents/interfaces/pattern.py` lines ~215, 237, 319): add `error_details=ErrorDetails.from_exception(exc).model_dump() if isinstance(exc, BaseException) else None`.

For `run.checkpoint_failed` (line ~1195): add `error_details=ErrorDetails.from_exception(exc).model_dump()`.

For `run.resume_attempted` / `run.resume_exhausted` (lines ~895, 903): add `error_code=getattr(exc, "code", "error.unknown")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/runtime/test_error_details_emission.py -v`
Expected: PASS.

Also run: `uv run pytest tests/unit/runtime -v` to catch any cross-test fallout.
Expected: PASS for all (some tests may need tweaks deferred to Task 13).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/plugins/builtin/runtime/default_runtime.py openagents/interfaces/pattern.py tests/unit/runtime/test_error_details_emission.py
rtk git commit -m "feat(runtime): emit error_details in failure events; write ErrorDetails on RunResult"
```

---

## Task 7: Declare `error_details` / `error_code` in event taxonomy

**Files:**
- Modify: `openagents/interfaces/event_taxonomy.py`
- Modify: `docs/event-taxonomy.md` / `docs/event-taxonomy.en.md` (regenerate)
- Test: existing schema tests catch the new optional fields

- [ ] **Step 1: Add `error_details` / `error_code` to each relevant `EventSchema`**

| Event | Action |
|---|---|
| `run.failed` | Add `"error_details"` to `optional_payload` |
| `tool.failed` | Add `"error_details"` to `optional_payload` |
| `llm.failed` | Add `"error_details"` to `optional_payload` |
| `memory.inject.failed` | Add `"error_details"` to `optional_payload` |
| `memory.writeback.failed` | Add `"error_details"` to `optional_payload` |
| `run.checkpoint_failed` | Add `"error_details"` to `optional_payload` |
| `run.resume_attempted` | Add `"error_code"` to `optional_payload` |
| `run.resume_exhausted` | Add `"error_code"` to `optional_payload` |

(Kept optional to avoid forcing external subscribers to validate new fields.)

- [ ] **Step 2: Regenerate docs/event-taxonomy.{md,en.md}**

Run: `uv run python -m openagents.tools.gen_event_doc`
Expected: both files updated in place; diff only shows the new optional fields.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit -k "event_taxonomy or events" -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
rtk git add openagents/interfaces/event_taxonomy.py docs/event-taxonomy.md docs/event-taxonomy.en.md
rtk git commit -m "chore(events): declare optional error_details / error_code payload fields"
```

---

## Task 8: `RetryToolExecutor` reads `retryable` attribute, adds jitter + `retry_after_ms`

**Files:**
- Modify: `openagents/plugins/builtin/tool_executor/retry.py`
- Test: `tests/unit/plugins/builtin/tool_executor/test_retry.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/plugins/builtin/tool_executor/test_retry.py`:

```python
import random
from unittest.mock import AsyncMock

import pytest

from openagents.errors.exceptions import OpenAgentsError, ToolRateLimitError, ToolValidationError
from openagents.interfaces.tool import ToolExecutionRequest, ToolExecutionResult
from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor

pytestmark = pytest.mark.asyncio


class _CustomRetryable(OpenAgentsError):
    code = "user.custom_retryable"
    retryable = True


async def test_retry_uses_retryable_attribute_not_class_name_list(monkeypatch):
    """Attribute-driven classification catches user subclasses without configuration."""
    exc = _CustomRetryable("transient")
    inner = AsyncMock()
    inner.execute.side_effect = [
        ToolExecutionResult(tool_id="x", success=False, exception=exc, error="t"),
        ToolExecutionResult(tool_id="x", success=True, data="ok"),
    ]
    executor = RetryToolExecutor(config={"inner": {"type": "safe"}, "max_attempts": 2, "jitter": "none"})
    executor._inner = inner
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    result = await executor.execute(_dummy_request(tool_id="x"))
    assert result.success is True
    assert result.metadata["retry_attempts"] == 2


async def test_retry_skips_permanent_errors_by_attribute():
    exc = ToolValidationError("bad input", tool_name="x")  # retryable=False
    inner = AsyncMock()
    inner.execute.return_value = ToolExecutionResult(tool_id="x", success=False, exception=exc, error="bad input")
    executor = RetryToolExecutor(config={"inner": {"type": "safe"}, "max_attempts": 3, "jitter": "none"})
    executor._inner = inner
    result = await executor.execute(_dummy_request(tool_id="x"))
    assert result.success is False
    assert inner.execute.await_count == 1  # no retry


async def test_retry_uses_retry_after_ms_as_sleep_floor(monkeypatch):
    exc = ToolRateLimitError("429", tool_name="api", retry_after_ms=2000)
    inner = AsyncMock()
    inner.execute.side_effect = [
        ToolExecutionResult(tool_id="api", success=False, exception=exc, error="429"),
        ToolExecutionResult(tool_id="api", success=True, data="ok"),
    ]
    captured: list[float] = []
    async def _sleep(s):
        captured.append(s)
    monkeypatch.setattr("asyncio.sleep", _sleep)
    executor = RetryToolExecutor(config={
        "inner": {"type": "safe"},
        "max_attempts": 2,
        "initial_delay_ms": 100,
        "max_delay_ms": 5000,
        "jitter": "none",
    })
    executor._inner = inner
    result = await executor.execute(_dummy_request(tool_id="api"))
    assert result.success is True
    # initial backoff would be 100ms but retry_after_ms=2000 raises the floor
    assert captured[0] == pytest.approx(2.0)


async def test_jitter_equal_reduces_delay_to_half_plus_random(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda a, b: 0)  # lower bound of [0, delay/2]
    executor = RetryToolExecutor(config={
        "inner": {"type": "safe"},
        "initial_delay_ms": 1000,
        "backoff_multiplier": 1.0,
        "max_delay_ms": 5000,
        "jitter": "equal",
    })
    delay_ms = executor._delay_for(0, exc=None)
    assert delay_ms == 500  # 1000 // 2 + 0


def _dummy_request(*, tool_id: str) -> ToolExecutionRequest:
    from openagents.interfaces.tool import ToolExecutionSpec
    return ToolExecutionRequest(tool_id=tool_id, tool=object(), params={}, execution_spec=ToolExecutionSpec())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/plugins/builtin/tool_executor/test_retry.py -v -k "retryable_attribute or retry_after_ms or jitter_equal or permanent"`
Expected: FAIL on all four — config rejects `jitter`; `_should_retry` still uses `retry_on` list; `_delay_for` signature wrong.

- [ ] **Step 3: Update `RetryToolExecutor`**

In `openagents/plugins/builtin/tool_executor/retry.py`:

```python
import random
from typing import Literal

class RetryToolExecutor(ToolExecutorPlugin):
    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "safe"})
        max_attempts: int = 3
        initial_delay_ms: int = 200
        backoff_multiplier: float = 2.0
        max_delay_ms: int = 5_000
        jitter: Literal["none", "full", "equal"] = "equal"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities=set())
        cfg = self.Config.model_validate(self.config)
        self._max_attempts = max(1, cfg.max_attempts)
        self._initial_delay_ms = max(0, cfg.initial_delay_ms)
        self._backoff = max(1.0, cfg.backoff_multiplier)
        self._max_delay_ms = max(self._initial_delay_ms, cfg.max_delay_ms)
        self._jitter = cfg.jitter
        self._inner = self._load_inner(cfg.inner)

    def _should_retry(self, exc: Exception | None) -> bool:
        return getattr(exc, "retryable", False) is True

    def _delay_for(self, attempt: int, exc: Exception | None = None) -> int:
        base_ms = int(min(self._initial_delay_ms * (self._backoff ** attempt), self._max_delay_ms))
        floor_ms = int(getattr(exc, "retry_after_ms", 0) or 0)
        delay_ms = max(base_ms, floor_ms)
        if self._jitter == "full":
            return random.randint(0, delay_ms)
        if self._jitter == "equal":
            half = delay_ms // 2
            return half + random.randint(0, half)
        return delay_ms  # "none"
```

Update the one caller of `_delay_for` to pass `exc=result.exception`:

```python
            delay_ms = self._delay_for(attempt, result.exception)
```

Remove dead imports (`ToolTimeoutError` no longer referenced directly).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/plugins/builtin/tool_executor/test_retry.py -v`
Expected: PASS (new + existing tests; pre-existing tests that configured `retry_on` now need to drop that key — fix them as part of this commit if the old kwargs still get sent through).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/plugins/builtin/tool_executor/retry.py tests/unit/plugins/builtin/tool_executor/test_retry.py
rtk git commit -m "refactor(tool-executor)!: drive RetryToolExecutor by retryable attribute + add jitter

BREAKING CHANGE: RetryToolExecutor.Config no longer accepts retry_on or
retry_on_timeout. Classification reads exc.retryable; rate-limit sleep
floor reads exc.retry_after_ms; default jitter is 'equal'."
```

---

## Task 9: Remove `RETRYABLE_RUN_ERRORS`; durable resume reads `exc.retryable`

**Files:**
- Modify: `openagents/plugins/builtin/runtime/default_runtime.py`
- Test: `tests/unit/runtime/test_durable_resume_retryable_attribute.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runtime/test_durable_resume_retryable_attribute.py
"""Durable resume catches by exc.retryable, not by a hardcoded tuple."""

from __future__ import annotations

import pytest

from openagents.errors.exceptions import OpenAgentsError

pytestmark = pytest.mark.asyncio


class _UserRetryableError(OpenAgentsError):
    code = "user.my_retryable"
    retryable = True


class _UserPermanentError(OpenAgentsError):
    code = "user.my_permanent"
    retryable = False


async def test_durable_resume_catches_user_retryable_subclass(durable_runtime_factory):
    """User-defined retryable exc participates in durable resume without monkey-patching."""
    runtime, request = await durable_runtime_factory(
        raises_on_step=(1, _UserRetryableError("transient")),
        succeeds_on_step=2,
        durable=True,
    )
    result = await runtime.run_request(request)
    assert result.stop_reason.value == "completed"


async def test_durable_does_not_resume_user_permanent_subclass(durable_runtime_factory):
    runtime, request = await durable_runtime_factory(
        raises_on_step=(1, _UserPermanentError("fatal")),
        durable=True,
    )
    result = await runtime.run_request(request)
    assert result.stop_reason.value == "failed"
    assert result.error_details.code == "user.my_permanent"
```

If `durable_runtime_factory` is not already in a conftest, add a small one at `tests/unit/runtime/conftest.py` following the pattern of `tests/unit/runtime/test_durable_execution.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/runtime/test_durable_resume_retryable_attribute.py -v`
Expected: FAIL — `_UserRetryableError` is not in `RETRYABLE_RUN_ERRORS`, so no resume.

- [ ] **Step 3: Remove the constant and switch to attribute read**

In `openagents/plugins/builtin/runtime/default_runtime.py`:

Before deleting, verify each import is only used by the tuple:

```bash
rtk grep "LLMConnectionError\|LLMRateLimitError\|ToolRateLimitError\|ToolUnavailableError" openagents/plugins/builtin/runtime/default_runtime.py
```

Expected: only the `RETRYABLE_RUN_ERRORS` tuple line(s). If any other `isinstance` / raise site remains, keep that import.

1. Delete the imports at top that are now unused (only those confirmed unused after the grep).
2. Delete the `RETRYABLE_RUN_ERRORS: tuple[...] = (...)` constant (around line 78).
3. Locate `except RETRYABLE_RUN_ERRORS as exc:` (line ~882). Replace with:

```python
                        except OpenAgentsError as exc:
                            if not request.durable or not exc.retryable:
                                raise
                            durable_blob = session_state.get(_DURABLE_STATE_KEY) or {}
                            checkpoint_id = durable_blob.get("checkpoint_id")
                            ...  # rest of block unchanged
```

Keep the `OpenAgentsError` import already at the top.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/runtime/test_durable_resume_retryable_attribute.py tests/unit/runtime/test_durable_execution.py -v`
Expected: PASS for both (existing durable tests still pass because `LLMRateLimitError`, `LLMConnectionError`, `ToolRateLimitError`, `ToolUnavailableError` all have `retryable=True`).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/plugins/builtin/runtime/default_runtime.py tests/unit/runtime/test_durable_resume_retryable_attribute.py
[ -f tests/unit/runtime/conftest.py ] && rtk git add tests/unit/runtime/conftest.py
rtk git commit -m "refactor(runtime)!: durable resume reads exc.retryable instead of RETRYABLE_RUN_ERRORS

BREAKING CHANGE: RETRYABLE_RUN_ERRORS tuple is removed from default_runtime.
User-defined OpenAgentsError subclasses with retryable=True now participate
in durable resume automatically."
```

---

## Task 10: `_http_base` propagates `Retry-After` into `LLMRateLimitError.retry_after_ms`

**Files:**
- Modify: `openagents/llm/providers/_http_base.py`
- Test: `tests/unit/llm/providers/test_retry_after_propagation.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/llm/providers/test_retry_after_propagation.py
"""When HTTP retries are exhausted on 429, the raised LLMRateLimitError carries retry_after_ms."""

from __future__ import annotations

import pytest

from openagents.errors.exceptions import LLMRateLimitError
from openagents.llm.providers._http_base import (
    _RetryPolicy,
    _make_error_for_status,
)


def test_make_error_for_status_threads_retry_after_ms():
    exc = _make_error_for_status(
        url="https://example/api",
        status=429,
        body_excerpt="Too Many Requests",
        retryable_status=_RetryPolicy().retryable_status,
        retry_after_ms=5000,
    )
    assert isinstance(exc, LLMRateLimitError)
    assert exc.retry_after_ms == 5000


def test_make_error_for_status_retry_after_none_when_absent():
    exc = _make_error_for_status(
        url="https://example/api",
        status=429,
        body_excerpt="",
        retryable_status=_RetryPolicy().retryable_status,
        retry_after_ms=None,
    )
    assert isinstance(exc, LLMRateLimitError)
    assert exc.retry_after_ms is None
```

Add an integration-style test that spins `_request` against an `httpx.MockTransport` returning 429 + `Retry-After: 5`:

```python
import httpx
from openagents.llm.providers._http_base import HTTPProviderClient


@pytest.mark.asyncio
async def test_request_exhausted_retries_raises_with_retry_after_ms(monkeypatch):
    call_count = {"n": 0}
    def _handler(request):
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "5"}, text="slow")
    transport = httpx.MockTransport(_handler)

    class _Stub(HTTPProviderClient):
        async def _get_http_client(self):
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(transport=transport)
            return self._http_client

    client = _Stub(timeout_ms=1000)
    client._retry_policy = _RetryPolicy(max_attempts=2, initial_backoff_ms=1, max_backoff_ms=1)
    monkeypatch.setattr("asyncio.sleep", lambda *_: None)

    with pytest.raises(LLMRateLimitError) as ei:
        await client._request("POST", "https://api.example/messages", json_body={})
    assert ei.value.retry_after_ms == 5000
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/llm/providers/test_retry_after_propagation.py -v`
Expected: FAIL — `_make_error_for_status` has no `retry_after_ms` parameter.

- [ ] **Step 3: Thread `retry_after_ms` through `_make_error_for_status`, `_request`, and `_open_stream`**

In `openagents/llm/providers/_http_base.py`:

```python
def _make_error_for_status(
    *,
    url: str,
    status: int,
    body_excerpt: str,
    retryable_status: frozenset[int],
    retry_after_ms: int | None = None,
) -> Exception:
    classifier = _classify_status(status, retryable_status)
    msg = f"HTTP {status}: {body_excerpt}"
    if classifier == "rate_limit":
        hint = "provider rate-limited or overloaded; increase 'llm.retry.max_attempts' or slow down request rate"
        return LLMRateLimitError(msg, hint=hint, retry_after_ms=retry_after_ms).with_context()
    if classifier == "connection":
        hint = f"upstream server error from {url}; check provider status"
        return LLMConnectionError(msg, hint=hint)
    return LLMResponseError(msg, hint=f"non-retryable HTTP {status} from {url}")
```

In `_request`, when falling out of the loop, compute retry_after_ms from the last response's `Retry-After` header and thread it in:

```python
        if last_response is not None:
            last_headers = _response_headers(last_response)
            ra_s = _parse_retry_after_seconds(
                last_headers.get("Retry-After") or last_headers.get("retry-after")
            )
            raise _make_error_for_status(
                url=url,
                status=int(getattr(last_response, "status_code", 0)),
                body_excerpt=_body_excerpt(last_response),
                retryable_status=retryable,
                retry_after_ms=int(ra_s * 1000) if ra_s is not None else None,
            )
```

Same change in `_open_stream` (around the "last_status" raise site).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/llm/providers/test_retry_after_propagation.py -v`
Expected: PASS.

Also run: `uv run pytest tests/unit/llm -v` to catch fallout.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/llm/providers/_http_base.py tests/unit/llm/providers/test_retry_after_propagation.py
rtk git commit -m "feat(llm): propagate Retry-After header into LLMRateLimitError.retry_after_ms"
```

---

## Task 11: `LiteLLMClient._map_litellm_exception` best-effort `retry_after`

**Files:**
- Modify: `openagents/llm/providers/litellm_client.py`
- Test: `tests/unit/llm/providers/test_retry_after_propagation.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to the test file:

```python
from openagents.llm.providers.litellm_client import _map_litellm_exception


def test_map_litellm_rate_limit_reads_retry_after():
    class _FakeRateLimitError(Exception):
        pass
    _FakeRateLimitError.__module__ = "litellm.exceptions"
    _FakeRateLimitError.__name__ = "RateLimitError"

    exc = _FakeRateLimitError("slow down")
    exc.retry_after = 7  # seconds, LiteLLM sometimes sets this
    mapped = _map_litellm_exception(exc)
    assert isinstance(mapped, LLMRateLimitError)
    assert mapped.retry_after_ms == 7000


def test_map_litellm_rate_limit_retry_after_none_when_absent():
    class _FakeRateLimitError(Exception):
        pass
    _FakeRateLimitError.__module__ = "litellm.exceptions"
    _FakeRateLimitError.__name__ = "RateLimitError"
    mapped = _map_litellm_exception(_FakeRateLimitError("slow"))
    assert isinstance(mapped, LLMRateLimitError)
    assert mapped.retry_after_ms is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/llm/providers/test_retry_after_propagation.py -v`
Expected: FAIL — `LLMRateLimitError` constructed without `retry_after_ms`.

- [ ] **Step 3: Update the mapping**

In `openagents/llm/providers/litellm_client.py`:

```python
def _map_litellm_exception(exc: BaseException) -> Exception:
    if not type(exc).__module__.startswith("litellm"):
        return exc
    name = type(exc).__name__
    if name == "RateLimitError":
        ra_s = getattr(exc, "retry_after", None)
        retry_after_ms = int(ra_s * 1000) if isinstance(ra_s, (int, float)) and ra_s > 0 else None
        return LLMRateLimitError(str(exc), retry_after_ms=retry_after_ms)
    if name in ("APIConnectionError", "Timeout"):
        return LLMConnectionError(str(exc))
    return LLMResponseError(str(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/llm/providers/test_retry_after_propagation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/llm/providers/litellm_client.py tests/unit/llm/providers/test_retry_after_propagation.py
rtk git commit -m "feat(llm): litellm retry_after best-effort → LLMRateLimitError.retry_after_ms"
```

---

## Task 12: `ErrorSnapshot.error_code` + diagnostics plugins

**Files:**
- Modify: `openagents/interfaces/diagnostics.py`
- Modify: `openagents/plugins/builtin/diagnostics/phoenix_plugin.py`
- Modify: `openagents/plugins/builtin/diagnostics/langfuse_plugin.py`
- Modify: `openagents/plugins/builtin/diagnostics/rich_plugin.py`
- Test: modifications to `tests/unit/plugins/builtin/diagnostics/*.py`

- [ ] **Step 1: Write the failing test**

Pick the existing diagnostics test file (likely `tests/unit/plugins/builtin/diagnostics/test_capture_error_snapshot.py` — verify by `ls`). If absent, add a focused test:

```python
# tests/unit/plugins/builtin/diagnostics/test_error_code_field.py
from __future__ import annotations

from openagents.errors.exceptions import ToolTimeoutError
from openagents.interfaces.diagnostics import DiagnosticsPlugin


def test_capture_error_snapshot_sets_error_code():
    plugin = DiagnosticsPlugin()
    snap = plugin.capture_error_snapshot(
        run_id="r", agent_id="a", session_id="s",
        exc=ToolTimeoutError("slow", tool_name="x"),
    )
    assert snap.error_code == "tool.timeout"


def test_capture_error_snapshot_falls_back_for_non_openagents_error():
    plugin = DiagnosticsPlugin()
    snap = plugin.capture_error_snapshot(run_id="r", agent_id="a", session_id="s", exc=ValueError("bad"))
    assert snap.error_code == "error.unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/plugins/builtin/diagnostics/test_error_code_field.py -v`
Expected: FAIL — `ErrorSnapshot` has no `error_code`.

- [ ] **Step 3: Add the field and populate it**

In `openagents/interfaces/diagnostics.py`:

```python
@dataclass
class ErrorSnapshot:
    run_id: str
    agent_id: str
    session_id: str
    error_type: str
    error_message: str
    traceback: str
    tool_call_chain: list[dict[str, Any]]
    last_transcript: list[dict[str, Any]]
    usage_at_failure: dict[str, Any]
    state_snapshot: dict[str, Any]
    captured_at: str
    error_code: str = "error.unknown"   # NEW — defaulted so this stays a non-breaking dataclass extension
```

Placed at the end with a default so any external code constructing `ErrorSnapshot` positionally doesn't break.

In `DiagnosticsPlugin.capture_error_snapshot`, compute:

```python
        error_code = getattr(exc, "code", None) or "error.unknown"
        ...
        return ErrorSnapshot(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            error_type=type(exc).__name__,
            error_code=error_code,
            error_message=str(exc),
            ...
        )
```

In each diagnostics plugin, add an attribute write alongside the existing `error_type`:

- `phoenix_plugin.py` (~line 94): `root_span.set_attribute("error.code", snapshot.error_code)`
- `langfuse_plugin.py` (~line 87): add `"error_code": snapshot.error_code` to the dict
- `rich_plugin.py` (~line 94): adjust format to include `[dim]({snapshot.error_code})[/]`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/plugins/builtin/diagnostics -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/interfaces/diagnostics.py openagents/plugins/builtin/diagnostics/phoenix_plugin.py openagents/plugins/builtin/diagnostics/langfuse_plugin.py openagents/plugins/builtin/diagnostics/rich_plugin.py tests/unit/plugins/builtin/diagnostics/test_error_code_field.py
rtk git commit -m "feat(diagnostics): ErrorSnapshot.error_code + plugin attribute writes"
```

---

## Task 13: Migrate existing tests reading `result.error` / `result.exception`

**Files:** Whatever the grep turns up.

- [ ] **Step 1: Find every consumer**

Run:
```bash
rtk grep "result\.error[^_]" tests/
rtk grep "result\.exception" tests/
```

Expected output: a small list (estimate: 8-15 files based on existing codebase patterns).

- [ ] **Step 2: For each match, migrate**

Mechanical rewrite:
- `result.error` → `result.error_details.message` (or `result.error_details.code` where the test was actually checking the class, not the text)
- `result.exception` → `result.error_details` for shape checks; or `isinstance(... , SomeError)` patterns switch to `result.error_details.code == "some.code"`

For tests that use `assert result.exception is not None`, switch to `assert result.error_details is not None`.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (coverage below may be reported — that's fine until Task 15).

- [ ] **Step 4: Commit**

```bash
rtk git add tests/
rtk git commit -m "test: migrate result.error / result.exception readers to error_details"
```

---

## Task 14: `docs/errors.md` (Chinese primary) + `docs/errors.en.md` (English mirror)

**Files:**
- Create: `docs/errors.md`
- Create: `docs/errors.en.md`
- Modify: `docs/developer-guide.md`, `docs/developer-guide.en.md` (link)

- [ ] **Step 1: Author `docs/errors.md`**

Structure (see spec §4.1). Skeleton:

````markdown
# 错误参考

本手册列出所有 `OpenAgentsError` 子类、code、可重试性、典型 hint 及推荐处理策略。
所有错误都带 `.to_dict()` 方法用于序列化，失败的 `RunResult.error_details` 会 mirror 这个结构。

## 总览表

| code | 类 | retryable | 典型 stop_reason |
|---|---|---|---|
| `openagents.error` | `OpenAgentsError` | ❌ | `failed` |
| `config.error` | `ConfigError` | ❌ | `failed` |
| `config.load` | `ConfigLoadError` | ❌ | `failed` |
| `config.validation` | `ConfigValidationError` | ❌ | `failed` |
| `plugin.error` | `PluginError` | ❌ | `failed` |
| `plugin.load` | `PluginLoadError` | ❌ | `failed` |
| `plugin.capability` | `PluginCapabilityError` | ❌ | `failed` |
| `plugin.config` | `PluginConfigError` | ❌ | `failed` |
| `execution.error` | `ExecutionError` | ❌ | `failed` |
| `execution.max_steps` | `MaxStepsExceeded` | ❌ | `max_steps` |
| `execution.budget_exhausted` | `BudgetExhausted` | ❌ | `budget_exhausted` |
| `execution.output_validation` | `OutputValidationError` | ❌ | `failed` |
| `session.error` | `SessionError` | ❌ | `failed` |
| `pattern.error` | `PatternError` | ❌ | `failed` |
| `tool.error` | `ToolError` | ❌ | `failed` |
| `tool.retryable` | `RetryableToolError` | ✅ | `failed` |
| `tool.permanent` | `PermanentToolError` | ❌ | `failed` |
| `tool.timeout` | `ToolTimeoutError` | ✅ | `failed` |
| `tool.not_found` | `ToolNotFoundError` | ❌ | `failed` |
| `tool.validation` | `ToolValidationError` | ❌ | `failed` |
| `tool.auth` | `ToolAuthError` | ❌ | `failed` |
| `tool.rate_limit` | `ToolRateLimitError` | ✅ | `failed` |
| `tool.unavailable` | `ToolUnavailableError` | ✅ | `failed` |
| `tool.cancelled` | `ToolCancelledError` | ❌ | `failed` |
| `llm.error` | `LLMError` | ❌ | `failed` |
| `llm.connection` | `LLMConnectionError` | ✅ | `failed` |
| `llm.rate_limit` | `LLMRateLimitError` | ✅ | `failed` |
| `llm.response` | `LLMResponseError` | ❌ | `failed` |
| `llm.model_retry` | `ModelRetryError` | ❌ (consumed by runtime finalize loop) | `failed` |
| `user.error` | `UserError` | ❌ | `failed` |
| `user.invalid_input` | `InvalidInputError` | ❌ | `failed` |
| `user.agent_not_found` | `AgentNotFoundError` | ❌ | `failed` |

## config.*

### `config.load` — `ConfigLoadError`
- 触发：`load_config()` 读不到文件 / JSON 损坏 / 环境变量缺失
- retryable: false
- 典型 hint: "Run from the repo root, or pass an absolute path to the config file"
- 推荐处理：修文件路径 / 补充环境变量 / 修 JSON 语法

### `config.validation` — `ConfigValidationError`
- 触发：config 不符合 `AppConfig` pydantic schema
...

## plugin.*
...

## execution.*
...

## session.*
...

## pattern.*
...

## tool.*
### `tool.timeout` — `ToolTimeoutError`
- retryable: **true**
- RetryToolExecutor 会自动重试
...

### `tool.rate_limit` — `ToolRateLimitError`
- retryable: **true**
- 携带 `retry_after_ms` 字段；RetryToolExecutor 会把它作为 sleep 下限。
...

## llm.*
### `llm.rate_limit` — `LLMRateLimitError`
- retryable: **true**
- 携带 `retry_after_ms`，解析自 `Retry-After` 头（delta-seconds 或 HTTP-date 均支持）
...

## user.*
...

## 自定义错误类

```python
from openagents.errors import RetryableToolError

class MyToolQuotaError(RetryableToolError):
    code = "tool.my_quota"
    # retryable 继承 True
```

声明后 `RetryToolExecutor` / durable resume 自动把该类作为可重试。Code 必须是 dotted 且全局唯一。
````

Every row in the overview table must correspond to an `OpenAgentsError` subclass declared in `openagents/errors/exceptions.py`. Use `test_errors_md_coverage.py` (Task 15) as the drift gate.

- [ ] **Step 2: Translate to `docs/errors.en.md`**

Mirror the structure, translate commentary. Keep the same section headings (dotted codes) so the coverage test can scan both.

- [ ] **Step 3: Link from developer-guide**

Append one bullet under the "Reference" section of both `docs/developer-guide.md` and `docs/developer-guide.en.md`:

```markdown
- [Error reference / 错误参考](errors.md)
```

- [ ] **Step 4: Preview**

Skim both files locally; ensure no `TODO` / `TBD` markers left.

- [ ] **Step 5: Commit**

```bash
rtk git add docs/errors.md docs/errors.en.md docs/developer-guide.md docs/developer-guide.en.md
rtk git commit -m "docs: errors.md error reference (zh + en) linked from developer guide"
```

---

## Task 15: `docs/migration-0.3-to-0.4.md` + docs coverage gate

**Files:**
- Create: `docs/migration-0.3-to-0.4.md`
- Create: `tests/unit/docs/test_errors_md_coverage.py`

- [ ] **Step 1: Write the coverage test**

```python
# tests/unit/docs/test_errors_md_coverage.py
"""Drift gate: every OpenAgentsError subclass must appear (by code) in both
docs/errors.md and docs/errors.en.md."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import openagents.errors.exceptions as errors_mod
from openagents.errors.exceptions import OpenAgentsError

ROOT = Path(__file__).resolve().parents[3]


def _all_codes() -> list[str]:
    codes = []
    for _, cls in inspect.getmembers(errors_mod, inspect.isclass):
        if issubclass(cls, OpenAgentsError) and cls.__module__ == errors_mod.__name__:
            codes.append(cls.code)
    return codes


@pytest.mark.parametrize("doc_path", ["docs/errors.md", "docs/errors.en.md"])
def test_errors_doc_covers_every_code(doc_path):
    text = (ROOT / doc_path).read_text(encoding="utf-8")
    missing = [c for c in _all_codes() if c not in text]
    assert not missing, f"{doc_path} missing codes: {missing}"
```

- [ ] **Step 2: Run test to verify it passes (or fails, revealing gaps)**

Run: `uv run pytest tests/unit/docs/test_errors_md_coverage.py -v`
Expected: PASS if Task 14 was complete; otherwise the list of missing codes tells you what to add.

- [ ] **Step 3: Write the migration guide**

```markdown
# 0.3 → 0.4 Migration Guide

## Breaking Changes

### 1. `RunResult.error` / `RunResult.exception` removed

| 0.3 | 0.4 |
|---|---|
| `result.error: str \| None` | `result.error_details.message: str` |
| `result.exception: OpenAgentsError \| None` | `result.error_details` (structured model) |
| — | `result.error_details.code: str` |
| — | `result.error_details.retryable: bool` |
| — | `result.error_details.hint / docs_url / context / cause` |

**Migration:**
```python
# Before
if result.exception is not None:
    log.error("run failed: %s", result.error)
    raise result.exception

# After
if result.error_details is not None:
    log.error("run failed [%s]: %s", result.error_details.code, result.error_details.message)
    # The original exception object is no longer on RunResult; subscribe to
    # the 'run.failed' event or read DiagnosticsPlugin snapshots if you need it.
```

### 2. `RetryToolExecutor` configuration changed

Removed fields: `retry_on`, `retry_on_timeout`.
New field: `jitter ∈ {"none", "full", "equal"}` (default `"equal"`).

Classification now reads `exc.retryable` (a ClassVar on every `OpenAgentsError` subclass).

**Migration:**
```json
// Before
{"tool_executor": {"type": "retry", "config": {
    "retry_on": ["ToolTimeoutError", "ToolRateLimitError"],
    "retry_on_timeout": true
}}}

// After
{"tool_executor": {"type": "retry", "config": {
    "jitter": "equal"
}}}
```

### 3. `DefaultRuntime.RETRYABLE_RUN_ERRORS` constant removed

External code should not import this constant. Durable resume classification
is now attribute-based: any `OpenAgentsError` subclass with `retryable = True`
participates automatically.

### 4. Event payloads: new `error_details` / `error_code` fields

New optional fields on `run.failed`, `tool.failed`, `llm.failed`,
`memory.inject.failed`, `memory.writeback.failed`, `run.checkpoint_failed`
(`error_details: dict`), and `run.resume_attempted`, `run.resume_exhausted`
(`error_code: str`). Legacy `error` and `error_type` fields remain for
backward compat and may be deprecated in a later release.

## New Capabilities

- `OpenAgentsError.to_dict()` — stable JSON-serializable shape.
- `ToolRateLimitError.retry_after_ms` / `LLMRateLimitError.retry_after_ms` — threaded from `Retry-After` headers and used as retry sleep floor.
- `ErrorDetails.from_exception(exc)` — walk `__cause__` up to depth 3.
- `RetryToolExecutor` jitter (`none` / `full` / `equal`, default `equal`).
- `docs/errors.md` error reference manual.

## Declaring Custom Retryable Errors

```python
from openagents.errors import RetryableToolError

class MyToolQuotaError(RetryableToolError):
    code = "tool.my_quota"
    # retryable inherited = True
```

No registration needed — `RetryToolExecutor` and durable resume see it automatically.
```

- [ ] **Step 4: Run coverage gate again**

Run: `uv run pytest tests/unit/docs/test_errors_md_coverage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add docs/migration-0.3-to-0.4.md tests/unit/docs/test_errors_md_coverage.py
rtk git commit -m "docs: 0.3-to-0.4 migration guide + errors.md coverage gate"
```

---

## Task 16: Final verification + coverage

**Files:** None (verification only)

- [ ] **Step 1: Run full suite**

Run: `uv run pytest -q`
Expected: All tests PASS.

- [ ] **Step 2: Run coverage**

Run: `uv run coverage run -m pytest && uv run coverage report`
Expected:
- Overall ≥ 90% (floor from `pyproject.toml`).
- `openagents/errors/exceptions.py` ≥ 95%.
- `openagents/plugins/builtin/tool_executor/retry.py` ≥ 95%.
- `openagents/llm/providers/_http_base.py` ≥ 95%.

If any hotspot is below target, add a targeted test (e.g. `test_retry_jitter_full_mode` for `RetryToolExecutor._delay_for`'s `full` branch).

- [ ] **Step 3: Skim changelog / verify acceptance criteria**

Walk through the 11 acceptance criteria in the spec's "验收标准" section; tick each one with a grep or a test that proves it. If any fails, open a follow-up task before declaring done.

- [ ] **Step 4: Commit verification artifacts (if any)**

If coverage floor needs adjusting via targeted tests, commit them:
```bash
rtk git add tests/
rtk git commit -m "test: raise coverage on retry and transport hotspots to ≥95%"
```

- [ ] **Step 5: Final announcement**

All Tier 1 tasks complete. Surface summary to user (affected files, coverage, migration guide location). Do NOT merge to main without explicit user approval — @superpowers:finishing-a-development-branch covers the handoff.

---

## Follow-ups (not in this plan)

- **Tier 2** (separate spec/plan): new typed errors (`RunCancelledError`, `ContentFilteredError`, `TokenBudgetExceededError`, `ContextAssemblyError`, `SkillError`, `StreamError`); extend `LLMChunkErrorType` buckets; silent-swallow audit events; structured validation-retry feedback.
- **Tier 3** (separate spec/plan): circuit breaker; `BatchError(ExceptionGroup)`; error pattern aggregation.
- Consider deprecation timeline for legacy event fields (`error: str`, `error_type: str`) during Tier 2 planning — give SSE / OTel subscribers one release cycle of warning before removing.
