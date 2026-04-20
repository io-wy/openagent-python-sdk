# Tool Invocation Module Enhancement

**Date:** 2026-04-19
**Status:** Draft
**Reference codebase:** `D:\Project\open-claude-code` (design inspiration)

## Problem

The SDK's tool-invocation machinery is functional but uneven:

1. **Orphan metadata.** `ToolExecutionSpec` declares `concurrency_safe`, `approval_mode`,
   `interrupt_behavior`, `supports_streaming`, `side_effects` — none of these have any
   caller today. Tool authors fill them in and they quietly do nothing.
2. **No batched execution.** The executor processes one request at a time. When the
   LLM asks for five independent file reads, we do them sequentially.
3. **No tool-level cancellation.** `SafeToolExecutor` uses `asyncio.wait_for` (timeout
   only). There is no way for the runtime, a sibling tool, or a user gesture to stop
   an in-flight tool call short of tearing down the whole run.
4. **No long-running / background tools.** A tool that takes minutes (model training,
   large export) must block the agent loop or be hand-rolled with ad-hoc state.
5. **Coarse error taxonomy.** Only `Retryable / Permanent / Timeout / NotFound` exist.
   The retry executor cannot tell rate-limit from auth failure.
6. **Weak per-call observability.** `tool.called` / `tool.succeeded` / `tool.failed`
   don't carry a call_id, so the same tool used twice in a step is not disambiguable
   in the event stream.
7. **No per-call hooks.** `preflight` runs once per run. Tools that need to refresh
   tokens or record custom metrics each call must monkey-patch `invoke`.

Inspiration from open-claude-code: `isConcurrencySafe`-based batch partitioning,
`classifyToolError` taxonomy, `AbortController`-style cancellation, pre/post hooks,
structured call tracing. These ideas map well onto our existing surface — we do not
need to copy their full six-stage pipeline or their product-UX layers.

## Decision

Consolidate all new tool-related behavior onto **`ToolPlugin` methods** (not new
seams). Extend `ToolExecutionRequest` with exactly one field for cancellation. Add
one new builtin tool_executor (`concurrent_batch`). Activate the orphan spec fields
through the new methods rather than adding more.

Hard constraints:

1. No new seam. The plugin loader's top-level slot list does not grow.
2. `ToolExecutorPlugin.evaluate_policy` keeps its job — cross-tool constraints
   (filesystem roots, network allowlist) stay there.
3. Kernel does not implement approval UX. `requires_approval()` emits an event and
   raises; the app layer is responsible for injecting approvals into the next run.
4. No changes to existing `ToolPlugin` methods, `ToolExecutionResult`, or
   `PolicyDecision`. We only add.

## Change Classification

Every change below is labeled as one of:

- 🆕 **NEW** — brand-new field, method, or class
- ⚡ **ACTIVATE** — field/method already exists but has zero callers; we add the caller
- ♻️ **REUSE** — existing surface we depend on but do not modify

## Interface Changes

### `interfaces/tool.py`

#### 🆕 NEW models

```python
class BatchItem(BaseModel):
    """One entry in a batch tool call."""
    params: dict[str, Any] = Field(default_factory=dict)
    item_id: str = Field(default_factory=lambda: str(uuid4()))


class BatchResult(BaseModel):
    """One result in a batched tool call. Preserves item_id and order."""
    item_id: str
    success: bool
    data: Any = None
    error: str | None = None
    exception: OpenAgentsError | None = None


class JobHandle(BaseModel):
    """Returned by invoke_background(). Serialized back to the LLM as the tool result."""
    job_id: str
    tool_id: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    created_at: float  # time.time() at submission


class JobStatus(BaseModel):
    """Returned by poll_job()."""
    job_id: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    progress: float | None = None      # 0..1; None if unknown
    result: Any = None                  # only when succeeded
    error: str | None = None            # only when failed
```

#### 🆕 NEW field on `ToolExecutionRequest`

