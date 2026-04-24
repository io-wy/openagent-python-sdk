# 0.3 → 0.4 Migration Guide

Error handling underwent a Tier 1 overhaul. This document lists the breaking
changes and the mechanical migration path.

## Breaking Changes

### 1. `RunResult.error` / `RunResult.exception` removed

| 0.3 | 0.4 |
|---|---|
| `result.error: str \| None` | `result.error_details.message: str` |
| `result.exception: OpenAgentsError \| None` | `result.error_details` (structured `ErrorDetails` model) |
| — | `result.error_details.code: str` (dotted, e.g. `tool.timeout`) |
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
    log.error(
        "run failed [%s]: %s",
        result.error_details.code,
        result.error_details.message,
    )
    # The original exception object is no longer on RunResult; subscribe to
    # the 'run.failed' event or read DiagnosticsPlugin snapshots if you need
    # the live object.
```

For code that dispatched on exception class:
```python
# Before
if isinstance(result.exception, ToolTimeoutError):
    ...

# After
if result.error_details and result.error_details.code == "tool.timeout":
    ...
```

### 2. `RetryToolExecutor` configuration changed

Removed fields: `retry_on`, `retry_on_timeout`.
New field: `jitter ∈ {"none", "full", "equal"}` (default `"equal"`).

Classification now reads `exc.retryable` (a `ClassVar` on every `OpenAgentsError` subclass).

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

If you had subclassed `RetryToolExecutor` or constructed it programmatically with `retry_on=...`, drop that kwarg entirely. All `OpenAgentsError` subclasses with `retryable=True` are captured automatically.

### 3. `DefaultRuntime.RETRYABLE_RUN_ERRORS` constant removed

External code should not import this constant. Durable resume classification
is now attribute-based: any `OpenAgentsError` subclass with `retryable = True`
participates automatically.

If you had:
```python
from openagents.plugins.builtin.runtime.default_runtime import RETRYABLE_RUN_ERRORS
```
Delete the import. To declare a custom retryable class, set `retryable = True` on the subclass — no registration needed.

### 4. Runtime wrapper `Runtime.run()` raises `RuntimeError` instead of the original exception

`Runtime.run(agent_id=..., session_id=..., input_text=...)` (the high-level wrapper that returns `final_output` or raises) now unconditionally raises `RuntimeError` with the error message when the run fails.

For code that caught specific exception types:
```python
# Before
try:
    output = await runtime.run(agent_id="a", session_id="s", input_text="hi")
except ToolTimeoutError:
    ...

# After — switch to run_detailed for structured access
result = await runtime.run_detailed(request=RunRequest(...))
if result.error_details and result.error_details.code == "tool.timeout":
    ...
```

Or if `run()` is convenient, match on `RuntimeError` and parse the message / use `run_detailed` for classification.

### 5. Event payloads: new `error_details` / `error_code` fields

New OPTIONAL fields on:
- `run.failed`, `tool.failed`, `llm.failed`, `memory.inject.failed`, `memory.writeback.failed`, `run.checkpoint_failed` → add `error_details: dict` (same shape as `ErrorDetails`)
- `run.resume_attempted`, `run.resume_exhausted` → add `error_code: str`

The legacy string `error` field on `*.failed` events is preserved for backward compat. It may be deprecated in a later release.

## New Capabilities

- `OpenAgentsError.to_dict()` — stable JSON-serializable shape (code, message, hint, docs_url, retryable, context)
- `ToolRateLimitError.retry_after_ms` / `LLMRateLimitError.retry_after_ms` — threaded from `Retry-After` headers; used as retry sleep floor by `RetryToolExecutor`
- `ErrorDetails.from_exception(exc)` — walks `__cause__` up to depth 3, cycle-safe
- `RetryToolExecutor` jitter (`none` / `full` / `equal`, default `equal`)
- `ErrorSnapshot.error_code` — new dotted-code field for diagnostics plugins
- `docs/errors.md` — complete error reference manual (zh + en)

## Declaring Custom Retryable Errors

```python
from openagents.errors import RetryableToolError

class MyToolQuotaError(RetryableToolError):
    code = "tool.my_quota"
    # retryable inherited = True
```

No registration needed — `RetryToolExecutor` and durable resume see it automatically via attribute lookup.

`code` must be dotted (e.g. `tool.my_quota`), matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, and globally unique across all `OpenAgentsError` subclasses in the process.

## See Also

- [docs/errors.md](errors.md) — error reference manual
