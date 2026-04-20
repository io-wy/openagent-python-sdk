# Plugin Development

This document covers three topics:

1. How the plugin loader finds and instantiates plugins
2. The minimum contract for each plugin / seam type
3. When to write a plugin versus keeping logic in the app-defined protocol layer

## 1. Loader Model

The loader rules are straightforward:

1. If the config has `impl`, import it first.
2. Otherwise, if `type` is set, look it up in the builtin registry or the decorator registry.
3. Instantiate the symbol.
4. Validate capabilities and required methods.

Instantiation is attempted in order:

- `factory(config=config)`
- `factory(config)`
- `factory()`

Class-based plugins are the most stable shape.

## 2. Plugin Sources

A plugin can currently come from three places:

- The builtin registry
- The decorator registry
- An `impl` dotted path in the config

!!! note
    Both the builtin and decorator registries are resolved by name at config-load time.
    The decorator registry is process-local. If the module declaring a decorator is not
    imported before config load, the type name will not resolve.

## 3. Recommended Shape

Prefer class-based plugins that explicitly provide:

- `config`
- `capabilities`
- The required method implementations

Inheriting from `BasePlugin` is not mandatory, but it is more consistent and usually
saves boilerplate.

## 4. Capability and Method Validation

The loader checks two things:

- Whether the required capabilities are declared
- Whether every declared capability has a corresponding method

### Main plugin types

| Type | Required capability | Required methods |
| --- | --- | --- |
| `pattern` | `pattern.execute` | `execute()` |
| `tool` | `tool.invoke` | `invoke()`, `schema()` |
| `runtime` | `runtime.run` | `run()` |
| `session` | `session.manage` | `session()` |
| `events` | `event.emit` | `emit()`, `subscribe()` |
| `tool_executor` | — | `execute()`, `execute_stream()` |
| `context_assembler` | — | `assemble()`, `finalize()` |
| `skills` | — | Plugin-defined (`local` builtin implements discovery / warm-up / injection) |

### `memory`

Memory is slightly special:

- If `memory.inject` is declared, `inject()` must be implemented.
- If `memory.writeback` is declared, `writeback()` must be implemented.

### Optional overrides

The following methods are not part of capability checking, but the builtin runtime calls
them when they exist:

| Type | Optional method | Description |
| --- | --- | --- |
| `pattern` | `resolve_followup()` | Local follow-up short-circuit (return `None` to abstain) |
| `pattern` | `repair_empty_response()` | Empty-response degradation (return `None` to abstain) |
| `tool_executor` | `evaluate_policy()` | Access control check (default: allow all) |

## 5. The Most Important Judgment

Before writing a plugin, decide which category the need belongs to:

- Plugin / seam category
- An existing seam
- App-defined protocol

Rules of thumb:

- If it changes reusable runtime behavior, use a plugin / seam.
- If it expresses your product semantics, put it in the app layer first.

Things that typically belong in the app layer:

- Coding-task envelopes
- Review contracts
- Workflow state machines
- App-specific action summaries
- UI state semantics

## 6. Custom Tool

Write a Tool when you want to give the pattern a named callable capability.

```python
from __future__ import annotations

from typing import Any

from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolPlugin


class EchoTool(ToolPlugin):
    name = "echo_tool"
    description = "Echo text with a prefix."

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        self._prefix = self.config.get("prefix", "echo")

    async def invoke(self, params: dict[str, Any], context: RunContext[Any] | None) -> Any:
        text = str(params.get("text", "")).strip()
        return {"output": f"{self._prefix}: {text}"}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to echo"}
            },
            "required": ["text"],
        }
```

Configuration:

```json
{
  "tools": [
    {
      "id": "echo",
      "impl": "myapp.plugins.EchoTool",
      "config": {"prefix": "custom"}
    }
  ]
}
```

### TypedConfigPluginMixin

Use `TypedConfigPluginMixin` to automatically validate `self.config` (raw dict) into a
typed `self.cfg` (Pydantic model):