```python
class ToolExecutionRequest(BaseModel):
    # ... existing fields unchanged ...
    cancel_event: Any | None = None
    # asyncio.Event. None => not cancellable. Arbitrary type (already allowed).
```

#### 🆕 NEW methods on `ToolPlugin`

All have default implementations so existing tools work unchanged.

```python
async def invoke_batch(
    self, items: list[BatchItem], context: RunContext[Any] | None
) -> list[BatchResult]:
    """Batched invocation. Default: sequential loop over invoke().

    Override when the tool can do N items cheaper than N invokes (MCP bulk calls,
    single-syscall multi-file reads, pipelined HTTP). Result list must match the
    input length and item_ids.
    """
    results: list[BatchResult] = []
    for item in items:
        try:
            data = await self.invoke(item.params, context)
            results.append(BatchResult(item_id=item.item_id, success=True, data=data))
        except OpenAgentsError as exc:
            results.append(BatchResult(
                item_id=item.item_id, success=False,
                error=str(exc), exception=exc,
            ))
        except Exception as exc:  # noqa: BLE001
            wrapped = ToolError(str(exc), tool_name=self.tool_name)
            results.append(BatchResult(
                item_id=item.item_id, success=False,
                error=str(wrapped), exception=wrapped,
            ))
    return results


async def invoke_background(
    self, params: dict[str, Any], context: RunContext[Any] | None
) -> JobHandle:
    """Submit a long-running job; return handle immediately.

    Default: NotImplementedError. Only tools that support background work override.
    """
    raise NotImplementedError(f"{self.tool_name} does not support background execution")


async def poll_job(
    self, handle: JobHandle, context: RunContext[Any] | None
) -> JobStatus:
    """Query background job status. Default: NotImplementedError."""
    raise NotImplementedError(f"{self.tool_name} does not support background execution")


async def cancel_job(
    self, handle: JobHandle, context: RunContext[Any] | None
) -> bool:
    """Cancel a background job. Return True if successfully cancelled.
    Default: NotImplementedError."""
    raise NotImplementedError(f"{self.tool_name} does not support background execution")


def requires_approval(
    self, params: dict[str, Any], context: RunContext[Any] | None
) -> bool:
    """Whether this call needs human approval before execution.

    Default reads ``execution_spec().approval_mode``:
      "always"    → True
      "never"     → False
      "inherit"   → False  (app layer decides elsewhere)
    Override to decide per-parameters (e.g. 'rm -rf /' vs 'ls').
    """
    return self.execution_spec().approval_mode == "always"


async def before_invoke(
    self, params: dict[str, Any], context: RunContext[Any] | None
) -> None:
    """Per-call pre-hook. Default no-op.

    Distinct from ``preflight`` (which runs once per run). Use for token refresh,
    per-call metrics, rate-limit token acquisition.
    """


async def after_invoke(
    self,
    params: dict[str, Any],
    context: RunContext[Any] | None,
    result: Any,
    exception: BaseException | None = None,
) -> None:
    """Per-call post-hook. Always runs (success or failure). Default no-op.

    ``result`` is None on failure; ``exception`` is set on failure.
    """
```

### `errors/exceptions.py` — 🆕 5 new subclasses

```python
class ToolValidationError(PermanentToolError):
    """Parameters failed schema / semantic validation."""

class ToolAuthError(PermanentToolError):
    """Authentication / authorization failed. Not retryable without new creds."""

class ToolRateLimitError(RetryableToolError):
    """Third-party rate-limited us. Retryable with backoff."""

class ToolUnavailableError(RetryableToolError):
    """Transient unreachability (DNS, TCP, 5xx). Retryable."""

class ToolCancelledError(PermanentToolError):
    """Cancelled mid-execution via cancel_event. Not retryable."""
```

