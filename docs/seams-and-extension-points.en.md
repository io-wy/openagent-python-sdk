# Seams and Extension Points

This document answers one question:

**When you need new behavior, which layer does it belong in?**

If you answer this question wrong, everything ends up in `Pattern.execute()`, the kernel becomes a mess, and product features erode SDK boundaries.

## 1. Three Categories of Problems

### Kernel Protocol Problems

These involve changing the lowest, most stable protocol objects:

- `RunRequest`
- `RunResult`
- `RunContext`
- `ToolExecutionRequest`
- `ContextAssemblyResult`

This layer should change rarely.

### SDK Seam Problems

These change reusable runtime behavior:

- How a tool executes
- Whether a tool is allowed to execute
- What context a run receives
- Whether a follow-up can be answered locally
- How bad provider responses are degraded

These belong in a seam (or in an overridable method on an existing seam).

### App Protocol Problems

These express your product semantics:

- Coding-task envelope
- Planner contract
- Review state
- Branch ownership
- Artifact taxonomy
- Product status semantics

These should generally **not** become SDK seams.

## 2. Current Seams (8 total)

**Agent capability seams:**

| Seam | Builtin implementations |
|---|---|
| `memory` | `buffer` (default), `window_buffer`, `chain`, `mem0` (requires `[mem0]` extra), `markdown_memory` (human-readable file-backed long-term memory; persisted across sessions as `MEMORY.md` index + per-section files) |
| `pattern` | `react` (default), `plan_execute`, `reflexion` |
| `tool` | No builtins (apps register their own tools) |

**Agent execution seams:**

| Seam | Builtin implementations |
|---|---|
| `tool_executor` | `safe` (default), `retry`, `filesystem_aware` |
| `context_assembler` | `truncating` (default), `head_tail`, `sliding_window`, `importance_weighted` |

**App infrastructure seams:**

| Seam | Builtin implementations |
|---|---|
| `runtime` | `default` |
| `session` | `in_memory` (default), `jsonl_file`, `sqlite` (requires `[sqlite]` extra) |
| `events` | `async` (default), `file_logging`, `otel_bridge` (requires `[otel]` extra), `rich_console` (requires `[rich]` extra) |
| `skills` | `local` (default) |

These are the official extension points in the codebase — **8 total**.

!!! info "Seam consolidation (2026-04-18, 11 → 8)"
    `execution_policy`, `followup_resolver`, and `response_repair_policy` were removed as independent seams. Their functionality was folded into overridable methods on existing seams:

    - `ToolExecutorPlugin.evaluate_policy()` — replaces `execution_policy`
    - `PatternPlugin.resolve_followup()` — replaces `followup_resolver`
    - `PatternPlugin.repair_empty_response()` — replaces `response_repair_policy`

### Migrating from Old Seams

**`execution_policy` → `ToolExecutorPlugin.evaluate_policy()`**

Old (0.2.x):

```json
{
  "tool_executor": {"type": "safe"},
  "execution_policy": {"type": "filesystem", "config": {"root": "/workspace"}}
}
```

New (0.3.x) — use the builtin `filesystem_aware` executor:

```json
{
  "tool_executor": {"type": "filesystem_aware", "config": {"root": "/workspace"}}
}
```

Or write a subclass:

```python
from openagents.interfaces.tool import ToolExecutorPlugin, PolicyDecision, ToolExecutionRequest
from openagents.plugins.builtin.execution_policy import FilesystemExecutionPolicy

class MyExecutor(ToolExecutorPlugin):
    def __init__(self, config=None):
        super().__init__(config=config or {})
        self._fs_policy = FilesystemExecutionPolicy(root="/workspace")

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        decision = self._fs_policy.check(request)
        if not decision.allowed:
            return decision
        # additional custom checks
        return PolicyDecision(allowed=True)
```

**`followup_resolver` → `PatternPlugin.resolve_followup()`**

Old (0.2.x):

```json
{
  "pattern": {"type": "react"},
  "followup_resolver": {"type": "transcript_summary"}
}
```

New (0.3.x) — override in a pattern subclass:

```python
from openagents.interfaces.followup import FollowupResolution
from openagents.plugins.builtin.pattern.react import ReActPattern

class MyReAct(ReActPattern):
    async def resolve_followup(self, *, context):
        last_tools = [r["tool_id"] for r in context.tool_results[-5:]]
        if "what files did you read" in context.input_text.lower():
            files = [r for r in last_tools if "read_file" in r]
            if files:
                return FollowupResolution(
                    status="resolved",
                    output=f"I read: {', '.join(files)}",
                )
        return None  # abstain — let the LLM answer
```

