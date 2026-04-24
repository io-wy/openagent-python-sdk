# Developer Guide

This guide is about **getting the architectural layering right with OpenAgents**.

If there is one thing to remember, it is this:

**Do not push product semantics into the kernel.**

The right division of responsibility is:

- Keep the kernel protocol as stable as possible
- Keep SDK seams few and hard
- Let the app invent its own middle protocol

## 1. Project Boundaries

OpenAgents is a **single-agent runtime kernel**.

This means:

- One `RunRequest` maps to exactly one `agent_id`
- One `Runtime.run()` executes exactly one agent run
- Session, memory, pattern, and tool bundles are all organized around this single-agent model

It also means the kernel deliberately does **not** own:

- Multi-agent team orchestration
- Subagent delegation
- Mailbox / background jobs
- Approval UX
- Product workflow state machines

These capabilities belong in layers above the SDK.

## 2. Three-Layer Structure

### Kernel Protocol

The lowest and most stable set of objects in the runtime:

- `RunRequest`
- `RunResult`
- `RunUsage`
- `RunArtifact`
- `RunContext`
- `ToolExecutionRequest`
- `ToolExecutionResult`
- `ContextAssemblyResult`
- `SessionArtifact`
- `SessionCheckpoint`

These objects should stay small, explicit, and free of product bias.

### SDK Seams

The fixed set of control seams that the runtime exposes — **8 total** (2026-04-18 consolidation: 11 → 8):

- **Capability seams**
  - `memory`
  - `pattern`
  - `tool`
- **Execution seams**
  - `tool_executor` (tool execution + built-in policy check via `evaluate_policy()`)
  - `context_assembler`
- **App infrastructure seams**
  - `runtime`
  - `session`
  - `events`
  - `skills`

!!! note "Seam consolidation (0.3.0)"
    `execution_policy`, `followup_resolver`, and `response_repair_policy` were removed as independent seams on 2026-04-18 and merged into overridable methods on pattern/executor:

    - `ToolExecutorPlugin.evaluate_policy()` — tool permission check (default: allow-all)
    - `PatternPlugin.resolve_followup()` — answer follow-ups locally (default: abstain / None)
    - `PatternPlugin.repair_empty_response()` — degrade bad/empty responses (default: abstain / None)

    Migration details and code examples are in [`seams-and-extension-points.en.md`](seams-and-extension-points.en.md) §2.

#### `PatternPlugin` Overridable Methods

`PatternPlugin` subclasses can extend behavior by overriding these methods, without creating a new seam:

```python
async def resolve_followup(
    self, *, context: RunContext[Any]
) -> FollowupResolution | None:
    """Answer a follow-up locally. Return None to abstain (let the LLM handle it)."""
    return None

async def repair_empty_response(
    self,
    *,
    context: RunContext[Any],
    messages: list[dict[str, Any]],
    assistant_content: list[dict[str, Any]],
    stop_reason: str | None,
    retries: int,
) -> ResponseRepairDecision | None:
    """Handle a bad or empty provider response. Return None to abstain (let it propagate)."""
    return None
```

Both methods are called automatically by the builtin `ReActPattern.execute()`; custom pattern subclasses should also call them at the appropriate points.

#### `ToolExecutorPlugin.evaluate_policy()`

`ToolExecutorPlugin` subclasses can override this method to implement tool execution policy:

```python
async def evaluate_policy(
    self, request: ToolExecutionRequest
) -> PolicyDecision:
    """Override to restrict tool execution. Default: allow all."""
    return PolicyDecision(allowed=True)
```

The base class `execute()` and `execute_stream()` both call `evaluate_policy()` before invoking any tool; if `allowed=False`, they short-circuit with an error result.

### App-Defined Middle Protocol

This is where high-density agent design should actually happen.

Examples:

- Coding-task envelope
- Review contract
- Retrieval plan
- Permission state
- Artifact taxonomy
- Action summary

OpenAgents does not build all of these into SDK seams. Instead, it gives you carriers to hold them.

## 3. The Run Lifecycle

The builtin runtime executes in this order:

