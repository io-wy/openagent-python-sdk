# API Reference

This document summarizes the most important package exports, the runtime surface, and the protocol objects you actually need to care about.

It is not a substitute for reading the source.  
Its purpose is to tell you: **where the current stable API surface is.**

## 1. Package exports

`openagents` currently exports:

### Core entry points

- `AppConfig`
- `LocalSkillsManager`
- `Runtime`
- `RunContext`
- `SessionSkillSummary`
- `SkillsPlugin`
- `load_config`
- `load_config_dict`
- `run_agent`
- `run_agent_detailed`
- `run_agent_detailed_with_config`
- `run_agent_with_config`
- `run_agent_with_dict`

### Streaming API (added in 0.3.0)

- `RunStreamChunk`
- `RunStreamChunkKind`

### Error types (added in 0.3.0)

- `ModelRetryError` — a pattern may raise this to request a model retry
- `OutputValidationError` — raised when structured-output validation exhausts retries

### Skills (added in 0.3.0)

- `LocalSkillsManager`
- `SessionSkillSummary`

### Decorators

- `tool`
- `memory`
- `pattern`
- `runtime`
- `session`
- `event_bus`
- `tool_executor`
- `context_assembler`

### Registry accessors

- `get_tool`
- `get_memory`
- `get_pattern`
- `get_runtime`
- `get_session`
- `get_event_bus`
- `get_tool_executor`
- `get_context_assembler`

### Registry list helpers

- `list_tools`
- `list_memories`
- `list_patterns`
- `list_runtimes`
- `list_sessions`
- `list_event_buses`
- `list_tool_executors`
- `list_context_assemblers`

!!! note "Seam consolidation (2026-04-18)"
    The `execution_policy` / `followup_resolver` / `response_repair_policy` decorator
    and registry triplet has been removed.
    - Tool permission → `ToolExecutorPlugin.evaluate_policy()`
    - Follow-up → `PatternPlugin.resolve_followup()`
    - Empty-response repair → `PatternPlugin.repair_empty_response()`

## 2. Runtime facade

### `Runtime(config: AppConfig, *, _config_path: Path | None = None)`

The external runtime facade. ``config.agents`` must be non-empty; the top-level ``runtime``/``session``/``events``/``skills`` fields may all be omitted — the pydantic schema fills them in with builtin defaults (``default``/``in_memory``/``async``/``local``) and the plugin loader resolves everything through a single path.

Internally holds:

- The app config
- Top-level runtime / session / events / skills components (always loader-resolved)
- A per-(session, agent) plugin bundle cache

```python
Runtime(AppConfig(agents=[...]))       # agents only — rest defaulted by schema
Runtime.from_dict({"agents": [...]})   # minimal dict
Runtime.from_config("agent.json")      # full JSON
```

### `Runtime.from_config(config_path: str | Path) -> Runtime`

Loads a JSON config from disk and constructs a runtime.

### `Runtime.from_dict(payload: dict[str, Any]) -> Runtime`

Constructs a runtime directly from a Python dict.

### `await runtime.run(*, agent_id: str, session_id: str, input_text: str) -> Any`

Compatibility entry point. Returns `RunResult.final_output`.  
Raises on failure.

### `await runtime.run_detailed(*, request: RunRequest) -> RunResult`

Structured entry point.  
Prefer this when building higher-level runtimes, frameworks, or products.

### `async runtime.run_stream(*, request: RunRequest) -> AsyncGenerator[RunStreamChunk, None]`

Streaming entry point (added in 0.3.0). An async generator that yields `RunStreamChunk` objects in sequence.  
The final chunk has `kind=RUN_FINISHED` and carries the complete `RunResult`.

See the [Streaming API deep-dive guide](stream-api.en.md) for details.

### `runtime.run_sync(*, agent_id: str, session_id: str, input_text: str) -> Any`

Synchronous wrapper for `run()`.

### `await runtime.reload() -> None`

Reloads the original config file from disk.  
Only updates agent definitions for future runs; does not hot-swap top-level components.

### `await runtime.reload_agent(agent_id: str) -> None`

Invalidates the cached plugin bundle for one agent across all sessions.

### `runtime.get_session_count() -> int`

Returns the number of currently active sessions.

### `await runtime.list_agents() -> list[dict[str, Any]]`