Point to your subclass via `impl` in config:

```json
{
  "pattern": {"impl": "myapp.plugins.MyReAct"}
}
```

**`response_repair_policy` → `PatternPlugin.repair_empty_response()`**

Old (0.2.x):

```json
{
  "response_repair_policy": {
    "type": "default_message",
    "config": {"message": "I was unable to complete the task."}
  }
}
```

New (0.3.x) — override in a pattern subclass:

```python
from openagents.interfaces.response_repair import ResponseRepairDecision

class MyReAct(ReActPattern):
    async def repair_empty_response(
        self, *, context, messages, assistant_content, stop_reason, retries
    ):
        if retries >= 2:
            return ResponseRepairDecision(
                status="repaired",
                output="I was unable to complete the task.",
                reason="max_retries_reached",
            )
        return None  # abstain — let the empty response propagate
```

## 3. Problem → Recommended Layer

| Problem | Recommended location |
| --- | --- |
| Change the agent loop | `pattern` |
| Change memory inject / writeback | `memory` |
| Change a tool's capability | `tool` |
| Change how / whether a tool executes | `tool_executor` (override `evaluate_policy()`) |
| Change transcript / artifact assembly | `context_assembler` |
| Answer "what did you just do?" follow-ups | `PatternPlugin.resolve_followup()` override |
| Degrade bad / empty responses | `PatternPlugin.repair_empty_response()` override |
| Discover / import / execute skill packages | top-level `skills` component |
| Change provider HTTP / SSE adapter | `llm` provider |
| Build teams, mailboxes, schedulers | app / product layer — not SDK core |

## 4. What Each Seam Actually Answers

### `memory`

**What should this run remember, and how?**

Typical scenarios:

- Short-term buffer (`buffer`)
- Sliding window (`window_buffer`)
- Chained memory (`chain`: buffer first, then long-term storage)
- Vector/semantic retrieval (`mem0`, requires `[mem0]` extra)
- Human-readable file-backed long-term memory (`markdown_memory`: user goals / feedback / decisions / references, persisted across sessions as `MEMORY.md` index + per-section files)

Config example:

```json
{
  "memory": {"type": "window_buffer", "config": {"window_size": 10}}
}
```

### `pattern`

**What does the agent loop look like?**

Builtins: `react` (default), `plan_execute`, `reflexion`.

`react` implements the ReAct loop (Thought → Act → Observe). `plan_execute` plans first and then executes step by step. `reflexion` applies self-reflection and correction across multiple turns.

Config example:

```json
{
  "pattern": {"type": "react", "config": {"max_steps": 10}}
}
```

### `tool_executor`

**How should this tool run, and should it be allowed to run at all?**

Typical scenarios:

- Timeout enforcement (`safe`)
- Parameter validation (`safe`)
- Stream passthrough
- Error normalization
- Exponential-backoff retry (`retry`)
- Filesystem allowlist (`filesystem_aware`)
- Dynamic permission evaluation (override `evaluate_policy()`)

Builtins: `safe`, `retry`, `filesystem_aware`.

Config example (combining retry + filesystem_aware):

```json
{
  "tool_executor": {
    "type": "retry",
    "config": {
      "max_attempts": 3,
      "inner": {
        "type": "filesystem_aware",
        "config": {"root": "/workspace", "allow_writes": true}
      }
    }
  }
}
```

### `context_assembler`

**What context should this run actually receive?**

Typical scenarios:

- Transcript trimming (`truncating`: cuts when token limit exceeded)
- Head + tail retention (`head_tail`: preserves first and last messages)
- Sliding window (`sliding_window`)
- Importance-weighted retention (`importance_weighted`: high-score messages kept first)

Config example:

```json
{
  "context_assembler": {
    "type": "head_tail",
    "config": {"max_tokens": 8192, "head_turns": 2, "tail_turns": 8}
  }
}
```

### `runtime`

**How is the runtime initialized and how does it schedule runs?**

Use `default` in almost all cases; only customize when replacing the entire execution engine.

### `session`

**How are transcripts and artifacts persisted?**

- `in_memory` (default): no disk persistence; suitable for testing and short sessions
- `jsonl_file`: append-only NDJSON, replayable on restart
- `sqlite` (requires `[sqlite]` extra): indexed, persistent storage

Config example:

```json
{
  "session": {"type": "jsonl_file", "config": {"path": "sessions/", "compress": true}}
}
```