`RetryToolExecutor.Config.retry_on` default changes from
`["RetryableToolError", "ToolTimeoutError"]` to
`["RetryableToolError", "ToolTimeoutError", "ToolRateLimitError", "ToolUnavailableError"]`.
`ToolValidationError` / `ToolAuthError` / `ToolCancelledError` are never retried
because they inherit from `PermanentToolError`.

### `plugins/builtin/tool_executor/concurrent_batch.py` — 🆕 NEW builtin

```python
class ConcurrentBatchExecutor(ToolExecutorPlugin):
    """Batch-aware executor. Single-request path delegates to inner.

    Config:
      inner: nested executor ref (default: safe)
      max_concurrency: semaphore bound for parallel group (default: 10)

    execute_batch(requests):
      - Partition requests by execution_spec.concurrency_safe
      - concurrency_safe=True  → asyncio.gather with Semaphore
      - concurrency_safe=False → sequential, honoring submit order
      - Return list[ToolExecutionResult] in same order as input
    """

    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "safe"})
        max_concurrency: int = 10

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        return await self._inner.execute(request)

    async def execute_stream(self, request):
        async for chunk in self._inner.execute_stream(request):
            yield chunk

    async def execute_batch(
        self, requests: list[ToolExecutionRequest]
    ) -> list[ToolExecutionResult]:
        ...
```

Registered in `plugins/registry.py` as `"concurrent_batch"` under `tool_executor`.

### `interfaces/tool.py` — `ToolExecutor` protocol extension

```python
@runtime_checkable
class ToolExecutor(Protocol):
    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult: ...
    async def execute_stream(self, request) -> AsyncIterator[dict[str, Any]]: ...

    # 🆕 NEW — optional; loader does not require it
    async def execute_batch(
        self, requests: list[ToolExecutionRequest]
    ) -> list[ToolExecutionResult]: ...
```

`ToolExecutorPlugin` gets a default `execute_batch` that loops `execute` sequentially,
so existing custom executors keep working without overriding.

### `plugins/builtin/runtime/default_runtime.py` — `_BoundTool` extensions

```python
class _BoundTool:
    # existing invoke, invoke_stream, fallback, describe, schema unchanged

    async def invoke_batch(
        self, items: list[BatchItem], context: Any
    ) -> list[BatchResult]:
        """Build N requests → executor.execute_batch → map back to BatchResult."""

    async def invoke_background(self, params, context) -> JobHandle:
        """Delegate to wrapped tool (no executor pipeline — bg tools manage lifecycle)."""
        await self._tool.before_invoke(params, context)
        try:
            handle = await self._tool.invoke_background(params, context)
            return handle
        finally:
            # after_invoke with result=handle, exception=None on success
            ...

    async def poll_job(self, handle, context): ...
    async def cancel_job(self, handle, context): ...
```

The runtime, in session prep, creates an `asyncio.Event` and stashes it at
`ctx.scratch['__cancel_event__']`. `_BoundTool.invoke` copies this into
`ToolExecutionRequest.cancel_event`. External cancellation (from an admin signal,
UI abort button, sibling-run coordination) sets the event.

### `plugins/builtin/tool_executor/safe.py` — SafeToolExecutor cancel integration

Change the inner wait from `asyncio.wait_for(coro, timeout)` to a race between
`coro`, `timeout`, and `cancel_event.wait()`. If `cancel_event` fires first, the
coro is cancelled and we return `ToolCancelledError`. `interrupt_behavior` in
`ToolExecutionSpec` is consulted:

- `"cancel"` (default): fire-and-forget, tool's asyncio task is cancelled
- `"block"`: ignore the cancel request, wait for the tool to finish naturally
  (useful for tools whose mid-operation state must not be abandoned)

### `interfaces/event_taxonomy.py` — 🆕 event schema additions