Returns a minimal list of agents containing only `id` and `name`.

### `await runtime.get_agent_info(agent_id: str) -> dict[str, Any] | None`

Returns:

- The agent's selector configuration
- Whether a loaded plugin instance currently exists

### `await runtime.close_session(session_id: str) -> None`

Closes the plugin bundle for one session. Also cascades into `release_session(session_id)` to release runtime-level shared resources such as the MCP session pool.

### `await runtime.release_session(session_id: str) -> None`

Releases the runtime-owned resources tied to `session_id` (today: the `DefaultRuntime` MCP session pool shared connections) without touching the session's agent plugin bundle. Idempotent; safe to call on a `session_id` that never allocated a pool.

### `await runtime.close() -> None`

Closes the runtime and any closeable downstream resources. For `DefaultRuntime`, this cascades through every MCP session pool and drains their shared connections.

### `runtime.event_bus`

Property. Returns the current event bus instance.

### `runtime.session_manager`

Property. Returns the current session manager instance.

## 3. Sync helpers

### `run_agent(config_path, *, agent_id, session_id="default", input_text) -> Any`

Loads config from a file path and runs synchronously.

### `run_agent_with_config(config, *, agent_id, session_id="default", input_text) -> Any`

Runs synchronously from a pre-loaded config.

### `run_agent_detailed(config_path, *, agent_id, session_id="default", input_text) -> RunResult`

Synchronous detailed run from a file path.

### `run_agent_detailed_with_config(config, *, agent_id, session_id="default", input_text) -> RunResult`

Synchronous detailed run from a pre-loaded config.

### `run_agent_with_dict(payload, *, agent_id, session_id="default", input_text) -> Any`

Synchronous run directly from a Python dict.

### `stream_agent_with_dict(payload, *, request: RunRequest) -> Generator[RunStreamChunk]`

Synchronous streaming from a Python config dict (added in 0.3.0).  
Safe to call from non-async contexts. Cannot be called from inside a running event loop.

### `stream_agent_with_config(config_path, *, request: RunRequest) -> Generator[RunStreamChunk]`

Synchronous streaming from a JSON config file path (added in 0.3.0).  
Internally delegates to `stream_agent_with_dict`.

## 4. Streaming API

### `RunStreamChunkKind`

A `str` enum representing the source event type of a chunk:

| Enum member | Value | Description |
| --- | --- | --- |
| `RUN_STARTED` | `run.started` | Run has begun |
| `LLM_DELTA` | `llm.delta` | Incremental LLM text output |
| `LLM_FINISHED` | `llm.finished` | A single LLM call completed |
| `TOOL_STARTED` | `tool.started` | A tool is about to execute |
| `TOOL_DELTA` | `tool.delta` | Streaming tool output |
| `TOOL_FINISHED` | `tool.finished` | Tool execution finished (success or failure) |
| `ARTIFACT` | `artifact` | An artifact was emitted |
| `VALIDATION_RETRY` | `validation.retry` | Structured output validation failed, retrying |
| `RUN_FINISHED` | `run.finished` | Run complete (terminal chunk) |

### `RunStreamChunk`

| Field | Type | Description |
| --- | --- | --- |
| `kind` | `RunStreamChunkKind` | Chunk type |
| `run_id` | `str` | The corresponding run ID |
| `session_id` | `str` | The owning session |
| `agent_id` | `str` | The owning agent |
| `sequence` | `int` | Monotonically increasing per run; use for disconnect detection |
| `timestamp_ms` | `int` | Unix timestamp in milliseconds |
| `payload` | `dict[str, Any]` | Event-specific data (see table below) |
| `result` | `RunResult \| None` | Only populated on the `RUN_FINISHED` chunk |

**Key payload fields by kind:**

| Kind | Payload fields |
| --- | --- |
| `llm.delta` | `text: str` |
| `llm.finished` | `model: str` |
| `tool.started` | `tool_id: str`, `params: dict` |
| `tool.delta` | `tool_id: str`, `text: str` |
| `tool.finished` | `tool_id: str`, `result: Any` (success) or `error: str` (failure) |
| `artifact` | `name: str`, `kind: str`, `payload: Any` |
| `validation.retry` | `attempt: int`, `error: str` |

