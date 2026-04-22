# Error Reference

This manual lists every `OpenAgentsError` subclass, its dotted code, retryability, typical hints, and recommended handling strategies.

All errors expose a `.to_dict()` method for serialization. A failed `RunResult.error_details` mirrors this structure.

At the event level: every `*.failed` event payload includes `error_details: dict` (same shape as `.to_dict()`);
`run.resume_attempted` / `run.resume_exhausted` include `error_code: str`.

## Overview Table

| code | class | retryable | typical stop_reason |
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

## openagents.*

### `openagents.error` — `OpenAgentsError` (base)

The root class for all SDK errors.

- **retryable**: false
- **Common fields**: `agent_id`, `session_id`, `run_id`, `tool_id`, `step_number`, `hint`, `docs_url`
- **Serialization**: `.to_dict()` returns `{code, message, hint, docs_url, retryable, context}`
- **Handling**: Catch more specific subclasses when possible; catch the base only when the concrete type cannot be anticipated

## config.*

### `config.load` — `ConfigLoadError`

- **Raised when**: `load_config()` cannot read the file, encounters a JSON syntax error, or a required env var is not set
- **retryable**: false
- **Typical hint**: "Run from the repo root, or pass an absolute path to the config file"
- **Handling**: Fix the file path, set missing environment variables, repair JSON syntax

### `config.validation` — `ConfigValidationError`

- **Raised when**: the config payload violates the `AppConfig` pydantic schema
- **retryable**: false
- **Handling**: Correct the fields as described in [Configuration Reference](configuration.md)

### `config.error` — `ConfigError` (base)

- **Raised when**: other configuration failures; usually a more specific subclass is raised
- **retryable**: false

## plugin.*

### `plugin.load` — `PluginLoadError`

- **Raised when**: `plugins/loader.py` cannot resolve a `type` / `impl` reference or the import fails
- **retryable**: false
- **Typical hint**: "Did you mean?" near-match suggestion
- **Handling**: Fix the `type` / `impl` field; confirm the module is installed

### `plugin.capability` — `PluginCapabilityError`

- **Raised when**: a plugin is missing required capability methods
- **retryable**: false
- **Handling**: Implement all interface methods required by the plugin's declared capabilities

### `plugin.config` — `PluginConfigError`

- **Raised when**: a plugin's `config` sub-object is invalid (`TypedConfigPluginMixin` validation failed)
- **retryable**: false
- **Handling**: Correct the fields against the plugin's `Config` schema

### `plugin.error` — `PluginError` (base)

- **Raised when**: other plugin-related failures; usually a more specific subclass is raised
- **retryable**: false

## execution.*

### `execution.max_steps` — `MaxStepsExceeded`

- **Raised when**: the pattern exceeds `max_steps`, the tool-call budget, or the session step limit
- **retryable**: false
- **stop_reason**: `max_steps`
- **Handling**: Increase `agent.runtime.max_steps`, or optimize the pattern to converge faster

### `execution.budget_exhausted` — `BudgetExhausted`

- **Raised when**: a `RunBudget` dimension (tool_calls / duration / cost) is exceeded
- **retryable**: false
- **stop_reason**: `budget_exhausted`
- **Extra fields**: `kind` (tool_calls|duration|steps|cost), `current`, `limit`
- **Handling**: Relax the corresponding `RunBudget` field; for `kind="cost"` check `max_cost_usd`

### `execution.output_validation` — `OutputValidationError`

- **Raised when**: the finalize phase fails `output_type.model_validate` after `max_validation_retries` consecutive attempts
- **retryable**: false (not retryable at the run level; individual attempts are consumed by `ModelRetryError` → runtime finalize loop)
- **Extra fields**: `attempts`, `last_validation_error`, `output_type`
- **Handling**: Adjust pattern output or relax the `output_type` schema; increase retries via `RunBudget.max_validation_retries`

### `session.error` — `SessionError`

- **Raised when**: session management fails (e.g., session lock acquisition timeout, persistence error)
- **retryable**: false
- **Handling**: Check session storage configuration

### `pattern.error` — `PatternError`

- **Raised when**: an untyped exception occurs during pattern execution (the runtime wraps it into `PatternError`)
- **retryable**: false
- **Handling**: Inspect the `cause` field for the original exception; fix the pattern implementation

### `execution.error` — `ExecutionError` (base)

- **Raised when**: other runtime execution failures; usually a more specific subclass is raised
- **retryable**: false

## tool.*

### `tool.timeout` — `ToolTimeoutError`

- **Raised when**: `tool.invoke` exceeds `execution_spec.default_timeout_ms`
- **retryable**: ✅ true
- **Handling**: `RetryToolExecutor` retries automatically; if timeouts persist, increase the timeout or speed up the tool

### `tool.rate_limit` — `ToolRateLimitError`

