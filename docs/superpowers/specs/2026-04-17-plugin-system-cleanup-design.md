# Plugin System Cleanup & Consistency — Design

- Status: drafted via brainstorm 2026-04-17, awaiting user review
- Scope: single spec, single implementation plan, single PR-shape
- Position in roadmap: this is **Spec A** of a 3-spec sequence agreed during brainstorm:
  - **A (this spec)**: cleanup + consistency — close known follow-ups + Config(BaseModel) refactor
  - **B (later)**: quality hardening + observability across all builtins
  - **C (later)**: selective new builtins (sqlite session, OTel events bridge)
- Non-goals: kernel protocol changes, new seams, new builtins, behavior changes beyond the explicit event-payload addition, hardening of edge cases, test-coverage expansion beyond what the refactor itself requires.

## 1. Motivation

The 0.3.x line accumulated three classes of debt that the previous expansion spec (`2026-04-17-builtin-plugins-expansion-design.md` §13.3) explicitly deferred:

1. **Private API leakage** — `openagents/plugins/loader.py:_load_plugin` is a private symbol but is reached into by four combinator builtins (`memory/chain`, `tool_executor/retry`, `execution_policy/composite`, `events/file_logging`). External users authoring their own combinator plugins must do the same underscore violation.

2. **Lost executor metadata** — `_BoundTool.invoke` in `openagents/plugins/builtin/runtime/default_runtime.py` returns only `result.data` and discards `ToolExecutionResult.metadata` before pattern's `call_tool` emits `tool.*` events. Retry attempt counts, executor timeouts, policy decisions never reach observability — `examples/research_analyst` had to fall back to indirect proof of retry firing (errata §13.2).

3. **Config style drift** — 0.3.x additions (`RetryToolExecutor`, `CompositeExecutionPolicy`, `RuleBasedFollowupResolver`, `JsonlFileSessionManager`, `FileLoggingEventBus`, `StrictJsonResponseRepairPolicy`, all four `context_assembler` builtins) adopt `class Config(BaseModel)` + `model_validate`. Pre-existing builtins (`SafeToolExecutor`, `FilesystemExecutionPolicy`, `BufferMemory`, `WindowBufferMemory`, all three patterns, `BasicFollowupResolver`, `BasicResponseRepairPolicy`, `InMemorySessionManager`, `AsyncEventBus`, `LocalSkillsManager`, `DefaultRuntime`, `HttpRequestTool`, `McpTool`) still use `self.config.get(...)` raw-dict access. The two styles co-exist with no migration path.

A clean fix for all three is structurally small (~20 files), low-risk, and unlocks observability that downstream B and C work depends on.

### 1.1 Audit confirmation

Before drafting, the full `openagents/plugins/` tree was audited for hidden simplifications under three criteria: (a) hard placeholders (`pass`-only methods, `NotImplementedError`, `# TODO`, docstrings claiming behavior the code lacks); (b) semantic inconsistencies between sibling builtins; (d) docstring-vs-implementation drift.

- (a) **None found.** The single `NotImplementedError` (`context/base.py:52`) is a proper abstract method that subclasses (`HeadTailContextAssembler`, `SlidingWindowContextAssembler`) implement.
- (b) The Config-style drift listed above; nothing else.
- (d) **None found.** Docstrings accurately describe intent.

This gives the spec a tight, well-bounded scope rather than an open-ended audit pass.

## 2. High-level plan

Four work packages, mutually independent and orderable in any sequence.

| # | work package | files touched (approx) |
|---|---|---|
| 1 | Public loader API — `_load_plugin` → `load_plugin` + 4 combinator updates + deprecation alias | 5 |
| 2 | `_BoundTool` metadata passthrough → `tool.succeeded` event payload extension | 3 (+ stream projection) |
| 3 | Introduce `TypedConfigPluginMixin` in `openagents/interfaces/typed_config.py` | 1 (new) |
| 4 | Config(BaseModel) refactor for 13 non-tool builtins + 2 config-bearing tools | 15 |

**Approach principles:**