!!! tip
    The `sequence` field is guaranteed to be monotonically increasing within a single run.
    Consumers can detect disconnections by checking for gaps in the sequence.

## 5. Structured output

`RunRequest.output_type` accepts a Pydantic model class. The runtime uses it to validate the final output:

```python
from pydantic import BaseModel
from openagents.interfaces.runtime import RunRequest, RunBudget

class Answer(BaseModel):
    value: str
    confidence: float

request = RunRequest(
    agent_id="assistant",
    session_id="s1",
    input_text="What is 2+2?",
    output_type=Answer,
    budget=RunBudget(max_validation_retries=3),
)
result = await runtime.run_detailed(request=request)
answer: Answer = result.final_output
```

**Validation retry loop:**

1. After the pattern finishes executing, the runtime performs Pydantic validation on `final_output`.
2. On failure, the error is injected into `context.scratch["last_validation_error"]` and a `validation.retry` event is emitted on the event bus.
3. The runtime re-enters `pattern.execute()`. The pattern can read from scratch to correct its output.
4. After exceeding `RunBudget.max_validation_retries` (default: 3), `OutputValidationError` is raised.

**Related symbols:**

- `RunRequest.output_type: type[T] | None` — target Pydantic model
- `RunBudget.max_validation_retries: int | None = 3` — maximum validation retry count
- `OutputValidationError` — raised when retries are exhausted; carries `output_type`, `attempts`, `last_validation_error`
- `ModelRetryError` — a pattern can raise this proactively to ask the runtime to retry the current step

## 6. Cost tracking

### `RunUsage` cost fields

| Field | Type | Description |
| --- | --- | --- |
| `cost_usd` | `float \| None` | Total USD cost for the run (computed automatically when the LLM provides token counts) |
| `cost_breakdown` | `dict[str, float]` | Cost broken down by category (e.g. `input`, `output`, `cached_read`) |

### LLM pricing configuration