1. `Runtime.from_config()` or `Runtime.from_dict()` wires top-level components
2. `Runtime.run_detailed()` locates the target agent
3. `Runtime` calls top-level `skills.prepare_session()` to warm up skill descriptions for the session
4. `Runtime` creates or reuses the `(session_id, agent_id)` plugin bundle
5. `DefaultRuntime.run()` emits events and acquires the session lock
6. `context_assembler.assemble()` builds the transcript / artifacts / metadata
7. Runtime budget is injected into the pattern
8. Tools are rebound through `tool_executor` (whose `evaluate_policy()` handles permission checks)
9. `pattern.setup()` constructs the `RunContext`
10. `memory.inject()`
11. `pattern.execute()`
12. `memory.writeback()`
13. Transcript and artifacts are persisted
14. `context_assembler.finalize()`
15. `RunResult` is returned

Two important cache lifetimes to keep straight:

- Agent plugin bundles are keyed by `(session_id, agent_id)`
- Builtin LLM clients are keyed by `agent.id` only

The plugin lifecycle and the LLM client lifecycle are not the same thing.

## 4. New Capabilities in 0.3.0

### Typed Structured Output

`RunRequest.output_type` accepts a `pydantic.BaseModel` subclass. When set, the runtime calls `PatternPlugin.finalize()` on the raw output after `pattern.execute()` completes, running `model_validate()`. On failure it raises `ModelRetryError`, which triggers a validation retry loop.

```python
from pydantic import BaseModel
from openagents import Runtime, RunRequest

class ReviewReport(BaseModel):
    verdict: str
    issues: list[str]
    score: float

request = RunRequest(
    agent_id="reviewer",
    session_id="s1",
    input_text="Review this PR...",
    output_type=ReviewReport,
)
result = await runtime.run_detailed(request)
report: ReviewReport = result.final_output
```

The retry limit is controlled by `RunBudget.max_validation_retries` (default: 3). Once exceeded, `PermanentToolError` is raised.

### Cost Tracking

`RunUsage.cost_usd` accumulates the total USD cost for the current run (non-None only when the provider reports it).

`RunBudget.max_cost_usd` sets a hard cost ceiling — exceeding it causes `call_llm()` to raise `BudgetExhausted`.

```python
from openagents.interfaces.runtime import RunBudget

request = RunRequest(
    agent_id="coder",
    session_id="s1",
    input_text="...",
    budget=RunBudget(max_cost_usd=0.10, max_steps=20),
)
result = await runtime.run_detailed(request)
print(f"cost: ${result.usage.cost_usd:.4f}")
```

If the provider does not report cost data, `cost_usd` remains `None` and budget checks are silently skipped (a single `budget.cost_skipped` event is emitted to notify callers).

### Streaming API

`Runtime.run_stream()` returns an `AsyncIterator[RunStreamChunk]` that pushes incremental progress:

```python
async for chunk in runtime.run_stream(request):
    match chunk.kind:
        case RunStreamChunkKind.LLM_DELTA:
            print(chunk.payload["delta"], end="", flush=True)
        case RunStreamChunkKind.TOOL_STARTED:
            print(f"\n[tool: {chunk.payload['tool_id']}]")
        case RunStreamChunkKind.RUN_FINISHED:
            result = chunk.result
            break
```

`RunStreamChunkKind` values: `run.started`, `llm.delta`, `llm.finished`, `tool.started`, `tool.delta`, `tool.finished`, `artifact`, `validation.retry`, `run.finished`.

### CLI Tools

The `openagents` CLI provides three subcommands (requires `[cli]` extra or standalone install):

```bash
openagents schema                   # Print config schemas for all registered builtin plugins
openagents validate config.json     # Validate an agent config file
openagents list-plugins             # List all currently registered plugin types
```

### Observability and Logging

The SDK provides two debug output channels:

#### Python stdlib logging (`openagents.*` namespace)

```python
from openagents.observability import configure, LoggingConfig

configure(LoggingConfig(level="DEBUG", pretty=True))
```

Or configure in `agent.json`:

```json
{
  "logging": {
    "auto_configure": true,
    "level": "INFO",
    "per_logger_levels": {"openagents.llm": "DEBUG"},
    "pretty": true,
    "redact_keys": ["api_key", "authorization"]
  }
}
```

**Environment variable overrides** (useful for CI or one-off debugging):