```python
# Field added to all existing tool.* events (optional):
#   call_id: str    # uuid per call, stable across before/after/succeeded/failed

# New events:
"tool.batch.started":     (batch_id, call_ids, concurrent_count)
"tool.batch.completed":   (batch_id, successes, failures, duration_ms)
"tool.approval_needed":   (tool_id, call_id, params, reason?)
"tool.cancelled":         (tool_id, call_id, reason)
"tool.background.submitted":    (tool_id, call_id, job_id)
"tool.background.polled":       (tool_id, call_id, job_id, status)
"tool.background.completed":    (tool_id, call_id, job_id, status)
```

### `interfaces/pattern.py` — optional helper

```python
class PatternPlugin(BasePlugin):
    async def call_tool_batch(
        self, requests: list[tuple[str, dict[str, Any]]]
    ) -> list[Any]:
        """Convenience: batch N tool calls through the bound-tool layer.

        Groups by tool_id, calls _BoundTool.invoke_batch per group, returns
        results in input order. Patterns that don't use this remain unchanged.
        """
```

## Data Flow

### Single invocation (with cancel support)

```
pattern.call_tool(tool_id, params)
 └── _BoundTool.invoke(params, ctx)
      ├── req.cancel_event = ctx.scratch['__cancel_event__']
      ├── ctx.scratch['__current_call_id__'] = uuid4().hex
      ├── budget check (existing)
      ├── approval gate (if tool.requires_approval(params, ctx))  🆕
      │    └── emit tool.approval_needed; check ctx approvals dict
      │       → None  → raise PermanentToolError("approval pending")
      │       → 'deny'→ raise PermanentToolError("approval denied")
      │       → 'allow' → fall through
      ├── tool.before_invoke(params, ctx)                 🆕
      ├── try:
      │     executor.execute(req)
      │      └── SafeToolExecutor:
      │           ├── tool.validate_params()
      │           └── race(invoke, timeout, cancel_event) 🆕
      │                ├── cancel fires → ToolCancelledError
      │                ├── timeout       → ToolTimeoutError
      │                └── invoke done   → result
      ├── finally:
      │     tool.after_invoke(params, ctx, result, exception)  🆕
      └── return result → pattern unwraps → emit tool.succeeded/failed
```