```python
from pydantic import BaseModel
from openagents.interfaces.typed_config import TypedConfigPluginMixin

class EchoTool(TypedConfigPluginMixin, ToolPlugin):
    class Config(BaseModel):
        prefix: str = "echo"
        max_length: int = 500

    def __init__(self, config=None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        self._init_typed_config()
        # self.cfg is a validated Config instance
        self._prefix = self.cfg.prefix
        self._max_length = self.cfg.max_length

    async def invoke(self, params, context):
        text = str(params.get("text", "")).strip()[: self.cfg.max_length]
        return {"output": f"{self._prefix}: {text}"}
```

Key points:

- `Config` is a nested `pydantic.BaseModel`.
- `_init_typed_config()` must be called explicitly after `super().__init__()`.
- The mixin must appear **before** the plugin ABC in the MRO, otherwise `super().__init__` cannot reach the ABC.
- Unknown config keys emit a warning only (0.3.x migration safety). A future release may switch to `extra='forbid'`.
- Config validation failures raise `PluginConfigError` with a schema hint.

### Optional: `durable_idempotent` attribute (added in 0.4.x)

Durable runs (`RunRequest.durable=True`) rehydrate from the most recent checkpoint and re-invoke `pattern.execute()` after a retryable error. If your tool has externally-visible side effects (writes files, sends HTTP POSTs, spawns subprocesses, mutates env vars), that re-invocation may replay the tool — which is non-idempotent against outside state.

Declare `durable_idempotent = False` on the class to have the runtime emit a one-shot `run.durable_idempotency_warning` event the first time the tool is invoked inside a durable run. The warning is advisory; it does not block execution.

```python
class MyWriteTool(ToolPlugin):
    durable_idempotent = False  # default True — read-only tools omit this
```

The builtins `WriteFileTool`, `DeleteFileTool`, `HttpRequestTool`, `ShellExecTool`, `ExecuteCommandTool`, `SetEnvTool` are already marked `False`; read-only / query tools keep the `True` default.

## 7. Custom Memory

Write a Memory plugin when you need to control `inject` / `writeback` behavior.

```python
from __future__ import annotations

from typing import Any

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_WRITEBACK
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.run_context import RunContext


class CustomMemory(MemoryPlugin):
    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={MEMORY_INJECT, MEMORY_WRITEBACK})
        self._state_key = self.config.get("state_key", "custom_history")

    async def inject(self, context: RunContext[Any]) -> None:
        history = context.state.get(self._state_key, [])
        context.memory_view["history"] = list(history)

    async def writeback(self, context: RunContext[Any]) -> None:
        history = list(context.state.get(self._state_key, []))
        history.append(
            {
                "input": context.input_text,
                "output": context.state.get("_runtime_last_output", ""),
            }
        )
        context.state[self._state_key] = history
```

## 8. Custom Pattern

Write a Pattern plugin when you need to control the agent loop itself.

The typical shape is:

- `setup()` receives data injected by the runtime.
- Store `RunContext` on `self.context`.
- In `execute()` orchestrate tool calls and model calls.

```python
from __future__ import annotations

from typing import Any

from openagents.interfaces.capabilities import PATTERN_EXECUTE, PATTERN_REACT
from openagents.interfaces.run_context import RunContext


class CustomPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context: RunContext[Any] | None = None

    async def setup(
        self,
        agent_id: str,
        session_id: str,
        input_text: str,
        state: dict[str, Any],
        tools: dict[str, Any],
        llm_client: Any,
        llm_options: Any,
        event_bus: Any,
        **kwargs: Any,
    ) -> None:
        self.context = RunContext[Any](
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            state=state,
            tools=tools,
            llm_client=llm_client,
            llm_options=llm_options,
            event_bus=event_bus,
        )

    async def react(self) -> dict[str, Any]:
        assert self.context is not None
        return {"type": "final", "content": self.context.input_text}

    async def execute(self) -> Any:
        action = await self.react()
        self.context.state["_runtime_last_output"] = action["content"]
        return action["content"]
```