### `events`

**How is the runtime event stream consumed?**

- `async` (default): in-memory async queue
- `file_logging`: appends every event to an NDJSON audit log
- `otel_bridge` (requires `[otel]` extra): exports as OpenTelemetry spans
- `rich_console` (requires `[rich]` extra): pretty-prints to terminal

All four are `EventBusPlugin` wrappers that can be chained via the `inner` field.

Config example (`rich_console` wrapping `file_logging` wrapping `async`):

```json
{
  "events": {
    "type": "rich_console",
    "config": {
      "inner": {
        "type": "file_logging",
        "config": {"path": "audit.ndjson", "inner": {"type": "async"}}
      },
      "include_events": ["tool.*", "llm.*"],
      "show_payload": false
    }
  }
}
```

### `skills`

**How are host-level skill packages discovered, warmed up, and executed?**

Currently only the `local` implementation is available (loads skill bundles from a local directory). `skills.prepare_session()` is called before each run to inject skill descriptions into the pattern context.

## 5. PatternPlugin Method Overrides in Detail

### `PatternPlugin.resolve_followup()`

```python
async def resolve_followup(
    self, *, context: RunContext[Any]
) -> FollowupResolution | None:
    ...
```

**Answers: Can this follow-up be resolved locally, without calling the LLM again?**

- Return `None` → abstain (continue to LLM loop), equivalent to `FollowupResolution(status="abstain")`
- Return `FollowupResolution(status="resolved", output="...")` → short-circuit; use `output` as the run's `final_output`
- Return `FollowupResolution(status="error", reason="...")` → cause the caller to raise

The builtin `ReActPattern.execute()` calls this method once before starting the LLM loop. When `resolved` is returned, the pattern skips the LLM call entirely and finishes the run immediately.

Typical scenarios:

- "What did you do last turn?" / "Which files did you read?" / "Which tools did you call?"
- Conversational follow-ups that can be answered directly from the transcript

Reference implementation: `examples/production_coding_agent/app/plugins.py`

### `PatternPlugin.repair_empty_response()`

```python
async def repair_empty_response(
    self,
    *,
    context: RunContext[Any],
    messages: list[dict[str, Any]],
    assistant_content: list[dict[str, Any]],
    stop_reason: str | None,
    retries: int,
) -> ResponseRepairDecision | None:
    ...
```

**Answers: When the provider returns an empty or malformed response, what should the system do?**

- Return `None` → abstain (let the empty response propagate), equivalent to `ResponseRepairDecision(status="abstain")`
- Return `ResponseRepairDecision(status="repaired", output="...")` → replace the empty response with `output`
- Return `ResponseRepairDecision(status="error", reason="...")` → cause the caller to raise

Builtin patterns call this method once each time an empty response is encountered. The `retries` parameter indicates how many repair attempts have already been made (starts at 0).

Typical scenarios:

- Diagnosing empty responses (output diagnostics when `stop_reason` is unexpected)
- Handling malformed JSON responses (provide a retry hint)
- Provider-specific degradation (supply a fallback message)

## 6. `ToolExecutorPlugin.evaluate_policy()` in Detail

```python
async def evaluate_policy(
    self, request: ToolExecutionRequest
) -> PolicyDecision:
    ...
```

**Answers: Is this tool allowed to execute for this request?**

The default implementation returns `PolicyDecision(allowed=True)` (allow-all).

`PolicyDecision` fields:

| Field | Type | Description |
|---|---|---|
| `allowed` | `bool` | Whether execution is permitted |
| `reason` | `str` | Denial reason (fill this in — useful for debugging) |
| `metadata` | `dict` | Policy metadata (for audit logs, UI display, etc.) |

**Combining multiple policies:**

```python
from openagents.plugins.builtin.execution_policy import (
    FilesystemExecutionPolicy,
    NetworkAllowlistExecutionPolicy,
    CompositePolicy,
)
from openagents.interfaces.tool import ToolExecutorPlugin, PolicyDecision

class SandboxedExecutor(ToolExecutorPlugin):
    def __init__(self, config=None):
        super().__init__(config=config or {})
        self._policy = CompositePolicy(
            mode="AND",  # all policies must pass
            policies=[
                FilesystemExecutionPolicy(root="/workspace", allow_writes=True),
                NetworkAllowlistExecutionPolicy(
                    allowed_hosts=["api.github.com"],
                    allowed_schemes=["https"],
                ),
            ],
        )

    async def evaluate_policy(self, request) -> PolicyDecision:
        return self._policy.check(request)
```