Ordering rule: `_BoundTool` owns the call_id + approval + before/after hooks
(they're per-call runtime concerns). The executor owns validation + cancel
race + timeout. This keeps executors composable (Retry, FilesystemAware) without
each re-implementing hooks/approval.

### Batched invocation

```
pattern.call_tool_batch([(tool_a, p1), (tool_a, p2), (tool_b, p3)])
 └── group by tool_id:
      ├── _BoundTool[tool_a].invoke_batch([(p1), (p2)])
      │    └── ConcurrentBatchExecutor.execute_batch([req1, req2])
      │         ├── partition by execution_spec.concurrency_safe
      │         ├── safe group → asyncio.gather(sem_limit=max_concurrency)
      │         └── unsafe group → sequential
      └── _BoundTool[tool_b].invoke_batch([(p3)])
 emits:
   tool.batch.started   (batch_id, call_ids, concurrent_count)
   tool.batch.completed (batch_id, successes, failures, duration_ms)
```

### Background jobs

```
LLM calls tool "export" with op=submit
 └── _BoundTool.invoke_background(params, ctx)
      ├── before_invoke
      ├── tool.invoke_background(params, ctx) → JobHandle(status=pending)
      ├── after_invoke(result=handle)
      ├── emit tool.background.submitted
      └── return handle as the tool_succeeded payload
LLM later calls tool with op=poll job_id=...
 └── _BoundTool.poll_job(handle, ctx) → JobStatus
      └── emit tool.background.polled
```

**The runtime does NOT auto-poll.** Polling cadence is a product decision and
belongs to the app layer or the LLM itself.

### Approval

The kernel implementation is non-blocking:

```
_BoundTool.invoke (before execution):
  if tool.requires_approval(params, ctx):
    call_id = uuid.uuid4().hex
    emit tool.approval_needed(tool_id, call_id, params)
    approvals = ctx.run_request.context_hints.get('approvals', {})
    decision = approvals.get(call_id)
    if decision is None:
        raise PermanentToolError("approval pending", tool_name=tool_id,
                                 hint="Inject approvals[call_id]='allow' in next run")
    if decision == 'deny':
        raise PermanentToolError("approval denied", tool_name=tool_id)
    # 'allow' → fall through to normal execution
```

Apps wire up approval UI and retry the run with `context_hints['approvals']`
populated. The kernel never blocks on user input.

## Error Handling

| Error                         | Source                       | Retried by default? | LLM sees as |
|-------------------------------|------------------------------|---------------------|-------------|
| 🆕 `ToolValidationError`      | SafeToolExecutor / tool      | ❌                  | `tool.failed` + hint to fix params |
| 🆕 `ToolAuthError`            | tool raises                  | ❌                  | `tool.failed` + hint to rotate creds |
| 🆕 `ToolRateLimitError`       | tool raises                  | ✅                  | retried, then `tool.failed` |
| 🆕 `ToolUnavailableError`     | tool / MCP connect fails     | ✅                  | retried, then `tool.failed` |
| 🆕 `ToolCancelledError`       | cancel_event fires           | ❌                  | `tool.cancelled` (distinct event) |
| ♻️ `ToolTimeoutError`         | SafeToolExecutor             | ✅                  | retried |
| ♻️ `RetryableToolError`       | tool raises                  | ✅                  | retried |
| ♻️ `PermanentToolError`       | tool raises                  | ❌                  | `tool.failed` |
| ♻️ other `Exception`          | tool raises                  | ❌                  | wrapped in `ToolError`, `tool.failed` |

**Non-catchable exceptions:** `KeyboardInterrupt`, `SystemExit`, and foreign
`asyncio.CancelledError` (not initiated by our `cancel_event`) propagate unchanged.

**How we distinguish "our" cancel from foreign CancelledError:** only catch
`CancelledError` inside the race when `cancel_event.is_set()`. Any other
`CancelledError` bubbles up.

## Observability

Every tool call gets a stable `call_id` (uuid4 hex) threaded through:

- `before_invoke` → receives `context` where `ctx.scratch['__current_call_id__']`
  is set
- all `tool.*` events include the `call_id` in their payload
- `BatchItem.item_id` is surfaced as `call_id` inside the batch's constituent
  calls (so batch/non-batch events look the same from a subscriber's POV)

`tool.succeeded` continues to carry `executor_metadata` (retry counts, timeout_ms)
as today. `tool.batch.completed` adds aggregate `successes` / `failures` /
`duration_ms`.

## Orphan-Field Activation Map

| Orphan field / method                       | Current callers | New caller |
|---------------------------------------------|-----------------|-----------|
| `ToolExecutionSpec.concurrency_safe`        | 0               | `ConcurrentBatchExecutor.execute_batch` partition |
| `ToolExecutionSpec.approval_mode`           | 0               | `ToolPlugin.requires_approval` default |
| `ToolExecutionSpec.interrupt_behavior`      | 0               | `SafeToolExecutor` cancel race behavior |
| `ToolExecutionSpec.supports_streaming`      | 0               | `_BoundTool.invoke_stream` pre-check |
| `ToolExecutionSpec.side_effects`            | 0               | Payload on `tool.succeeded` for audit logs |
| `ToolPlugin.get_dependencies()`             | 0               | Runtime startup validation (can load every named dep) |
| `McpTool.get_available_tools()`             | 0               | `McpTool.invoke_batch` override to do parallel MCP calls |

## Testing Strategy

Coverage floor stays at 90% (`pyproject.toml` `fail_under = 90`). The co-evolution
rule from AGENTS.md applies: source and tests land in the same change.

| Test file | Scope |
|-----------|-------|
| 🆕 `tests/unit/test_tool_plugin_new_methods.py` | Default `invoke_batch` order & error shape; `invoke_background` defaults to `NotImplementedError`; `requires_approval` default reads spec |
| 🆕 `tests/unit/test_concurrent_batch_executor.py` | Partition by `concurrency_safe`; Semaphore limits actual concurrency; output order matches input; partial failure does not poison others |
| 🆕 `tests/unit/test_tool_cancellation.py` | `cancel_event` fires mid-invoke → `ToolCancelledError`; timeout vs cancel distinction; `interrupt_behavior="block"` waits for natural completion; foreign `CancelledError` not swallowed |
| 🆕 `tests/unit/test_tool_approval_flow.py` | `requires_approval=True` without approvals → raises; `approvals[call_id]='allow'` → proceeds; `'deny'` → raises; event emitted |
| 🆕 `tests/unit/test_tool_before_after_hooks.py` | Both hooks called exactly once per invoke; `after_invoke` called on failure with `exception` set |
| 🆕 `tests/unit/test_tool_error_taxonomy.py` | 5 new subclasses inherit correctly; updated `RetryToolExecutor.retry_on` default retries rate-limit/unavailable, not validation/auth/cancelled |
| 🆕 `tests/unit/test_tool_background.py` | `invoke_background` → handle; `poll_job` transitions; `cancel_job` idempotent |
| ⚡ `tests/unit/test_mcp_tool.py` (extend) | `McpTool.invoke_batch` override exercises the connection strategy efficiently |
| ⚡ `tests/unit/test_event_taxonomy.py` (extend) | New events declared; `call_id` required field where specified |

Regression baselines must stay green:
`test_retry_tool_executor.py` / `test_filesystem_policy_and_chain_memory.py` /
`test_composite_execution_policy.py` / `test_network_allowlist_policy.py` /
`test_bound_tool_metadata.py` / `test_shell_exec_tool.py`.

## Documentation

- `docs/api-reference.md` — new methods / fields / errors (Chinese primary)
- `docs/api-reference.en.md` — mirror
- `docs/plugin-development.md` — "implementing a batched tool" / "implementing a
  background tool" / "opting into cancellation" worked examples
- `docs/event-taxonomy.md` — regenerate via `uv run python -m
  openagents.tools.gen_event_doc`

## Out of Scope

The following are explicitly **not** in this spec; they would be separate proposals:

- Approval UI / CLI prompts (app layer)
- Persistent job storage for background tools (app layer; kernel only defines the
  protocol)
- MCP tool schema reflection as independent `ToolPlugin` instances (separate spec
  needed; changes prompt composition)
- OpenTelemetry span hierarchy for tool calls (would extend observability beyond
  simple `call_id`; separate proposal)
- Tool output persistence / content-replacement state (product-layer concern)
- Rule-based permission DSL with source tracking (app-layer; `requires_approval`
  returning a simple bool is all the kernel owes)

## Summary of Code Impact

| File | Change |
|------|--------|
| `openagents/interfaces/tool.py` | +4 models, +1 field, +7 methods |
| `openagents/errors/exceptions.py` | +5 exception subclasses |
| `openagents/interfaces/event_taxonomy.py` | +7 events, +1 field (call_id) across tool.* |
| `openagents/plugins/builtin/tool_executor/concurrent_batch.py` | NEW file |
| `openagents/plugins/builtin/tool_executor/safe.py` | cancel-race & interrupt_behavior |
| `openagents/plugins/builtin/tool_executor/retry.py` | retry_on default expanded |
| `openagents/plugins/builtin/runtime/default_runtime.py` | `_BoundTool` +4 methods; ctx cancel_event wiring |
| `openagents/plugins/registry.py` | register `concurrent_batch` |
| `openagents/interfaces/pattern.py` | +1 optional `call_tool_batch` |
| `tests/unit/test_*.py` | 7 new test files + 2 extensions |
| `docs/*.md` | 3 files updated; 1 regenerated |