## 9. Custom Skill

Skills are designed for runtime augmentation, not for taking over the entire agent loop.

If you are building a Codex / Claude Code-style host-level skill package, do not push it
into the runtime plugin seam. That kind of capability should be discovered, warmed up,
imported, and executed by the top-level `skills` component.

## 10. Custom Tool Executor

Write a `tool_executor` plugin when the question is "how should this tool be executed".

Common use cases:

- Uniform timeouts
- Parameter validation
- Stream adaptation
- Error normalization

Minimum contract:

- `execute(request) -> ToolExecutionResult`
- `execute_stream(request)` (async generator)

## 11. Custom Tool Policy (overriding `evaluate_policy()`)

When the question is "should this tool be allowed to execute", write a
`ToolExecutorPlugin` subclass and override `evaluate_policy()`. The previously
independent `execution_policy` seam was merged into `tool_executor` in the 2026-04-18
consolidation.

Common use cases:

- File root restrictions
- Allow / deny lists
- Dynamic permission checks
- App-specific policy metadata

Minimum contract:

- `evaluate_policy(request) -> PolicyDecision` (default: allow all)

Example (inheriting `SafeToolExecutor` and overriding `evaluate_policy`):

```python
from openagents.interfaces.tool import ToolExecutionRequest, PolicyDecision
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class MyRestrictedExecutor(SafeToolExecutor):
    ALLOWED_TOOLS = {"read_file", "http_request"}

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        if request.tool_id not in self.ALLOWED_TOOLS:
            return PolicyDecision(
                allowed=False,
                reason=f"tool '{request.tool_id}' not in allowlist",
            )
        return PolicyDecision(allowed=True)
```

Configuration:

```json
{
  "tool_executor": {
    "impl": "myapp.executor.MyRestrictedExecutor"
  }
}
```

References:

- The builtin `filesystem_aware` is the simplest example (wraps one `FilesystemExecutionPolicy`).
- `examples/research_analyst/app/executor.py` shows how to use `CompositePolicy` to combine multiple policy helpers.

## 12. Custom Context Assembler

Write a `context_assembler` plugin when the question is "what context should a run consume".

Common use cases:

- Transcript trimming
- Artifact trimming
- Retrieval packaging
- Task packet assembly
- Summary metadata

Minimum contract:

- `assemble(request, session_state, session_manager) -> ContextAssemblyResult`
- `finalize(request, session_state, session_manager, result) -> result`

Subclassing `TokenBudgetContextAssembler` (from `openagents.plugins.builtin.context.base`)
is recommended — it provides token-budget trimming helpers so strategies only need to
focus on ordering logic:

```python
from openagents.plugins.builtin.context.base import TokenBudgetContextAssembler
from openagents.interfaces.context import ContextAssemblyResult


class MyContextAssembler(TokenBudgetContextAssembler):
    """Assembles context with custom retrieval injection."""

    async def assemble(self, request, session_state, session_manager):
        # 1. Build the message list
        messages = list(session_state.get("transcript", []))

        # 2. Inject app-defined content (e.g. retrieval results)
        retrieval = request.context_hints.get("retrieval_results", [])
        if retrieval:
            messages.append({
                "role": "system",
                "content": "Relevant context:\n" + "\n".join(retrieval),
            })

        return ContextAssemblyResult(
            messages=messages,
            metadata={"retrieval_count": len(retrieval)},
        )

    async def finalize(self, request, session_state, session_manager, result):
        # Optional: update session state after the run
        return result
```

Configuration:

```json
{
  "context_assembler": {
    "impl": "myapp.context.MyContextAssembler",
    "config": {
      "max_input_tokens": 16000,
      "reserve_for_response": 4000
    }
  }
}
```

!!! tip
    `context_assembler` is also the best seam for carrying app-defined context protocols
    — things like task packets, retrieval bundles, and structured handoff state.

## 13. Custom PatternPlugin (resolve_followup + repair_empty_response)