Add a `pricing` field to the `llm` config to override the built-in price table (in USD per million tokens):

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "pricing": {
      "input": 3.00,
      "output": 15.00,
      "cached_read": 0.30,
      "cached_write": 3.75
    }
  }
}
```

### Built-in price tables

**Anthropic (USD per million tokens):**

| Model | input | output | cached_read | cached_write |
| --- | --- | --- | --- | --- |
| `claude-opus-4-6` | 15.00 | 75.00 | 1.50 | 18.75 |
| `claude-sonnet-4-6` | 3.00 | 15.00 | 0.30 | 3.75 |
| `claude-haiku-4-5` | 0.80 | 4.00 | 0.08 | 1.00 |

**OpenAI / openai_compatible (USD per million tokens):**

| Model | input | output | cached_read |
| --- | --- | --- | --- |
| `gpt-4o` | 2.50 | 10.00 | 1.25 |
| `gpt-4o-mini` | 0.15 | 0.60 | 0.075 |
| `o1` | 15.00 | 60.00 | 7.50 |

!!! note
    When `RunBudget.max_cost_usd` is set, the runtime terminates the run with
    `stop_reason=budget_exhausted` once the accumulated cost exceeds the limit.

## 7. Configuration objects

### `AppConfig`

Key fields:

- `version: str`
- `agents: list[AgentDefinition]`
- `runtime: RuntimeRef`
- `session: SessionRef`
- `events: EventBusRef`
- `skills: SkillsRef`
- `logging: LoggingConfig | None`

### `AgentDefinition`

Key fields:

- `id: str`
- `name: str`
- `memory: MemoryRef`
- `pattern: PatternRef`
- `llm: LLMOptions | None`
- `tool_executor: ToolExecutorRef | None`
- `context_assembler: ContextAssemblerRef | None`
- `tools: list[ToolRef]`
- `runtime: RuntimeOptions`

!!! warning
    `execution_policy` / `followup_resolver` / `response_repair_policy` were removed in the
    2026-04-18 seam consolidation. The strict schema will reject configs that contain these keys.

### `RuntimeOptions`

Fields:

- `max_steps: int = 16`
- `step_timeout_ms: int = 30000`
- `session_queue_size: int = 1000`
- `event_queue_size: int = 2000`

### `LLMOptions`

Fields:

- `provider: str = "mock"` — `"anthropic"` / `"openai_compatible"` / `"mock"`
- `model: str | None`
- `api_base: str | None` — required for `openai_compatible`
- `api_key_env: str | None`
- `temperature: float | None`
- `max_tokens: int | None`
- `timeout_ms: int = 30000`
- `stream_endpoint: str | None`
- `pricing: LLMPricing | None` — overrides the built-in price table
- `retry: LLMRetryOptions | None` — transport-level retry policy (default `None` → providers use built-in defaults: 3 attempts, exponential 500ms→2000ms→5000ms backoff, auto-retries 429/502/503/504 + Anthropic 529 + `httpx.ConnectError`/`ReadTimeout`)
- `extra_headers: dict[str, str] | None` — merged into every request; user keys override provider defaults (e.g. `{"anthropic-beta": "prompt-caching-2024-07-31"}`)
- `reasoning_model: bool | None` — `openai_compatible` only: explicitly mark a reasoning-family model (o1/o3/o4/gpt-5-thinking…). `None` uses a regex on the model name. `True` emits `max_completion_tokens` and drops `temperature`
- `openai_api_style: Literal["chat_completions", "responses"] | None` — `openai_compatible` only: pick the OpenAI API style. `None` auto-detects from `api_base` (trailing `/responses` → `"responses"`, else `"chat_completions"`). Responses API (v2) uses a different payload shape: `messages` → `input` + `instructions`; `max_tokens` → `max_output_tokens`; `response_format` → `text.format` (flattened, no nested `json_schema`). The response `output[]` array contains items with `type` of `message`/`reasoning`/`function_call`. Streaming is currently natively supported only for Chat Completions; the Responses API path falls back to a single non-streaming call inside `complete_stream()`
- `seed` / `top_p` / `parallel_tool_calls` — `openai_compatible` only (forwarded via `extra="allow"`); merged into every request payload

### `LLMRetryOptions`

- `max_attempts: int = 3` (set to `1` to disable retry)
- `initial_backoff_ms: int = 500`
- `max_backoff_ms: int = 5000`
- `backoff_multiplier: float = 2.0`
- `retry_on_connection_errors: bool = True`
- `total_budget_ms: int | None = None` — total wall-clock budget; no new attempts are issued after the budget is exhausted

### `LLMChunk`

Streaming chunk, new field:

- `error_type: Literal["rate_limit", "connection", "response", "unknown"] | None = None` — always `None` on non-error chunks; on error chunks this mirrors the typed `LLMError` hierarchy

### Provider behavior notes

**Anthropic** — `content` preserves `thinking` / `redacted_thinking` blocks (never concatenated into `output_text`); `system` accepts `str` or `list[dict]` (the list form preserves block-level `cache_control`); `cache_control` on `tools` and message content blocks passes through unchanged; `529` is classified as `rate_limit` and included in the retry set.

**OpenAI-compatible** — reasoning-family models (`o\d+(-.*)?` / `gpt-5-thinking*` or `reasoning_model=True`) use `max_completion_tokens` and drop `temperature`; `usage.completion_tokens_details.reasoning_tokens` lands in `LLMUsage.metadata["reasoning_tokens"]` (not double-counted into `output_tokens`); `finish_reason="tool_calls"` is unified to `stop_reason="tool_use"`.

## 8. Runtime protocol

### `RunBudget`

Optional per-run execution limits:

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `max_steps` | `int \| None` | `None` | Maximum step count |
| `max_duration_ms` | `int \| None` | `None` | Maximum execution time (ms) |
| `max_tool_calls` | `int \| None` | `None` | Maximum tool call count |
| `max_validation_retries` | `int \| None` | `3` | Maximum structured output validation retries |
| `max_cost_usd` | `float \| None` | `None` | Maximum cost ceiling (USD) |
| `max_resume_attempts` | `int \| None` | `3` | Maximum automatic resume attempts for durable runs (added in 0.4.x) |

### `RunArtifact`

Artifact emitted by a run:

- `name: str`
- `kind: str = "generic"`
- `payload: Any`
- `metadata: dict[str, Any]`

### `RunUsage`

Run usage aggregation:

- `llm_calls: int`
- `tool_calls: int`
- `input_tokens: int`
- `output_tokens: int`
- `total_tokens: int`
- `input_tokens_cached: int`
- `input_tokens_cache_creation: int`
- `cost_usd: float | None`
- `cost_breakdown: dict[str, float]`

### `RunRequest`

Structured run input:

- `agent_id: str`
- `session_id: str`
- `input_text: str`
- `run_id: str` — auto-generated UUID4 by default
- `parent_run_id: str | None`
- `metadata: dict[str, Any]`
- `context_hints: dict[str, Any]`
- `budget: RunBudget | None`
- `deps: Any`
- `output_type: type[BaseModel] | None` — structured output target type (added in 0.3.0)
- `durable: bool = False` — opt into durable execution: auto-checkpoint at every step boundary and auto-resume from the most recent checkpoint on retryable errors (added in 0.4.x)
- `resume_from_checkpoint: str | None = None` — explicitly resume a new run from a named checkpoint; `DefaultRuntime` skips `context_assembler.assemble()` and `memory.inject()` and rehydrates transcript / artifacts / usage from the checkpoint (added in 0.4.x)

### Durable execution

Durable execution is a runtime-level fault-recovery mechanism, not a new seam. Enable it via:

```python
from openagents.interfaces.runtime import RunBudget, RunRequest