**Execution policy helper classes:**

| Class | Description |
|---|---|
| `FilesystemExecutionPolicy` | Restricts file operations to a specified root directory |
| `NetworkAllowlistExecutionPolicy` | Host / scheme allowlist for network tool calls |
| `CompositePolicy` | AND / OR composition of multiple sub-policies |

These are standalone helper classes, not plugins. Import them from `openagents.plugins.builtin.execution_policy` and embed them in a custom executor's `evaluate_policy()` method.

Full reference: `examples/research_analyst/app/executor.py`

## 7. When Not to Create a New Seam

Just because you have a protocol does not mean you need a seam.

If a behavior:

- Belongs only to your app
- Is fundamentally structured data, not a runtime control behavior
- Will only be consumed by your custom pattern, tool, or app protocol
- Will not be reused across products

Then it belongs in the app layer.

Recommended carriers:

- `RunRequest.context_hints`
- `RunRequest.metadata`
- `RunContext.state`
- `RunContext.scratch`
- `RunContext.assembly_metadata`
- `RunArtifact.metadata`

## 8. The Healthiest Common Architecture

For most complex single-agent systems:

- `pattern` owns the loop (with optional `resolve_followup` / `repair_empty_response` overrides)
- `memory` owns memory
- `tool_executor` owns execution shape + permission (override `evaluate_policy`)
- `context_assembler` owns the context entry point
- `skills` owns host-level skill package discovery, warmup, and execution
- App-defined protocol lives on context carriers

This enables high-density agent design without seam proliferation.

## 9. Follow-up and Repair Status Semantics

Both pattern methods are intentionally lightweight.

### `PatternPlugin.resolve_followup()`

Return type: `FollowupResolution | None`. Recommended status values:

- `resolved` — use `output` directly
- `abstain` — continue the LLM loop
- `error` — cause the caller to raise

Returning `None` is equivalent to `abstain`.

### `PatternPlugin.repair_empty_response()`

Return type: `ResponseRepairDecision | None`. Recommended status values:

- `repaired` — use `output` directly
- `abstain` — let the empty response propagate
- `error` — cause the caller to raise

Returning `None` is equivalent to `abstain`.

This is intentional. The SDK should not define a large semantic recovery state tree on behalf of every possible product.

## 10. When to Promote from App Protocol to Seam

Only when all of these conditions are simultaneously true:

- The problem recurs across multiple applications
- It affects runtime behavior, not product semantics
- It needs its own selector and lifecycle
- Expressing it via existing carriers would be genuinely awkward
- You are prepared to maintain a builtin default and tests long-term

Otherwise, the right answer is almost always:

**Keep it as an app-defined protocol.**

## 11. Common Anti-Patterns

### Anti-pattern: Stuffing everything into `Pattern.execute()`

Extract outward instead:

- Execution shape + permission → `tool_executor` (override `evaluate_policy()`)
- Context entry → `context_assembler`
- Follow-up fallback → override `PatternPlugin.resolve_followup()` on a `PatternPlugin` subclass
- Provider degradation → override `PatternPlugin.repair_empty_response()` on a `PatternPlugin` subclass

### Anti-pattern: One giant untyped state blob

Split by semantics:

- Durable state → `state`
- Transient state → `scratch`
- Assembled context → `assembly_metadata`
- Caller hint → `context_hints`
- Persisted output → `RunArtifact`

### Anti-pattern: Pushing product infrastructure into the SDK

Queues, approvals, orchestration, UI workflows — none of these belong in the kernel.

## 12. The Safest Evolution Path

1. Build the real requirement in the app layer using `impl` to point at a custom class
2. Validate in a real example or product that the need is stable and reusable
3. Then evaluate whether it deserves promotion to a seam
4. Only then add a builtin / registry entry / documentation

This is the best way to avoid premature abstraction.

## 13. Long-Term Trade-offs

The healthiest long-term trajectory for OpenAgents is:

- **Small kernel**
- **Few strong seams**
- **Rich app protocols**

Not:

- A large seam catalog
- Blurry product boundaries
- All semantics forced into the SDK

## 14. Further Reading

- [Developer Guide](developer-guide.en.md)
- [Configuration Reference](configuration.md)
- [Plugin Development](plugin-development.md)
- [API Reference](api-reference.md)
- [Examples](examples.md)
- [Observability](observability.md)
- [0.2 → 0.3 Migration Guide](migration-0.2-to-0.3.md)