The previously independent `followup_resolver` and `response_repair_policy` seams were
merged into `PatternPlugin` in the 2026-04-18 consolidation. Override two optional
methods on your pattern subclass instead:

### `PatternPlugin.resolve_followup()`

Suitable for local semantic fallbacks:

- What happened in the last turn
- Which tools were used
- Which files were read

Contract:

```python
class MyPattern(ReActPattern):
    async def resolve_followup(self, *, context) -> FollowupResolution | None:
        ...  # default: return None (abstain)
```

The builtin `ReActPattern.execute()` calls this first; returning
`status="resolved"` short-circuits the LLM. Recommended statuses:
`"resolved"` / `"abstain"` / `"error"` (returning `None` is equivalent to abstain).

Full example (short-circuit on a special keyword):

```python
from openagents.plugins.builtin.pattern.react import ReActPattern
from openagents.interfaces.followup import FollowupResolution
from openagents.interfaces.response_repair import ResponseRepairDecision


class SmartReActPattern(ReActPattern):
    async def resolve_followup(self, *, context):
        # Return None to abstain (let the LLM handle it)
        # Return FollowupResolution(status="resolved", output=...) to short-circuit
        if context.input_text.lower() == "status":
            return FollowupResolution(
                status="resolved",
                output="Running.",
            )
        return None  # abstain

    async def repair_empty_response(
        self, *, context, messages, assistant_content, stop_reason, retries
    ):
        # Return None to abstain
        # Return ResponseRepairDecision(status="repaired", output=...) to recover
        return None  # abstain
```

### `PatternPlugin.repair_empty_response()`

Suitable for provider / runtime bad-response degradation:

- Empty responses
- Malformed responses
- Stop with no content
- Explicit diagnostic messages

Contract:

```python
class MyPattern(ReActPattern):
    async def repair_empty_response(
        self, *, context, messages, assistant_content, stop_reason, retries
    ) -> ResponseRepairDecision | None:
        ...  # default: return None (abstain)
```

The builtin pattern calls this once when the provider returns an empty string. Recommended
statuses: `"repaired"` / `"abstain"` / `"error"` (returning `None` is equivalent to abstain).

## 14. App-Defined Middle Protocol

This is the most critical layer for advanced applications.

Many teams think they need a new seam, but what they actually need is "putting the
protocol on the right carrier."

Recommended carriers:

- Caller hints → `RunRequest.context_hints`
- External tracking info → `RunRequest.metadata`
- Durable per-session state → `RunContext.state`
- Per-run scratch state → `RunContext.scratch`
- Assembled context protocol → `RunContext.assembly_metadata`
- Persisted output → `RunArtifact`

This is where high-design-density agents should actually grow.

## 15. Decorator Registration

The following categories support the decorator registry:

- `tool`
- `memory`
- `pattern`
- `runtime`
- `session`
- `event_bus`
- `tool_executor`
- `context_assembler`

Example:

```python
from openagents import context_assembler


@context_assembler(name="trimmed_context")
class TrimmedContextAssembler:
    ...
```

Then in configuration:

```json
{
  "context_assembler": {
    "type": "trimmed_context"
  }
}
```

!!! warning
    Decorator registration is process-local. The module declaring the decorator must be
    imported before config load.

## 16. When Not to Write a Plugin

The following situations typically do not warrant a plugin:

- Task semantics that belong only to your app
- Logic that can be expressed with structured data
- No need for a selector or reuse boundary

If only one product will ever use it, implement the protocol in the app layer first.
Do not rush it into the SDK.

## 17. Plugin Testing Patterns

### Recommended test path

1. Use `Runtime.from_dict({...})` with `provider: "mock"` to construct a minimal runtime.
2. Call `runtime.run()` or `runtime.run_detailed()`.
3. Assert on output, session state, events, or artifacts.

Using `Runtime.from_dict` is one step fewer than `load_config_dict` + `Runtime(config)`,
and gives clearer errors when config parsing fails:

```python
import pytest

from openagents.runtime.runtime import Runtime


@pytest.mark.asyncio
async def test_custom_tool_plugin():
    runtime = Runtime.from_dict(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "test",
                    "name": "test",
                    "memory": {"type": "buffer"},
                    "pattern": {"impl": "tests.fixtures.custom_plugins.CustomPattern"},
                    "llm": {"provider": "mock"},
                    "tools": [
                        {"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}
                    ],
                }
            ],
        }
    )
    result = await runtime.run(agent_id="test", session_id="s1", input_text="hello")
    assert result
```

### Testing a ToolExecutor

```python
@pytest.mark.asyncio
async def test_restricted_executor_blocks_unknown_tool():
    runtime = Runtime.from_dict(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "agent",
                    "name": "agent",
                    "memory": {"type": "buffer"},
                    "pattern": {"type": "react"},
                    "llm": {"provider": "mock"},
                    "tool_executor": {
                        "impl": "myapp.executor.MyRestrictedExecutor",
                    },
                    "tools": [
                        {"id": "dangerous_tool", "impl": "tests.fixtures.custom_plugins.DangerousTool"},
                    ],
                }
            ],
        }
    )
    result = await runtime.run(agent_id="agent", session_id="s1", input_text="run dangerous_tool")
    # MyRestrictedExecutor should have blocked the tool
    assert "not in allowlist" in str(result)
```

### Testing events

```python
@pytest.mark.asyncio
async def test_events_emitted():
    events_received = []

    runtime = Runtime.from_dict({
        "version": "1.0",
        "events": {"type": "async"},
        "agents": [...],
    })
    runtime.event_bus.subscribe("tool.*", lambda e: events_received.append(e))
    await runtime.run(agent_id="agent", session_id="s1", input_text="hello")
    assert any(e.name.startswith("tool.") for e in events_received)
```

Good references in the repo:

- `tests/unit/test_plugin_loader.py` — plugin loading and capability validation
- `tests/unit/test_runtime_orchestration.py` — end-to-end runtime flow
- `tests/fixtures/custom_plugins.py` — minimal implementation templates for each plugin type
- `tests/fixtures/runtime_plugins.py` — custom runtime/session plugin examples
- `examples/production_coding_agent/` — complete production-grade plugin combination

## 18. Typed Config

New plugins should use `TypedConfigPluginMixin` to generate a strongly-typed `self.cfg`
from `self.config`.

The mixin must appear before the plugin ABC in the class definition so that
`super().__init__` still resolves to the ABC:

```python
from pydantic import BaseModel, Field
from typing import Any

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_WRITEBACK
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class BufferMemory(TypedConfigPluginMixin, MemoryPlugin):
    class Config(BaseModel):
        state_key: str = "memory_buffer"
        view_key: str = "history"
        max_items: int | None = Field(default=None, gt=0)

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={MEMORY_INJECT, MEMORY_WRITEBACK},
        )
        self._init_typed_config()

    async def inject(self, context):
        view_key = self.cfg.view_key
        ...
```

Key points:

- `Config` is a nested `pydantic.BaseModel`.
- `_init_typed_config()` must be called explicitly after `super().__init__()`.
- Unknown config keys do not raise; they emit a single warning via the `openagents.interfaces.typed_config` logger to facilitate smooth migration.
- A future 0.4.x release may switch to `extra='forbid'` strict mode.

## 19. Composing Plugins

When writing a combinator plugin (one that loads another plugin internally), use the
public `load_plugin` API:

```python
from openagents.config.schema import ToolExecutorRef
from openagents.plugins.loader import load_plugin


class MyRetryExecutor:
    def __init__(self, config: dict[str, Any] | None = None):
        ...
        inner_ref = ToolExecutorRef(**config["inner"])
        self._inner = load_plugin(
            "tool_executor",
            inner_ref,
            required_methods=("execute", "execute_stream"),
        )
```