- **Raised when**: the tool endpoint signals rate limiting
- **retryable**: ✅ true
- **Extra fields**: `retry_after_ms: int | None` — when set, `RetryToolExecutor._delay_for` uses it as a sleep floor
- **Handling**: `RetryToolExecutor` backs off automatically; if `retry_after_ms` is consistently large, consider upgrading your API quota

### `tool.unavailable` — `ToolUnavailableError`

- **Raised when**: the tool is temporarily unreachable (DNS failure, TCP error, 5xx)
- **retryable**: ✅ true
- **Handling**: `RetryToolExecutor` retries automatically; investigate network or service availability

### `tool.retryable` — `RetryableToolError` (base)

- **Raised when**: a retryable tool error; concrete subclasses: `ToolTimeoutError`, `ToolRateLimitError`, `ToolUnavailableError`
- **retryable**: ✅ true

### `tool.permanent` — `PermanentToolError` (base)

- **Raised when**: a non-retryable tool error; concrete subclasses: `ToolNotFoundError`, `ToolValidationError`, `ToolAuthError`, `ToolCancelledError`
- **retryable**: false

### `tool.not_found` — `ToolNotFoundError`

- **Raised when**: the pattern requests a tool ID that is not registered
- **retryable**: false
- **Handling**: Check tool registration; ensure the tool's `id` matches what the pattern requests

### `tool.validation` — `ToolValidationError`

- **Raised when**: `tool.validate_params` returns false, or the tool itself rejects the parameters
- **retryable**: false
- **Handling**: Correct the call parameters; check that the tool schema aligns with LLM output

### `tool.auth` — `ToolAuthError`

- **Raised when**: the tool endpoint returns 401 or 403
- **retryable**: false (new credentials are required)
- **Handling**: Rotate API token / credentials; verify IAM permissions

### `tool.cancelled` — `ToolCancelledError`

- **Raised when**: `cancel_event` is set and the tool execution is cancelled mid-flight
- **retryable**: false (cancellation is a terminal state: retrying would hit the same cancel signal)
- **Handling**: User-initiated cancellation is normal; investigate if `cancel_event` fires unexpectedly

### `tool.error` — `ToolError` (base)

- **Raised when**: other tool errors; usually a more specific subclass is raised
- **retryable**: false
- **Extra fields**: `tool_name: str`

## llm.*

### `llm.connection` — `LLMConnectionError`

- **Raised when**: connection fails / times out / 5xx response
- **retryable**: ✅ true
- The HTTP layer has built-in retry; reaching the runtime layer means the retry budget is exhausted
- **Handling**: Check network connectivity; verify the API endpoint configuration; check the provider status page

### `llm.rate_limit` — `LLMRateLimitError`

- **Raised when**: 429 / 529 / provider overload
- **retryable**: ✅ true
- **Extra fields**: `retry_after_ms: int | None` — parsed from the `Retry-After` header (delta-seconds or HTTP-date); LiteLLM provider reads `exc.retry_after` on a best-effort basis
- **Handling**: Check provider quota; upgrade tier; manage request concurrency

### `llm.response` — `LLMResponseError`

- **Raised when**: a non-retryable 4xx (401, 400, etc.) or a non-JSON response body
- **retryable**: false
- **Handling**: Check API key; verify request parameters (model name, message format)

### `llm.model_retry` — `ModelRetryError`

- **Raised when**: `pattern.finalize` validation fails; the finalize loop injects a correction prompt and retries
- **retryable**: false (consumed by the runtime finalize loop; tool executors should not catch this)
- **Extra fields**: `validation_error` passes through the original pydantic `ValidationError`
- **Handling**: No manual handling is typically needed; if `OutputValidationError` is raised, adjust the `output_type` schema

### `llm.error` — `LLMError` (base)

- **Raised when**: other LLM/provider failures; usually a more specific subclass is raised
- **retryable**: false

## user.*

### `user.invalid_input` — `InvalidInputError`

- **Raised when**: the caller supplies invalid input (e.g., empty `input_text`, invalid field value)
- **retryable**: false
- **Handling**: Correct the `RunRequest` parameters

### `user.agent_not_found` — `AgentNotFoundError`

- **Raised when**: `Runtime.run(agent_id=...)` cannot find the specified agent
- **retryable**: false
- **Typical hint**: "Did you mean?" near-match suggestion
- **Handling**: Check the `id` field on the agent config; verify spelling

### `user.error` — `UserError` (base)

- **Raised when**: caller-side mistakes; usually a more specific subclass is raised
- **retryable**: false

## Custom Error Classes

```python
from openagents.errors import RetryableToolError

class MyToolQuotaError(RetryableToolError):
    code = "tool.my_quota"
    # retryable is inherited as True
```

Once declared:

- `RetryToolExecutor` automatically treats it as retryable
- `DefaultRuntime` durable resume captures it automatically
- `ErrorDetails.from_exception` correctly serializes `code = "tool.my_quota"`

**Constraint**: `code` must be dotted (e.g., `tool.my_quota`, matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`) and globally unique (no collision with built-in codes).