- **Composition, not inheritance.** Mixin (work package #3) is added laterally; no existing plugin ABC is altered.
- **No kernel protocol changes.** `RunRequest` / `RunResult` / `RunContext` / `ToolExecutionRequest` / `ToolExecutionResult` are untouched. The `tool.succeeded` event payload gains a field — events are SDK seam surface, not kernel protocol.
- **Backward-compatible by default.** Deprecation alias keeps `_load_plugin` callable for one release. Config refactor keeps `__init__(self, config)` signature unchanged. Unknown config keys produce a warning, not an error.
- **No new exception types.** Reuses the existing exception tree (`PluginLoadError`, `CapabilityError`, `ToolError`, `ToolTimeoutError`).
- **Patch release.** Lands on 0.3.x; not a breaking cut. Strict `extra='forbid'` defers to ≥0.4.0.

## 3. Component specs

### 3.1 Public loader API (`openagents/plugins/loader.py`)

Add a public `load_plugin` function that delegates to the existing private `_load_plugin`. Keep `_load_plugin` as a deprecation alias for one release.

```python
def load_plugin(
    kind: str,
    ref: PluginRef,
    *,
    required_methods: tuple[str, ...] = (),
) -> Any:
    """Load a child plugin from a PluginRef.

    Used by combinator builtins that compose other plugins
    (memory.chain, tool_executor.retry, execution_policy.composite,
    events.file_logging) and by external custom combinators.
    """
    return _load_plugin(kind, ref, required_methods=required_methods)
```

The existing `_load_plugin` is renamed to `_load_plugin_impl` (module-private worker). Both `load_plugin` and the deprecated `_load_plugin` shim delegate to it:

```python
def _load_plugin_impl(kind, ref, *, required_methods=()):
    # ... existing body ...

def load_plugin(kind, ref, *, required_methods=()):
    return _load_plugin_impl(kind, ref, required_methods=required_methods)

def _load_plugin(kind, ref, *, required_methods=()):
    warnings.warn(
        "openagents.plugins.loader._load_plugin is deprecated; "
        "use openagents.plugins.loader.load_plugin",
        DeprecationWarning,
        stacklevel=2,
    )
    return _load_plugin_impl(kind, ref, required_methods=required_methods)
```

This keeps the warning logic isolated from the actual loading logic and avoids any sentinel-flag bookkeeping.

**Combinator callsite updates** (4 files, mechanical):
- `openagents/plugins/builtin/memory/chain.py:40-50`
- `openagents/plugins/builtin/tool_executor/retry.py:46-48`
- `openagents/plugins/builtin/execution_policy/composite.py:31-33`
- `openagents/plugins/builtin/events/file_logging.py:56-58`

Each switches `from openagents.plugins.loader import _load_plugin` → `from openagents.plugins.loader import load_plugin` and updates the call.

### 3.2 `_BoundTool` metadata passthrough

#### 3.2.1 `openagents/plugins/builtin/runtime/default_runtime.py:_BoundTool.invoke`

Change return type from `Any` (effectively `result.data`) to `ToolExecutionResult`:

```python
async def invoke(self, params: dict[str, Any], context: Any) -> ToolExecutionResult:
    """Returns the full ToolExecutionResult so executor metadata
    (retry counts, timeouts, policy decisions) survives to events.

    The base PatternPlugin.call_tool unwraps via isinstance.
    """
    # ... existing budget / policy / executor logic ...
    if result.success:
        usage = getattr(context, "usage", None)
        if usage is not None:
            usage.tool_calls += 1
        return result   # was: return result.data
    if result.exception is not None:
        raise result.exception
    raise RuntimeError(result.error or f"Tool '{self._tool_id}' failed")
```

`_BoundTool.invoke_stream` is unchanged (it never returned `result`-shaped data).

#### 3.2.2 `openagents/interfaces/pattern.py:call_tool`

Add a duck-typed unwrap step between `tool.invoke()` and event emission:

```python
result = await tool.invoke(params or {}, ctx)

data, executor_metadata = unwrap_tool_result(result)

# ... existing retry-counter reset, tool_results append, usage bookkeeping ...

await self.emit(
    "tool.succeeded",
    tool_id=tool_id,
    result=data,
    executor_metadata=executor_metadata,
)
return data
```

Define `unwrap_tool_result` as a public module-level helper (no leading underscore — it's part of the supported pattern-author API):

```python
def unwrap_tool_result(result: Any) -> tuple[Any, dict[str, Any] | None]:
    """Unwrap a tool invocation return.

    Bound tools (via _BoundTool) return the full ToolExecutionResult.
    Raw ToolPlugin.invoke returns whatever the tool's invoke produced.
    """
    if isinstance(result, ToolExecutionResult):
        return result.data, dict(result.metadata or {})
    return result, None
```

The base `call_tool` calls `unwrap_tool_result` internally; the function is also exported from `openagents.interfaces.pattern` for custom pattern authors who override `call_tool`.

#### 3.2.3 `openagents/runtime/stream_projection.py`

The `tool.succeeded` event already projects to `RunStreamChunkKind.TOOL_SUCCEEDED`. The projection is payload-passthrough, so the new `executor_metadata` field appears automatically on the stream chunk. Confirm with a test rather than touch the projection code.

#### 3.2.4 Backward-compat note for custom patterns

Custom patterns that **do not override** `call_tool` inherit the new behavior automatically.

Custom patterns that **override** `call_tool` AND directly call `tool.invoke()` AND consume the return value as data may now receive a `ToolExecutionResult` instance. They should call the new public `unwrap_tool_result(result)` helper exported from `openagents.interfaces.pattern`.

### 3.3 `TypedConfigPluginMixin`

New file `openagents/interfaces/typed_config.py`:

```python
"""Typed plugin configuration helper."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TypedConfigPluginMixin:
    """Mixin that provides typed config validation for plugins.

    Subclasses declare a nested ``Config(BaseModel)`` and the mixin
    validates ``self.config`` into ``self.cfg`` when ``_init_typed_config``
    is invoked from the subclass ``__init__`` (after super().__init__).

    Unknown config keys emit a warning but are not rejected; this is
    a migration safety choice for the 0.3.x line. A future major
    release may switch to ``extra='forbid'``.
    """

    Config: ClassVar[type[BaseModel]]
    cfg: BaseModel

    def _init_typed_config(self) -> None:
        raw = dict(getattr(self, "config", {}) or {})
        config_cls = self.Config
        known = set(config_cls.model_fields.keys())
        unknown = sorted(set(raw.keys()) - known)
        if unknown:
            logger.warning(
                "plugin %s received unknown config keys: %s",
                type(self).__name__,
                unknown,
            )
        self.cfg = config_cls.model_validate(raw)
```

**Why a mixin and not a base-class change.** Each plugin family has its own ABC (`MemoryPlugin`, `PatternPlugin`, `ToolExecutorPlugin`, etc.). Forcing typed-config into every ABC would couple them without benefit. The mixin sits laterally and is opt-in per subclass; class-based plugins that want raw config access (rare, but possible) don't have to take it.

**Why the explicit `_init_typed_config()` call instead of an `__init_subclass__` or metaclass approach.** Plugin `__init__` signatures call `super().__init__(config=..., capabilities=...)` which sets `self.config` on the base. The typed validation must run *after* that, and the cleanest way to express the order is one explicit method call in each subclass `__init__`. Implicit hooks would have to either monkey-patch the base or constrain MRO.

### 3.4 Config(BaseModel) refactor — target list

#### 3.4.1 Non-tool builtins (13 files, all required)

| file | tentative Config fields (final shape settled by reading current `self.config.get(...)` calls) |
|---|---|
| `memory/buffer.py` (`BufferMemory`) | `state_key: str = "memory_buffer"`, `view_key: str = "history"`, `max_items: int \| None = Field(default=None, gt=0)` |
| `memory/window_buffer.py` (`WindowBufferMemory`) | TBD by reading current code |
| `pattern/react.py` (`ReActPattern`) | TBD |
| `pattern/plan_execute.py` (`PlanExecutePattern`) | TBD |
| `pattern/reflexion.py` (`ReflexionPattern`) | TBD |
| `followup/basic.py` (`BasicFollowupResolver`) | likely empty Config (still added for signature uniformity) |
| `response_repair/basic.py` (`BasicResponseRepairPolicy`) | likely empty Config |
| `session/in_memory.py` (`InMemorySessionManager`) | TBD |
| `events/async_event_bus.py` (`AsyncEventBus`) | `max_history: int = 10_000` |
| `skills/local.py` (`LocalSkillsManager`) | TBD (likely `roots: list[str]`) |
| `runtime/default_runtime.py` (`DefaultRuntime`) | TBD |
| `tool_executor/safe.py` (`SafeToolExecutor`) | `default_timeout_ms: int = 30_000`, `allow_stream_passthrough: bool = True` |
| `execution_policy/filesystem.py` (`FilesystemExecutionPolicy`) | `read_roots: list[str] = []`, `write_roots: list[str] = []`, `allow_tools: list[str] = []`, `deny_tools: list[str] = []` |

The implementation plan (next phase) will read each current `self.config.get(...)` call and freeze the field shape exactly. **No new fields are introduced.** Field names match existing keys verbatim. Validation constraints (`gt=0`, etc.) are added only where the current code does an equivalent ad-hoc check.

#### 3.4.2 Tool builtins (2 files only)

After grepping `self.config.(get|[)` and `__init__(self, config)` bodies under `openagents/plugins/builtin/tool/`:

| file | reason |
|---|---|
| `tool/http_ops.py` (`HttpRequestTool`) | Consumes `config.get("timeout", 30)` at construction. Config: `timeout: int = 30` |
| `tool/mcp_tool.py` (`McpTool`) | Optional extra; consumes `self.config.get(...)` for MCP server config. Field shape settled in implementation phase |

The remaining 23 tool files have `__init__(self, config)` boilerplate but never read `self.config` — they don't need a Config and stay as-is.

#### 3.4.3 Refactor mechanics

Each refactored class:

```python
class BufferMemory(TypedConfigPluginMixin, MemoryPlugin):
    """Append-only in-session memory with configurable projection."""

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

    # All methods that previously did self.config.get(...) now read self.cfg.field
```

MRO order: `TypedConfigPluginMixin` listed *before* the plugin ABC so `super().__init__` still resolves to the ABC (mixin doesn't define `__init__`).

## 4. Data flow change (work package #2)

### 4.1 Before

```
ReAct loop
  └─ pattern.call_tool("http_request", {"url": ...})
       ├─ emit("tool.called", tool_id, params)
       ├─ tool = ctx.tools["http_request"]              # _BoundTool
       ├─ data = await tool.invoke(params, ctx)
       │     └─ _BoundTool.invoke()
       │          ├─ policy.evaluate(...)
       │          ├─ result = await executor.execute(...)
       │          └─ return result.data                 ← metadata lost
       ├─ ctx.tool_results.append({tool_id, result: data})
       └─ emit("tool.succeeded", tool_id, result=data)  ← payload has no metadata
```

### 4.2 After

```
ReAct loop
  └─ pattern.call_tool("http_request", {"url": ...})
       ├─ emit("tool.called", tool_id, params)
       ├─ tool = ctx.tools["http_request"]
       ├─ result = await tool.invoke(params, ctx)
       │     └─ _BoundTool.invoke()
       │          ├─ policy.evaluate(...)
       │          ├─ result = await executor.execute(...)
       │          └─ return result                       ← full ToolExecutionResult
       ├─ data, executor_metadata = unwrap_tool_result(result)
       │     ├─ isinstance(result, ToolExecutionResult): unwrap
       │     └─ else: data=result, executor_metadata=None      ← raw ToolPlugin / fixture
       ├─ ctx.tool_results.append({tool_id, result: data})
       └─ emit("tool.succeeded",
              tool_id, result=data, executor_metadata=executor_metadata)
```

### 4.3 Observable contract changes

| surface | before | after |
|---|---|---|
| `pattern.call_tool()` return value | data | data (unchanged) |
| `tool.succeeded` event payload | `{tool_id, result}` | `{tool_id, result, executor_metadata}` |
| `RunStreamChunk(TOOL_SUCCEEDED).payload` | mirror of event | mirror (auto-includes new field) |
| `_BoundTool.invoke()` return | `result.data` | `ToolExecutionResult` (kernel-internal) |

### 4.4 Downstream cleanup

`tests/integration/test_research_analyst_example.py:test_research_analyst_end_to_end` currently relies on indirect proof that `RetryToolExecutor` fired (errata §13.2). With this change it can directly assert `executor_metadata.retry_attempts >= 3` on the matching `tool.succeeded` event in `events.ndjson`. Update the test and mark errata §13.2 as resolved.

## 5. Error handling and migration

### 5.1 New exception types

None.

### 5.2 Warning behavior

| trigger | mechanism | dedup |
|---|---|---|
| `_load_plugin` private alias called | `warnings.warn("_load_plugin is deprecated; use load_plugin", DeprecationWarning, stacklevel=2)` | per-callsite (`warnings.warn` default) |
| Plugin Config receives unknown keys | `logger.warning("plugin %s received unknown config keys: %s", name, sorted(unknown))` | none — every `__init__` warns; intentional, so different sessions surface independently |

### 5.3 Migration documentation

Append a section to `docs/migration-0.2-to-0.3.md`:

```markdown
## 0.3.x cleanup pass: plugin loader API & event payload changes

- `openagents.plugins.loader._load_plugin` → `load_plugin` (public).
  The underscore alias remains and emits a DeprecationWarning. Custom
  combinator plugins should switch imports.

- `tool.succeeded` event payload now includes `executor_metadata`.
  Existing subscribers reading only `tool_id` and `result` are unaffected.

- `_BoundTool.invoke()` (kernel-internal) now returns
  `ToolExecutionResult` instead of `result.data`. If your custom pattern
  bypasses the base `call_tool` and calls `tool.invoke()` directly, use
  the new `unwrap_tool_result(result)` helper exported from
  `openagents.interfaces.pattern` to handle both bound and raw returns.

- Plugins now warn (do not reject) on unknown config keys. To audit your
  agent.json files, check process logs for `received unknown config keys`.
  In a future major release this will become an error.
```

### 5.4 Compatibility matrix

| affected surface | impact |
|---|---|
| User `agent.json` with unknown fields | works; logs one warning per `__init__` |
| User `agent.json` with standard fields | unchanged |
| Built-in 4 combinators using `_load_plugin` | migrated internally to `load_plugin` |
| External user combinator using `_load_plugin` | works; emits `DeprecationWarning` |
| External custom pattern (no `call_tool` override) | automatically benefits from new event field |
| External custom pattern (overrides `call_tool`, calls `tool.invoke()` directly) | may now receive `ToolExecutionResult` — must call `unwrap_tool_result(...)` |
| External event subscribers reading `tool.succeeded.result` | unchanged |
| External event subscribers using strict-schema validation | one new optional field `executor_metadata` |

## 6. Testing plan

### 6.1 New unit test files

| file | purpose | key cases |
|---|---|---|
| `tests/unit/test_plugin_loader_public_api.py` | public `load_plugin` API | `load_plugin("memory", MemoryRef(type="buffer"))` succeeds; equivalence with `_load_plugin`; calling `_load_plugin` emits `DeprecationWarning` |
| `tests/unit/test_typed_config_mixin.py` | mixin behavior | known fields populate `self.cfg`; unknown fields emit warning but don't raise; defaults applied; multiple instances independent |
| `tests/unit/test_bound_tool_metadata.py` | `_BoundTool.invoke` return change | success returns `ToolExecutionResult`; failure still raises; `tool.succeeded` event payload contains `executor_metadata`; `executor_metadata` carries retry counts when wrapped with `RetryToolExecutor` |
| `tests/unit/test_pattern_call_tool_unwrap.py` | pattern unwrap helper | isinstance branch covered; fallback branch covered; `unwrap_tool_result` directly tested |

### 6.2 Modified unit test files

For each of the 13 non-tool refactored builtins and the 2 refactored tools, the existing test file (where one exists) gets:
- Existing assertions preserved (no behavior change permitted)
- One added test: `test_<plugin>_warns_on_unknown_config_keys` using `caplog`
- Where the test currently inspects `instance.config["key"]`, switch to `instance.cfg.key`

### 6.3 Integration test updates

`tests/integration/test_research_analyst_example.py:test_research_analyst_end_to_end`:
- Replace the indirect retry proof with a direct assertion on `events.ndjson`:
  ```python
  flaky_event = next(
      e for e in events
      if e["name"] == "tool.succeeded"
      and e["payload"].get("tool_id") == "http_request"
      and "/pages/flaky" in (e["payload"].get("result") or {}).get("url", "")
  )
  assert flaky_event["payload"]["executor_metadata"]["retry_attempts"] >= 3
  ```
- Update spec `2026-04-17-builtin-plugins-expansion-design.md` errata §13.2 to mark as resolved.

`tests/integration/test_production_coding_agent_example.py`:
- Scan for any strict-equal assertions on `tool.succeeded` payloads. Convert any such assertion to subset semantics (`>=` or key-by-key) so the new `executor_metadata` field doesn't trip them.

### 6.4 CLI smoke test

`tests/unit/test_cli_schema.py` (or closest equivalent):
- Verify `openagents schema` now emits Config schemas for the 13 refactored non-tool builtins (previously absent because they had no `class Config`).
- Verify `openagents list-plugins` output is unchanged.

### 6.5 Coverage targets

- Overall coverage maintains `fail_under = 90`.
- `openagents/interfaces/typed_config.py` ≥ 95% line coverage.
- `_BoundTool.invoke` and `pattern.call_tool` isinstance branches each have explicit tests.
- `mem0_memory.py` and `mcp_tool.py` remain excluded (consistent with current pyproject config); any Config refactor on `mcp_tool.py` does not change its exclusion status.

### 6.6 Regression scan

After implementation:
- `uv run pytest -q`
- `uv run coverage run -m pytest && uv run coverage report`
- `uv run python examples/quickstart/run_demo.py` (mock provider)
- `uv run python examples/research_analyst/run_demo.py`
- Confirm no new deprecation warnings surface beyond the intentional `_load_plugin` alias.

## 7. Documentation updates

| file | change |
|---|---|
| `docs/plugin-development.md` | add a section "Typed Config" describing `TypedConfigPluginMixin` usage with the `BufferMemory` example; add a section "Composing plugins" pointing at the public `load_plugin` API for combinator authors |
| `docs/migration-0.2-to-0.3.md` | append the cleanup-pass section from §5.3 |
| `docs/api-reference.md` | add `load_plugin` and `unwrap_tool_result` to the public API table |
| `docs/superpowers/specs/2026-04-17-builtin-plugins-expansion-design.md` | update errata §13.2 to "resolved by 2026-04-17 plugin-system-cleanup spec" |

No new top-level doc files.

## 8. Out of scope (deferred to B and C)

- Edge-case hardening (empty input, IO failure, concurrency races) → spec C
- New builtins (sqlite session manager, OTel events bridge) → spec B 精选
- Test coverage expansion beyond what this refactor itself requires → spec C
- `model_config = ConfigDict(extra="forbid")` strict mode → ≥0.4.0 breaking cut
- `_BoundTool.invoke_stream` metadata passthrough — streaming executor metadata has different semantics; deferred to spec B if user demand surfaces
- Removing the `_load_plugin` alias entirely — happens in a future release, not this one

## 9. Risks and mitigations

| risk | mitigation |
|---|---|
| Custom user pattern silently breaks because it consumes `tool.invoke()` return as data | Provide `unwrap_tool_result` helper; document in migration; export with non-underscore alias |
| MRO collisions with `TypedConfigPluginMixin` listed before plugin ABC | Mixin defines no `__init__`; `super().__init__` chain resolves to plugin ABC unchanged. Validated by the mixin test suite |
| Field-shape inference per refactored builtin gets the type subtly wrong (e.g. `int` vs `int \| None`) | Implementation plan reads each `self.config.get(key, default)` call individually; the inferred type matches the existing default exactly |
| `extra='ignore'` warning is too noisy for users intentionally passing pass-through metadata | Document that only `Config`-declared fields are validated; users with metadata-style configs should use a dedicated `metadata` field |
| Deprecation warning for `_load_plugin` alias breaks code that promotes warnings to errors in CI | Stacklevel=2 + clear message; users who treat warnings as errors must migrate immediately, which is the desired outcome |
| `examples/research_analyst` integration test still flaky on slow CI after switching to direct assertion | Direct assertion is strictly easier to satisfy than the indirect proof — if indirect passed, direct will pass |

## 10. Rollout

Single PR-shape, single implementation plan. Order suggested by writing-plans:

1. **Typed config mixin** (work package #3) — pure new file, no dependencies, lands first so subsequent refactors can use it.
2. **Config refactor** (work package #4) — 15 files, mechanical, can be split into one commit per seam. No behavior change permitted.
3. **Public loader API** (work package #1) — one new function in loader, four mechanical combinator updates, deprecation warning added to alias.
4. **`_BoundTool` metadata passthrough** (work package #2) — kernel-internal, smallest surface, requires the most-careful test work; lands last so all other tests are stable underneath.
5. **Documentation + errata cross-link.**

Each work package is independently testable; the merge order minimizes blast radius.