!!! warning
    `openagents.plugins.loader._load_plugin` still works but emits `DeprecationWarning`
    and will be removed in a future release. All in-tree combinators (`memory.chain`,
    `tool_executor.retry`, `execution_policy.composite`, `events.file_logging`) have
    migrated to the public API.

## 20. Three-Section Docstring (Spec B WP4)

All builtin plugin classes must include three sections in their class docstring:

```python
class MyMemory(MemoryPlugin):
    """One-line summary ending with a period.

    What:
        2-4 sentences describing what this plugin does and why
        (the user-facing behavior).

    Usage:
        Configuration shape and a 1-2 line example:
        ``{"type": "my_memory", "config": {"key": "value"}}``

    Depends on:
        - ``RunContext.state`` for X
        - sibling plugin ``baz``
        - external resource Y
    """
```

`tests/unit/test_builtin_docstrings_are_three_section.py` enforces this format. Tool
plugins may have a one-line `Usage` / `Depends on`; non-tool plugins should write the
full sections.

## 21. Error Hints and `docs_url` (Spec B WP1)

`OpenAgentsError` (and subclasses) support optional `hint=` / `docs_url=` keyword
arguments. Include them wherever a user is likely to hit a typical error (typos, missing
config, unknown IDs):

```python
from openagents.errors.exceptions import PluginLoadError
from openagents.errors.suggestions import near_match

available = sorted(known_plugins.keys())
guess = near_match(requested, available)
hint_text = (
    f"Did you mean '{guess}'?" if guess else f"Available: {available}"
)
raise PluginLoadError(
    f"Unknown plugin: '{requested}'",
    hint=hint_text,
)
```

`str(exc)` automatically appends a `hint: ...` line. The first line is unchanged to
protect log aggregation.

## 22. Event Taxonomy (Spec B WP2)

Emitted event names should be registered in
`openagents/interfaces/event_taxonomy.py:EVENT_SCHEMAS` and documented in
`docs/event-taxonomy.md` (regenerate with
`uv run python -m openagents.tools.gen_event_doc`). `AsyncEventBus.emit` performs
advisory validation on registered events: missing required payload keys emit a warning
but never raise. Unregistered event names pass through without validation.

## 23. Optional Extras (Spec C)

If your plugin depends on a heavy or optional PyPI package (e.g. `aiosqlite`,
`opentelemetry-api`, `mem0ai`, `mcp`), do not add it to `[project] dependencies`.
Declare it as an optional extra instead:

```toml
[project.optional-dependencies]
sqlite = ["aiosqlite>=0.20.0"]
otel = ["opentelemetry-api>=1.25.0"]
```

Guard the missing import at the module top level with a fail-soft import:

```python
try:
    import aiosqlite
    _HAS_AIOSQLITE = True
except ImportError:
    aiosqlite = None  # type: ignore[assignment]
    _HAS_AIOSQLITE = False
```

Raise `PluginLoadError` with an install hint when the user tries to construct the plugin:

```python
from openagents.errors.exceptions import PluginLoadError

class MyOptionalPlugin(...):
    def __init__(self, config=None):
        if not _HAS_AIOSQLITE:
            raise PluginLoadError(
                "session 'sqlite' requires the 'aiosqlite' package",
                hint="Install the 'sqlite' extra: uv sync --extra sqlite",
            )
        ...
```

This way `openagents.plugins.registry` can import even when the extra is not installed
(the `_BUILTIN_REGISTRY` registers the class symbol, not an instance).

In tests, use `pytest.importorskip("aiosqlite")` at the file top to skip the test when
the extra is absent. The default `uv sync` stays fully green; CI installs the extra and
runs the tests separately.

Add the new file to `[tool.coverage.report] omit` to avoid dragging down the coverage
floor when the optional dependency is not installed.

## 24. Further Reading

- [Developer Guide](developer-guide.md)
- [Seams and Extension Points](seams-and-extension-points.md)
- [Configuration Reference](configuration.en.md)
- [API Reference](api-reference.md)
- [Examples](examples.md)