| Variable | Example |
|---|---|
| `OPENAGENTS_LOG_AUTOCONFIGURE` | `1` |
| `OPENAGENTS_LOG_LEVEL` | `DEBUG` |
| `OPENAGENTS_LOG_LEVELS` | `openagents.llm=DEBUG,openagents.events=INFO` |
| `OPENAGENTS_LOG_PRETTY` | `1` |
| `OPENAGENTS_LOG_STREAM` | `stderr` |
| `OPENAGENTS_LOG_INCLUDE` | `openagents.llm,openagents.events` |
| `OPENAGENTS_LOG_EXCLUDE` | `openagents.events.file_logging` |
| `OPENAGENTS_LOG_REDACT` | `api_key,authorization` |
| `OPENAGENTS_LOG_MAX_VALUE_LENGTH` | `500` |

!!! warning
    `pretty: true` requires the `[rich]` extra: `pip install io-openagent-sdk[rich]`. Without it, `RichNotInstalledError` is raised.

#### Runtime Event Stream

`file_logging` (NDJSON), `otel_bridge` (OTel spans), and `rich_console` (pretty terminal output) are all `EventBusPlugin` wrappers that can be chained via the `inner` field:

```json
{
  "events": {
    "type": "rich_console",
    "config": {
      "inner": {
        "type": "file_logging",
        "config": {
          "path": "events.ndjson",
          "inner": {"type": "async"}
        }
      },
      "include_events": ["tool.*", "llm.succeeded"],
      "show_payload": true,
      "redact_keys": ["api_key"]
    }
  }
}
```

## 5. TypedConfigPluginMixin

All builtin plugins use `TypedConfigPluginMixin` for config validation. Usage:

```python
from pydantic import BaseModel
from openagents.interfaces.typed_config import TypedConfigPluginMixin
from openagents.interfaces.tool import ToolExecutorPlugin

class MyExecutor(TypedConfigPluginMixin, ToolExecutorPlugin):
    class Config(BaseModel):
        timeout_ms: int = 5000
        strict_mode: bool = False

    def __init__(self, config=None):
        super().__init__(config=config or {})
        self._init_typed_config()  # must be called at the end of __init__
        # then access validated config via self.cfg.timeout_ms
```

!!! note
    Unknown config keys emit a **warning** log entry but are not rejected (a 0.3.x compatibility decision). A future major release may switch to `extra='forbid'`.

## 6. New Builtins (0.3.x)

| Location | Key | Description |
| --- | --- | --- |
| `tool_executor` | `retry` | Wraps another executor; exponential-backoff retry by error category |
| `tool_executor` | `filesystem_aware` | Bundles `FilesystemExecutionPolicy` (replaces old `execution_policy: filesystem`) |
| `session` | `jsonl_file` | Append-only NDJSON file; replayable on restart |
| `events` | `file_logging` | Wraps inner event bus + appends every event to an NDJSON audit log |
| execution_policy helper (not a seam) | `CompositePolicy` | AND / OR composition of sub-policy lists, for embedding in a custom executor's `evaluate_policy` |
| execution_policy helper (not a seam) | `NetworkAllowlistExecutionPolicy` | Host/scheme allowlist for `http_request`-category tools |

## 7. Using State Carriers Correctly

Most middle protocols do not need a new seam. They need to be placed on the right carrier.

### `RunRequest.context_hints`

For caller-supplied run hints, such as:

- `task_id`, `workspace_root`, `interaction_mode`, `requested_depth`

Use this when the information is known to the caller at request time.

### `RunRequest.metadata`

For external tracing and observability, such as:

- Trace IDs, upstream request IDs, source, user ID

Use this when the primary consumer is a monitoring or tracing system.

### `RunContext.state`

For durable state that must survive across steps and turns, such as:

- Protocol state machines, planner state, session task state, persisted memory

### `RunContext.scratch`

For transient state that is only needed within a single run, such as:

- Pending tool IDs, current plan drafts, temporary parse results

### `RunContext.assembly_metadata`

For data produced by `context_assembler` and consumed by patterns, skills, or tools, such as:

- Context packets, transcript trimming statistics, retrieval selection metadata

### `RunArtifact`

For named outputs actually produced by the run, such as:

- Delivery reports, patch plans, generated files, research notes

If a result may be consumed by the session, UI, or an upstream system, do not hide it only in `state`.

## 8. Deciding Where a New Protocol Belongs

Work through these questions in order.

### Does it change how tools are executed?

Use `tool_executor`.

Examples: timeout, parameter validation, stream passthrough, error normalization.

### Does it determine whether a tool can execute?

Override `ToolExecutorPlugin.evaluate_policy()` in a subclass, or use the builtin `filesystem_aware` executor.

Examples: allow/deny rules, filesystem root restrictions, dynamic permission checks.

### Does it determine what context the run receives?

Use `context_assembler`.

Examples: transcript trimming, artifact trimming, retrieval packaging, task packet assembly.

### Is it answering a "what did you just do?" follow-up?

Override `PatternPlugin.resolve_followup()` in a pattern subclass.

### Is it recovering from a bad or empty provider response?

Override `PatternPlugin.repair_empty_response()` in a pattern subclass.

### Is it purely your product's task semantics?

Do not add a seam. Instead, model it as an app protocol on:

- `context_hints`, `state`, `scratch`, `assembly_metadata`, `skill_metadata`, `RunArtifact`

## 9. The Healthy Architecture for High-Density Agents

For most complex single-agent systems, the healthiest combination is:

- `pattern` owns the agent loop (with optional `resolve_followup` / `repair_empty_response` overrides)
- `memory` owns memory read/write
- `tool_executor` owns execution shape + permission (`evaluate_policy`)
- `context_assembler` owns the context entry point
- `skills` owns host-level skill package discovery, warmup, and execution
- App-defined protocol lives on context carriers

This is sufficient for very complex agents without seam proliferation.

## 10. When Is a New Seam Worth It?

Only when all of these conditions are simultaneously true:

- The problem recurs across multiple applications
- It affects runtime behavior, not just product semantics
- It needs its own selector and lifecycle
- Expressing it via existing carriers would be awkward
- You are prepared to maintain a builtin default and tests long-term

If these conditions are not all met, the right answer is almost always:

**Model it as an app-defined protocol first.**

## 11. Hot Reload and Lifecycle

`Runtime.reload()` semantics:

- Re-parses the config file
- Updates agent definitions for future runs
- Evicts caches for removed agents
- Invalidates LLM clients for changed agents
- Does **not** hot-swap top-level `runtime` / `session` / `events`

This reinforces the principle: top-level runtime machinery is a stable container and should not absorb product infrastructure.

## 12. Common Anti-Patterns

### Anti-pattern: Stuffing all logic into `Pattern.execute()`

Extract outward instead:

- Execution shape + permission → `tool_executor` (override `evaluate_policy()`)
- Context entry → `context_assembler`
- Follow-up fallback → override `PatternPlugin.resolve_followup()`
- Response degradation → override `PatternPlugin.repair_empty_response()`

### Anti-pattern: One giant untyped `state` dict

Split by semantics:

- Durable state → `state`
- Transient state → `scratch`
- Assembled context → `assembly_metadata`
- Caller hint → `context_hints`
- Persisted output → `RunArtifact`

### Anti-pattern: Promoting product semantics into seams too early

If only your app uses it, keep it out of the SDK.

### Anti-pattern: Pushing product infrastructure into the SDK

Queues, approvals, orchestration, UI workflows — these belong above the kernel.

## 13. Recommended Evolution Strategy

The safest evolution path:

1. Implement the real requirement in the app layer using existing seams and carriers
2. Validate in a real example or product that the need is stable
3. Then evaluate whether it deserves promotion to a seam
4. Only then consider builtin / registry / documentation

This prevents seam proliferation and kernel bloat.

## 14. Further Reading

- [Seams and Extension Points](seams-and-extension-points.en.md)
- [Configuration Reference](configuration.md)
- [Plugin Development](plugin-development.md)
- [API Reference](api-reference.md)
- [Examples](examples.md)
- [Streaming API](stream-api.md)
- [Observability](observability.md)
- [0.2 → 0.3 Migration Guide](migration-0.2-to-0.3.md)
- [Error reference (errors.en.md)](errors.en.md) — dotted codes, retryable classification, and recommended handling per exception class