request = RunRequest(
    agent_id="coding-agent",
    session_id="my-session",
    input_text="refactor this module...",
    durable=True,  # auto-checkpoint + auto-resume
    budget=RunBudget(max_resume_attempts=3),
)
result = await runtime.run_detailed(request=request)
```

**Checkpoint granularity**: a checkpoint is written after every successful `llm.succeeded` / `tool.succeeded` event, with `checkpoint_id = f"{run_id}:step:{n}"`. Batched tool calls (`call_tool_batch`) are collapsed to a single step.

**Retryable error classification**: `LLMRateLimitError`, `LLMConnectionError`, `ToolRateLimitError`, `ToolUnavailableError` trigger automatic resume. Every other error (`PermanentToolError`, `ConfigError`, `BudgetExhausted`, `OutputValidationError`) terminates the run immediately.

**Explicit resume**: after a process crash, use a persistent session backend (`jsonl_file` / `sqlite`) and resume in a fresh process:

```python
# Fresh process
request = RunRequest(
    agent_id="coding-agent",
    session_id="my-session",
    input_text="refactor this module...",
    resume_from_checkpoint="abc123:step:7",
)
```

**Events**: durable execution emits six events:
- `run.checkpoint_saved` — after each successful checkpoint
- `run.checkpoint_failed` — create_checkpoint raised (run continues, does not fail)
- `run.resume_attempted` — retryable error caught, about to resume
- `run.resume_succeeded` — state rehydration complete
- `run.resume_exhausted` — reached `max_resume_attempts` cap
- `run.durable_idempotency_warning` — a tool declaring `durable_idempotent=False` was invoked inside a durable run (one-shot per run/tool)

**ToolPlugin.durable_idempotent** (class attribute, default `True`): side-effectful tools (write_file, HTTP, shell subprocess, etc.) should declare `durable_idempotent = False` so runtime emits a one-shot warning when invoked in a durable run. The builtins `WriteFileTool`, `DeleteFileTool`, `HttpRequestTool`, `ShellExecTool`, `ExecuteCommandTool`, `SetEnvTool` are already marked `False`.

### `RunResult[T]`

Structured run output (generic since 0.3.0):

- `run_id: str`
- `final_output: T | None`
- `stop_reason: StopReason`
- `usage: RunUsage`
- `artifacts: list[RunArtifact]`
- `error: str | None`
- `exception: OpenAgentsError | None`
- `metadata: dict[str, Any]`

### `StopReason`

Values:

- `completed`
- `failed`
- `cancelled`
- `timeout`
- `max_steps`
- `budget_exhausted`

## 9. RunContext

`RunContext` is the runtime object actually consumed by patterns and tools.

Key fields:

- `agent_id`
- `session_id`
- `run_id`
- `input_text`
- `deps`
- `state`
- `tools`
- `llm_client`
- `llm_options`
- `event_bus`
- `memory_view`
- `tool_results`
- `scratch`
- `system_prompt_fragments`
- `transcript`
- `session_artifacts`
- `assembly_metadata`
- `run_request`
- `tool_executor`
- `usage`
- `artifacts`

!!! note
    `execution_policy` / `followup_resolver` / `response_repair_policy` attributes were
    removed in the 2026-04-18 seam consolidation. Permission checks are now handled by
    `tool_executor.evaluate_policy()`; follow-up and empty-response recovery are overridable
    methods on `PatternPlugin`.

This is the most important carrier for the app-defined middle protocol.

## 10. Tool execution protocol

### `ToolExecutionSpec`

Execution metadata:

- `concurrency_safe`
- `interrupt_behavior`
- `side_effects`
- `approval_mode`
- `default_timeout_ms`
- `reads_files`
- `writes_files`

### `PolicyDecision`

Policy output:

- `allowed`
- `reason`
- `metadata`

### `ToolExecutionRequest`

Structured tool execution input:

- `tool_id`
- `tool`
- `params`
- `context`
- `execution_spec`
- `metadata`

### `ToolExecutionResult`

Structured tool execution output:

- `tool_id`
- `success`
- `data`
- `error`
- `exception`
- `metadata`

## 11. Context assembly protocol

### `ContextAssemblyResult`

Structured pre-run context:

- `transcript`
- `session_artifacts`
- `metadata`

## 12. Follow-up / response repair protocol

### `FollowupResolution`

Fields:

- `status`
- `output`
- `reason`
- `metadata`

Recommended status values:

- `resolved`
- `abstain`
- `error`

### `ResponseRepairDecision`

Fields:

- `status`
- `output`
- `reason`
- `metadata`

Recommended status values:

- `repaired`
- `abstain`
- `error`

## 13. Session protocol

### `SessionArtifact`

Fields:

- `name`
- `kind`
- `payload`
- `metadata`

### `SessionCheckpoint`

Fields:

- `checkpoint_id`
- `state`
- `transcript_length`
- `artifact_count`
- `created_at`

## 14. Plugin contract

### `ToolPlugin`

Key methods:

- `async invoke(params, context) -> Any`
- `async invoke_stream(params, context)`
- `execution_spec() -> ToolExecutionSpec`
- `schema() -> dict`
- `describe() -> dict`
- `validate_params(params) -> tuple[bool, str | None]`
- `get_dependencies() -> list[str]`
- `async fallback(error, params, context) -> Any`

**Extension methods (2026-04-19)** — all have default implementations; override per-tool as needed:

- `async invoke_batch(items: list[BatchItem], context) -> list[BatchResult]` — default is a sequential loop over `invoke`; override to push batching down (MCP bulk calls, multi-file reads, pipelined HTTP). Result list length, order, and `item_id`s must match the input.
- `async invoke_background(params, context) -> JobHandle` — submit a long-running job; return handle immediately. Default raises `NotImplementedError`.
- `async poll_job(handle, context) -> JobStatus` — query background job status. Default raises `NotImplementedError`.
- `async cancel_job(handle, context) -> bool` — cancel a background job. Default raises `NotImplementedError`.
- `requires_approval(params, context) -> bool` — whether this call needs human approval. Default returns `execution_spec().approval_mode == "always"`.
- `async before_invoke(params, context)` / `async after_invoke(params, context, result, exception=None)` — per-call pre/post hooks (distinct from `preflight`, which runs once per run). `after_invoke` fires on both success and failure paths.

Accompanying pydantic models: `BatchItem` / `BatchResult` / `JobHandle` / `JobStatus` in `openagents.interfaces.tool`.

### `ToolExecutorPlugin`

Key methods:

- `async evaluate_policy(request) -> PolicyDecision` — override to restrict tool execution (default: allow all)
- `async execute(request) -> ToolExecutionResult`
- `async execute_stream(request)`
- `async execute_batch(requests) -> list[ToolExecutionResult]` — default is a sequential loop over `execute`. The builtin `ConcurrentBatchExecutor` partitions by `execution_spec.concurrency_safe` and runs the safe group in parallel under a `Semaphore(max_concurrency)`.

`ToolExecutionRequest` gains `cancel_event: asyncio.Event | None`. `DefaultRuntime` seeds `ctx.scratch['__cancel_event__']` before each run; `_BoundTool.invoke` threads it through to the request; `SafeToolExecutor.execute` runs a 3-way race (invoke vs. timeout vs. cancel). `ToolExecutionSpec.interrupt_behavior == "block"` makes the executor ignore cancel and wait for natural completion.

**New error subclasses (`openagents.errors.exceptions`)**:
`ToolValidationError` / `ToolAuthError` (not retried), `ToolRateLimitError` / `ToolUnavailableError` (retried by default in `RetryToolExecutor`), `ToolCancelledError` (raised by `SafeToolExecutor` when `cancel_event` fires; not retried).

**Pattern convenience**: `PatternPlugin.call_tool_batch(requests: list[tuple[str, dict]]) -> list[Any]` groups calls by `tool_id`, dispatches through `invoke_batch`, and preserves input order. Emits `tool.batch.started` and `tool.batch.completed` events.

### `MemoryPlugin`

Key methods:

- `async inject(context) -> None`
- `async writeback(context) -> None`
- `async retrieve(query, context) -> list[dict[str, Any]]`
- `async close() -> None`

### `PatternPlugin`

Key methods:

- `async setup(...) -> None`
- `async execute() -> Any`
- `async react() -> dict[str, Any]`
- `async emit(event_name, **payload) -> None`
- `async call_tool(tool_id, params=None) -> Any`
- `async call_llm(...) -> str`
- `async compress_context() -> None`
- `add_artifact(...) -> None`
- `async resolve_followup(*, context) -> FollowupResolution | None` — override to answer follow-ups locally (default: abstain)
- `async repair_empty_response(*, context, messages, assistant_content, stop_reason, retries) -> ResponseRepairDecision | None` — override to recover from bad LLM responses (default: abstain)

### `SkillsPlugin`

Key methods:

- `prepare_session(session_id, session_manager) -> dict[str, SessionSkillSummary]`
- `load_references(session_id, skill_name, session_manager) -> list[dict[str, str]]`
- `run_skill(session_id, skill_name, payload, session_manager) -> dict[str, Any]`

### `ContextAssemblerPlugin`

Key methods:

- `async assemble(request, session_state, session_manager) -> ContextAssemblyResult`
- `async finalize(request, session_state, session_manager, result) -> result`

### `RuntimePlugin`

Key methods:

- `async initialize() -> None`
- `async validate() -> None`
- `async health_check() -> bool`
- `async run(...) -> RunResult`
- `async pause() -> None`
- `async resume() -> None`
- `async close() -> None`

### `SessionManagerPlugin`

Key methods:

- `async with session(session_id)`
- `async get_state(session_id) -> dict[str, Any]`
- `async set_state(session_id, state) -> None`
- `async delete_session(session_id) -> None`
- `async list_sessions() -> list[str]`
- `async append_message(session_id, message) -> None`
- `async load_messages(session_id) -> list[dict[str, Any]]`
- `async save_artifact(session_id, artifact) -> None`
- `async list_artifacts(session_id) -> list[SessionArtifact]`
- `async create_checkpoint(session_id, checkpoint_id) -> SessionCheckpoint`
- `async load_checkpoint(session_id, checkpoint_id) -> SessionCheckpoint | None`
- `async close() -> None`

### `EventBusPlugin`

Key methods:

- `subscribe(event_name, handler) -> None`
- `async emit(event_name, **payload) -> RuntimeEvent`
- `async get_history(event_name=None, limit=None) -> list[RuntimeEvent]`
- `async clear_history() -> None`
- `async close() -> None`

## 15. Registry helpers

`get_*` helpers return the class from the decorator registry.  
`list_*` helpers return the names registered in the decorator registry.

They are not a complete substitute for the builtin registry.

## 16. Plugin authoring helpers

Public helpers for custom combinators and pattern authors.

| Symbol | Module | Purpose |
| --- | --- | --- |
| `load_plugin(kind, ref, *, required_methods=())` | `openagents.plugins.loader` | Public sub-plugin loading entry point; used internally by combinators (`memory.chain`, `tool_executor.retry`, `events.file_logging`, etc.) |
| `unwrap_tool_result(result) -> tuple[data, metadata \| None]` | `openagents.interfaces.pattern` | Unpacks a `ToolExecutionResult` returned by `_BoundTool.invoke()` into `(data, executor_metadata)`; passes raw `ToolPlugin.invoke()` return values through unchanged with `metadata=None` |
| `TypedConfigPluginMixin` | `openagents.interfaces.typed_config` | Mixin providing `self.cfg` validated from a nested `Config(BaseModel)`; issues a warning rather than raising on unknown keys |

`openagents.plugins.loader._load_plugin` is retained as a deprecated alias and emits `DeprecationWarning`.

## 17. Error and diagnostic helpers (Spec B WP1 / WP2)

| Symbol | Module | Purpose |
| --- | --- | --- |
| `OpenAgentsError(message, *, hint=None, docs_url=None, ...)` | `openagents.errors.exceptions` | Base exception; adds optional `hint` / `docs_url`. `str(exc)` appends `hint:` / `docs:` lines when set; the first line remains the original message |
| `near_match(needle, candidates, *, cutoff=0.6)` | `openagents.errors.suggestions` | Lightweight "did you mean?" wrapper based on `difflib.get_close_matches`; returns the best match or `None` |
| `EVENT_SCHEMAS` | `openagents.interfaces.event_taxonomy` | Dict mapping declared event names to `EventSchema(name, required_payload, optional_payload, description)`. `AsyncEventBus.emit` logs a warning when required keys are missing; never raises |
| `EventSchema` | `openagents.interfaces.event_taxonomy` | Frozen dataclass for a single event schema |
| `gen_event_doc.render_doc()` / `write_doc(target)` / `main(argv)` | `openagents.tools.gen_event_doc` | Helper for regenerating `docs/event-taxonomy.md` from `EVENT_SCHEMAS` |

## 18. Optional builtin index (Spec C)

These builtins ship under `openagents/plugins/builtin/` but require an
optional extra to construct. Module import always succeeds; instantiation
without the extra raises `PluginLoadError` with an install hint.

| Class | Seam / type key | Module | Extra |
| --- | --- | --- | --- |
| `Mem0Memory` | `memory` / `mem0` | `openagents.plugins.builtin.memory.mem0_memory` | `mem0` |
| `McpTool` | `tool` / `mcp` | `openagents.plugins.builtin.tool.mcp_tool` | `mcp` |
| `SqliteSessionManager` | `session` / `sqlite` | `openagents.plugins.builtin.session.sqlite_backed` | `sqlite` |
| `OtelEventBusBridge` | `events` / `otel_bridge` | `openagents.plugins.builtin.events.otel_bridge` | `otel` |

Install with `uv sync --extra <name>` (or `uv sync --extra all`). Each
module is also added to `[tool.coverage.report] omit` in `pyproject.toml`
so the 92% coverage floor stays intact when the extra is not installed.

### 18.1 `McpTool` lifecycle config (new in 0.3.x)

`McpTool.Config` adds the following fields on top of `server` and `tools`:

- `connection_mode: "per_call" | "pooled"` (default `per_call`). `per_call` preserves the anyio cancel-scope invariant — a dying subprocess cannot cancel the caller's next `await`. `pooled` reuses one long-lived session so N tool calls cost one subprocess spawn.
- `probe_on_preflight: bool` (default `false`). When `true`, `preflight()` opens a throwaway session and calls `list_tools` before the agent loop starts, surfacing unreachable servers as `PermanentToolError`.
- `dedup_inflight: bool` (default `true`). In `per_call` mode, coalesces concurrent `invoke()` calls with the same `(tool, arguments)` so parallel identical calls share a single session.

`ToolPlugin` exposes an optional hook `async def preflight(self, context) -> None`. The default is a no-op, so existing tools are unaffected. `DefaultRuntime` invokes every tool's `preflight` once per session before the first agent turn. `McpTool` overrides it to verify the `mcp` extra is importable, that the stdio `command` is on `PATH` (via `shutil.which`), and that any HTTP URL is well-formed. Failures raise `PermanentToolError`, which the runtime translates into a `RunResult` with `stop_reason=failed` — the pattern loop never runs.

Structured events emitted: `tool.preflight`, `tool.mcp.preflight`, `tool.mcp.connect`, `tool.mcp.call`, `tool.mcp.close`. Payloads carry only identifiers, status, and timing; they never include tool arguments, results, or request headers.

## 19. Further reading

- [Developer Guide](developer-guide.md)
- [Seams and Extension Points](seams-and-extension-points.md)
- [Configuration Reference](configuration.md)
- [Plugin Development](plugin-development.md)
- [Examples](examples.md)
- [Streaming API Deep-Dive](stream-api.en.md)
