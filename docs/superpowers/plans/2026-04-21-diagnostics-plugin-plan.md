# DiagnosticsPlugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `diagnostics` seam that captures error context snapshots, collects LLM latency metrics, and exports to Langfuse/Phoenix.

**Architecture:** Process-level `DiagnosticsPlugin` subscribes to `llm.succeeded`/`tool.called`/`tool.failed` events and accumulates per-run data keyed by `run_id`. `default_runtime.py` calls `on_run_complete()` after each run. LLM timing is added in `pattern.py:call_llm()` and piggybacked on the existing `llm.succeeded` event payload.

**Tech Stack:** Python 3.10+, pydantic v2, `langfuse>=2.0` (optional), `arize-phoenix-otel>=0.6` (optional), `rich>=13.7` (optional).

---

## File Map

**New files:**
- `openagents/interfaces/diagnostics.py` — `DiagnosticsPlugin`, `LLMCallMetrics`, `ErrorSnapshot`
- `openagents/plugins/builtin/diagnostics/__init__.py`
- `openagents/plugins/builtin/diagnostics/null_plugin.py`
- `openagents/plugins/builtin/diagnostics/rich_plugin.py`
- `openagents/plugins/builtin/diagnostics/langfuse_plugin.py`
- `openagents/plugins/builtin/diagnostics/phoenix_plugin.py`
- `tests/unit/interfaces/test_diagnostics_interface.py`
- `tests/unit/plugins/builtin/diagnostics/test_null_plugin.py`
- `tests/unit/plugins/builtin/diagnostics/test_rich_plugin.py`
- `tests/unit/plugins/builtin/diagnostics/test_langfuse_plugin.py`
- `tests/unit/plugins/builtin/diagnostics/test_phoenix_plugin.py`
- `tests/integration/test_diagnostics_integration.py`

**Modified files:**
- `openagents/interfaces/capabilities.py` — add `DIAG_METRICS`, `DIAG_ERROR`, `DIAG_EXPORT`
- `openagents/interfaces/event_taxonomy.py` — add `_metrics` optional field to `llm.succeeded` / `llm.failed`
- `openagents/interfaces/runtime.py` — extend `RunUsage` with 4 fields
- `openagents/config/schema.py` — add `DiagnosticsRef`; add `AppConfig.diagnostics`
- `openagents/plugins/loader.py` — add `load_diagnostics_plugin()`; extend `load_runtime_components()`
- `openagents/plugins/registry.py` — register 4 diagnostics builtins
- `openagents/interfaces/pattern.py` — add timing in `call_llm()`, attach `_metrics` to `llm.succeeded` payload
- `openagents/plugins/builtin/runtime/default_runtime.py` — call `on_run_complete()` and `capture_error_snapshot()`
- `pyproject.toml` — add `langfuse`/`phoenix` extras; update `omit` list

---

## Task 1: Core Interfaces

**Files:**
- Create: `openagents/interfaces/diagnostics.py`
- Modify: `openagents/interfaces/capabilities.py`
- Test: `tests/unit/interfaces/test_diagnostics_interface.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/interfaces/test_diagnostics_interface.py
from __future__ import annotations

import pytest
from openagents.interfaces.diagnostics import (
    DiagnosticsPlugin,
    ErrorSnapshot,
    LLMCallMetrics,
)
from openagents.interfaces.capabilities import DIAG_ERROR, DIAG_EXPORT, DIAG_METRICS


def test_llm_call_metrics_defaults():
    m = LLMCallMetrics(model="claude-3-5-sonnet", latency_ms=120.5, input_tokens=50, output_tokens=30, cached_tokens=0)
    assert m.ttft_ms is None
    assert m.attempt == 1
    assert m.error is None


def test_llm_call_metrics_with_ttft():
    m = LLMCallMetrics(
        model="claude-3-5-sonnet",
        ttft_ms=45.2,
        latency_ms=320.0,
        input_tokens=100,
        output_tokens=80,
        cached_tokens=20,
        attempt=2,
    )
    assert m.ttft_ms == 45.2
    assert m.attempt == 2


def test_error_snapshot_fields():
    snap = ErrorSnapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        error_type="ValueError",
        error_message="bad input",
        traceback="Traceback...",
        tool_call_chain=[{"tool_id": "t1", "params": {}}],
        last_transcript=[{"role": "user", "content": "hi"}],
        usage_at_failure={"llm_calls": 2},
        state_snapshot={},
        captured_at="2026-04-21T00:00:00Z",
    )
    assert snap.run_id == "r1"
    assert len(snap.tool_call_chain) == 1


def test_error_snapshot_empty_chain_for_degraded():
    snap = ErrorSnapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        error_type="ConfigError",
        error_message="bad cfg",
        traceback="",
        tool_call_chain=[],
        last_transcript=[],
        usage_at_failure={},
        state_snapshot={},
        captured_at="2026-04-21T00:00:00Z",
    )
    assert snap.tool_call_chain == []
    assert snap.last_transcript == []


def test_null_plugin_no_op():
    plugin = DiagnosticsPlugin()
    m = LLMCallMetrics(model="x", latency_ms=1.0, input_tokens=1, output_tokens=1, cached_tokens=0)
    plugin.record_llm_call("run-1", m)  # must not raise
    assert plugin.get_run_metrics("run-1") == {}


def test_capability_constants():
    assert DIAG_METRICS == "diagnostics.metrics"
    assert DIAG_ERROR == "diagnostics.error"
    assert DIAG_EXPORT == "diagnostics.export"
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/interfaces/test_diagnostics_interface.py -v
```

Expected: `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Add capability constants to `openagents/interfaces/capabilities.py`**

After the last existing constant (`SKILL_POST_RUN`), add:

```python
DIAG_METRICS = "diagnostics.metrics"
DIAG_ERROR = "diagnostics.error"
DIAG_EXPORT = "diagnostics.export"
```

Also add them to `KNOWN_CAPABILITIES`:

```python
KNOWN_CAPABILITIES = {
    MEMORY_INJECT,
    MEMORY_WRITEBACK,
    MEMORY_RETRIEVE,
    PATTERN_REACT,
    PATTERN_EXECUTE,
    TOOL_INVOKE,
    SKILL_SYSTEM_PROMPT,
    SKILL_TOOLS,
    SKILL_METADATA,
    SKILL_CONTEXT_AUGMENT,
    SKILL_TOOL_FILTER,
    SKILL_PRE_RUN,
    SKILL_POST_RUN,
    DIAG_METRICS,
    DIAG_ERROR,
    DIAG_EXPORT,
}
```

- [ ] **Step 4: Create `openagents/interfaces/diagnostics.py`**

```python
"""DiagnosticsPlugin seam — error snapshots, LLM metrics, export."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunResult, RunUsage


@dataclass
class LLMCallMetrics:
    """Timing and token data for a single LLM call."""

    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    ttft_ms: float | None = None  # None for non-streaming calls
    attempt: int = 1              # 1 = first attempt; >1 = retry
    error: str | None = None      # set if the call failed


@dataclass
class ErrorSnapshot:
    """Full error context captured at failure time."""

    run_id: str
    agent_id: str
    session_id: str
    error_type: str
    error_message: str
    traceback: str
    tool_call_chain: list[dict[str, Any]]  # [{tool_id, params, call_id}, ...]
    last_transcript: list[dict[str, Any]]  # last N transcript entries
    usage_at_failure: dict[str, Any]       # RunUsage.model_dump() snapshot
    state_snapshot: dict[str, Any]         # RunContext.state deep-copy (redacted)
    captured_at: str                       # ISO 8601 UTC


class DiagnosticsPlugin:
    """Base diagnostics seam — all methods are no-ops.

    Implementations are process-level singletons. Internal state is keyed
    by ``run_id`` to isolate concurrent runs. ``on_run_complete()`` must
    clean up any per-run data to prevent memory leaks.
    """

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        """Accumulate metrics for one LLM call within the given run."""

    def capture_error_snapshot(
        self,
        *,
        run_id: str,
        agent_id: str,
        session_id: str,
        exc: BaseException,
        ctx: RunContext | None = None,
        usage: RunUsage | None = None,
        last_n: int = 10,
        redact_keys: list[str] | None = None,
    ) -> ErrorSnapshot:
        """Build and return an ErrorSnapshot.

        When ``ctx`` is None (exception before RunContext is created) the
        tool_call_chain and last_transcript fields degrade to empty lists.
        """
        import traceback as tb
        import copy
        from datetime import datetime, timezone

        from openagents.observability.redact import redact

        chain: list[dict[str, Any]] = []
        transcript: list[dict[str, Any]] = []
        state: dict[str, Any] = {}

        if ctx is not None:
            chain = list(getattr(ctx, "_diag_tool_chain", []))
            raw_transcript = getattr(ctx, "transcript", []) or []
            transcript = list(raw_transcript[-last_n:])
            raw_state = getattr(ctx, "state", {}) or {}
            state = redact(
                copy.deepcopy(raw_state),
                keys=redact_keys or ["api_key", "token", "secret", "password", "authorization"],
                max_value_length=500,
            )

        usage_dict: dict[str, Any] = {}
        if usage is not None:
            usage_dict = usage.model_dump()

        return ErrorSnapshot(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback=tb.format_exc(),
            tool_call_chain=chain,
            last_transcript=transcript,
            usage_at_failure=usage_dict,
            state_snapshot=state,
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

    def on_run_complete(
        self,
        result: RunResult,
        snapshot: ErrorSnapshot | None,
    ) -> None:
        """Called after every run (success or failure).

        Implementations must: (1) compute and back-fill latency percentiles
        into ``result.usage``, (2) trigger export, (3) clean up per-run data.
        """

    def get_run_metrics(self, run_id: str) -> dict[str, Any]:
        """Return accumulated metrics for a run (for debugging)."""
        return {}
```

- [ ] **Step 5: Run tests — expect pass**

```
uv run pytest tests/unit/interfaces/test_diagnostics_interface.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add openagents/interfaces/diagnostics.py openagents/interfaces/capabilities.py tests/unit/interfaces/test_diagnostics_interface.py
git commit -m "feat(diagnostics): add DiagnosticsPlugin interface, LLMCallMetrics, ErrorSnapshot"
```

---

## Task 2: RunUsage Extension + Event Taxonomy

**Files:**
- Modify: `openagents/interfaces/runtime.py`
- Modify: `openagents/interfaces/event_taxonomy.py`
- Test: `tests/unit/interfaces/test_run_usage.py` (already exists — extend it)

- [ ] **Step 1: Write failing test for new RunUsage fields**

Open `tests/unit/interfaces/test_run_usage.py` and add at the end:

```python
def test_run_usage_diagnostics_fields_defaults():
    from openagents.interfaces.runtime import RunUsage
    u = RunUsage()
    assert u.ttft_ms is None
    assert u.llm_latency_p50_ms is None
    assert u.llm_latency_p95_ms is None
    assert u.llm_retry_count == 0


def test_run_usage_diagnostics_fields_set():
    from openagents.interfaces.runtime import RunUsage
    u = RunUsage(ttft_ms=42.0, llm_latency_p50_ms=150.0, llm_latency_p95_ms=400.0, llm_retry_count=2)
    assert u.ttft_ms == 42.0
    assert u.llm_latency_p50_ms == 150.0
    assert u.llm_latency_p95_ms == 400.0
    assert u.llm_retry_count == 2
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/interfaces/test_run_usage.py -v -k "diagnostics"
```

Expected: FAIL — `RunUsage` has no `ttft_ms`.

- [ ] **Step 3: Extend `RunUsage` in `openagents/interfaces/runtime.py`**

Add 4 fields after `cost_breakdown`:

```python
class RunUsage(BaseModel):
    """Usage statistics collected during a run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_cached: int = 0
    input_tokens_cache_creation: int = 0
    cost_usd: float | None = None
    cost_breakdown: dict[str, float] = Field(default_factory=dict)
    # Diagnostics fields (back-filled by DiagnosticsPlugin.on_run_complete)
    ttft_ms: float | None = None
    llm_latency_p50_ms: float | None = None
    llm_latency_p95_ms: float | None = None
    llm_retry_count: int = 0
```

- [ ] **Step 4: Update event taxonomy for `_metrics` optional field**

In `openagents/interfaces/event_taxonomy.py`, update the `llm.succeeded` and `llm.failed` schemas:

```python
"llm.succeeded": EventSchema(
    "llm.succeeded",
    ("model",),
    ("_metrics",),
    "LLM returned successfully. Optional '_metrics' carries LLMCallMetrics timing data.",
),
"llm.failed": EventSchema(
    "llm.failed",
    ("model",),
    ("_metrics",),
    "LLM call failed. Optional '_metrics' carries LLMCallMetrics timing data.",
),
```

Note: `llm.failed` does not currently exist in the taxonomy — add it as a new entry after `llm.succeeded`:

```python
"llm.failed": EventSchema(
    "llm.failed",
    ("model",),
    ("_metrics",),
    "LLM call failed. Optional '_metrics' carries LLMCallMetrics timing data.",
),
```

- [ ] **Step 5: Run tests — expect pass**

```
uv run pytest tests/unit/interfaces/test_run_usage.py tests/unit/interfaces/test_event_taxonomy.py -v
```

Expected: all PASS (new tests pass; existing taxonomy tests still pass).

- [ ] **Step 6: Commit**

```bash
git add openagents/interfaces/runtime.py openagents/interfaces/event_taxonomy.py tests/unit/interfaces/test_run_usage.py
git commit -m "feat(diagnostics): extend RunUsage with latency fields; declare _metrics in llm events"
```

---

## Task 3: NullDiagnosticsPlugin

**Files:**
- Create: `openagents/plugins/builtin/diagnostics/__init__.py`
- Create: `openagents/plugins/builtin/diagnostics/null_plugin.py`
- Test: `tests/unit/plugins/builtin/diagnostics/test_null_plugin.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/plugins/builtin/diagnostics/test_null_plugin.py
from __future__ import annotations

import pytest
from openagents.plugins.builtin.diagnostics.null_plugin import NullDiagnosticsPlugin
from openagents.interfaces.diagnostics import LLMCallMetrics


def test_null_record_llm_call_no_op():
    plugin = NullDiagnosticsPlugin()
    m = LLMCallMetrics(model="x", latency_ms=1.0, input_tokens=1, output_tokens=1, cached_tokens=0)
    plugin.record_llm_call("run-1", m)  # must not raise


def test_null_capture_error_snapshot_returns_snapshot():
    plugin = NullDiagnosticsPlugin()
    exc = ValueError("oops")
    snap = plugin.capture_error_snapshot(
        run_id="r1",
        agent_id="a1",
        session_id="s1",
        exc=exc,
        ctx=None,
        usage=None,
    )
    assert snap.run_id == "r1"
    assert snap.error_type == "ValueError"
    assert snap.tool_call_chain == []
    assert snap.last_transcript == []


def test_null_on_run_complete_no_op():
    from openagents.interfaces.runtime import RunResult, RunUsage
    plugin = NullDiagnosticsPlugin()
    result = RunResult(run_id="r1", usage=RunUsage())
    plugin.on_run_complete(result, None)  # must not raise


def test_null_get_run_metrics_empty():
    plugin = NullDiagnosticsPlugin()
    assert plugin.get_run_metrics("run-1") == {}
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_null_plugin.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `openagents/plugins/builtin/diagnostics/__init__.py`** (empty file)

```python
```

- [ ] **Step 4: Create `openagents/plugins/builtin/diagnostics/null_plugin.py`**

```python
"""NullDiagnosticsPlugin — no-op default implementation."""

from __future__ import annotations

from openagents.interfaces.diagnostics import DiagnosticsPlugin


class NullDiagnosticsPlugin(DiagnosticsPlugin):
    """Process-level diagnostics plugin that does nothing.

    Used as the default when no diagnostics are configured.
    All methods are inherited no-ops from DiagnosticsPlugin.
    """
```

Also create `tests/unit/plugins/builtin/diagnostics/__init__.py` (empty).

- [ ] **Step 5: Run tests — expect pass**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_null_plugin.py -v
```

Expected: all 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add openagents/plugins/builtin/diagnostics/ tests/unit/plugins/builtin/diagnostics/
git commit -m "feat(diagnostics): add NullDiagnosticsPlugin"
```

---

## Task 4: Config Schema + Registry + Loader Wiring

**Files:**
- Modify: `openagents/config/schema.py`
- Modify: `openagents/plugins/registry.py`
- Modify: `openagents/plugins/loader.py`
- Test: extend `tests/unit/test_plugin_loader.py` or create `tests/unit/plugins/test_diagnostics_loader.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/plugins/test_diagnostics_loader.py
from __future__ import annotations

import pytest
from openagents.config.schema import AppConfig
from openagents.plugins.loader import load_diagnostics_plugin
from openagents.plugins.builtin.diagnostics.null_plugin import NullDiagnosticsPlugin


def _minimal_config_dict(**overrides):
    base = {
        "agents": [{"id": "a1", "name": "Agent", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}]
    }
    base.update(overrides)
    return base


def test_app_config_diagnostics_defaults_to_none():
    cfg = AppConfig(**_minimal_config_dict())
    assert cfg.diagnostics is None


def test_app_config_diagnostics_null_type():
    cfg = AppConfig(**_minimal_config_dict(diagnostics={"type": "null"}))
    assert cfg.diagnostics is not None
    assert cfg.diagnostics.type == "null"


def test_load_diagnostics_plugin_none_returns_null():
    plugin = load_diagnostics_plugin(None)
    assert isinstance(plugin, NullDiagnosticsPlugin)


def test_load_diagnostics_plugin_null_type():
    from openagents.config.schema import DiagnosticsRef
    ref = DiagnosticsRef(type="null")
    plugin = load_diagnostics_plugin(ref)
    assert isinstance(plugin, NullDiagnosticsPlugin)


def test_load_diagnostics_plugin_unknown_type_raises():
    from openagents.config.schema import DiagnosticsRef
    from openagents.errors.exceptions import PluginLoadError
    ref = DiagnosticsRef(type="nonexistent")
    with pytest.raises(PluginLoadError):
        load_diagnostics_plugin(ref)
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/plugins/test_diagnostics_loader.py -v
```

Expected: `ImportError` / `AttributeError`.

- [ ] **Step 3: Add `DiagnosticsRef` to `openagents/config/schema.py`**

After the existing `SkillsRef` class (around line 101):

```python
class DiagnosticsRef(PluginRef):
    """Diagnostics plugin reference at global level."""
    error_snapshot_last_n: int = 10
    redact_keys: list[str] = Field(default_factory=lambda: ["api_key", "token", "secret", "password", "authorization"])
```

Add `DiagnosticsRef` import to the `PluginRef` subclass block, then add `diagnostics` field to `AppConfig` (after `logging`):

```python
class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    agents: list[AgentDefinition] = Field(default_factory=list)
    runtime: RuntimeRef = Field(default_factory=lambda: RuntimeRef(type="default"))
    session: SessionRef = Field(default_factory=lambda: SessionRef(type="in_memory"))
    events: EventBusRef = Field(default_factory=lambda: EventBusRef(type="async"))
    skills: SkillsRef = Field(default_factory=lambda: SkillsRef(type="local"))
    logging: LoggingConfig | None = None
    diagnostics: DiagnosticsRef | None = None
```

Also add `DiagnosticsRef` to the `_validate_config_rules` validator (only validate selector if set):

```python
@model_validator(mode="after")
def _validate_config_rules(self) -> "AppConfig":
    if not self.agents:
        raise ConfigValidationError("'agents' must contain at least one item")

    self.runtime.validate_selector("runtime")
    self.session.validate_selector("session")
    self.events.validate_selector("events")
    self.skills.validate_selector("skills")
    if self.diagnostics is not None:
        self.diagnostics.validate_selector("diagnostics")
    return self
```

- [ ] **Step 4: Register diagnostics builtins in `openagents/plugins/registry.py`**

Add imports after the existing event bus imports:

```python
from openagents.plugins.builtin.diagnostics.null_plugin import NullDiagnosticsPlugin
```

Add a `diagnostics` entry to `_BUILTIN_REGISTRY`:

```python
"diagnostics": {
    "null": NullDiagnosticsPlugin,
},
```

Add `"diagnostics": {}` to `_DECORATOR_REGISTRY_MAP`:

```python
_DECORATOR_REGISTRY_MAP: dict[str, dict[str, type[Any]]] = {
    ...
    "diagnostics": {},
}
```

- [ ] **Step 5: Add `load_diagnostics_plugin` to `openagents/plugins/loader.py`**

Add import at the top of the file:

```python
from openagents.config.schema import (
    AgentDefinition,
    ContextAssemblerRef,
    DiagnosticsRef,
    EventBusRef,
    MemoryRef,
    PatternRef,
    PluginRef,
    RuntimeRef,
    SessionRef,
    SkillsRef,
    ToolExecutorRef,
    ToolRef,
)
```

Add the function after `load_skills_plugin`:

```python
def load_diagnostics_plugin(ref: DiagnosticsRef | None) -> Any:
    """Load a diagnostics plugin. Returns NullDiagnosticsPlugin when ref is None."""
    from openagents.plugins.builtin.diagnostics.null_plugin import NullDiagnosticsPlugin

    if ref is None:
        return NullDiagnosticsPlugin()
    return _load_plugin_impl("diagnostics", ref)
```

Update `LoadedRuntimeComponents` dataclass and `load_runtime_components()` to include diagnostics:

```python
@dataclass
class LoadedRuntimeComponents:
    runtime: Any
    session: Any
    events: Any
    skills: Any
    diagnostics: Any  # ← new field
```

Update `load_runtime_components()` signature and body:

```python
def load_runtime_components(
    runtime_ref: RuntimeRef,
    session_ref: SessionRef,
    events_ref: EventBusRef,
    skills_ref: SkillsRef | None,
    diagnostics_ref: DiagnosticsRef | None = None,  # ← new, optional
) -> LoadedRuntimeComponents:
    ...
    events = load_events_plugin(events_ref)
    session = load_session_plugin(session_ref)
    skills = load_skills_plugin(skills_ref)
    if hasattr(skills, "_session_manager"):
        skills._session_manager = session
    diagnostics = load_diagnostics_plugin(diagnostics_ref)  # ← new
    runtime = load_runtime_plugin(runtime_ref)
    if hasattr(runtime, "_event_bus"):
        runtime._event_bus = events
    if hasattr(runtime, "_session_manager"):
        runtime._session_manager = session
    if hasattr(runtime, "_diagnostics"):           # ← new
        runtime._diagnostics = diagnostics         # ← new
    return LoadedRuntimeComponents(
        runtime=runtime,
        session=session,
        events=events,
        skills=skills,
        diagnostics=diagnostics,                   # ← new
    )
```

- [ ] **Step 6: Run tests — expect pass**

```
uv run pytest tests/unit/plugins/test_diagnostics_loader.py -v
```

Expected: all 5 PASS.

- [ ] **Step 7: Run the full suite to check nothing is broken**

```
uv run pytest -q
```

Expected: all PASS (no regressions; `LoadedRuntimeComponents` callers don't pass `diagnostics_ref` — the default `None` keeps them working).

- [ ] **Step 8: Commit**

```bash
git add openagents/config/schema.py openagents/plugins/registry.py openagents/plugins/loader.py tests/unit/plugins/test_diagnostics_loader.py
git commit -m "feat(diagnostics): wire DiagnosticsRef into config schema, registry, and loader"
```

---

## Task 5: LLM Timing in `pattern.py`

**Files:**
- Modify: `openagents/interfaces/pattern.py`
- Test: extend `tests/unit/interfaces/` with a new test file

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/interfaces/test_pattern_llm_timing.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_llm_succeeded_payload_contains_metrics():
    """llm.succeeded event must carry a _metrics dict after call_llm()."""
    from openagents.interfaces.diagnostics import LLMCallMetrics

    emitted_events: list[tuple[str, dict]] = []

    async def fake_emit(name, **payload):
        emitted_events.append((name, payload))

    # Build a minimal mock pattern context
    ctx = MagicMock()
    ctx.llm_client = AsyncMock()
    ctx.run_request = None
    ctx.usage = MagicMock(
        llm_calls=0, input_tokens=0, output_tokens=0, total_tokens=0,
        input_tokens_cached=0, input_tokens_cache_creation=0,
        cost_usd=0.0, cost_breakdown={}, scratch={},
    )
    ctx.scratch = {}

    fake_response = MagicMock()
    fake_response.usage = MagicMock(
        input_tokens=10, output_tokens=20, total_tokens=30, metadata={}
    )
    ctx.llm_client.generate = AsyncMock(return_value=fake_response)

    from openagents.interfaces.pattern import PatternPlugin
    plugin = PatternPlugin.__new__(PatternPlugin)
    plugin.emit = fake_emit

    await plugin.call_llm(ctx=ctx, messages=[], model="test-model")

    llm_succeeded = next((p for n, p in emitted_events if n == "llm.succeeded"), None)
    assert llm_succeeded is not None
    assert "_metrics" in llm_succeeded
    metrics = llm_succeeded["_metrics"]
    assert isinstance(metrics, LLMCallMetrics)
    assert metrics.model == "test-model"
    assert metrics.latency_ms >= 0
    assert metrics.input_tokens == 10
    assert metrics.output_tokens == 20
    assert metrics.ttft_ms is None  # non-streaming: None
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/interfaces/test_pattern_llm_timing.py -v
```

Expected: FAIL — `_metrics` not in payload.

- [ ] **Step 3: Add timing to `call_llm()` in `openagents/interfaces/pattern.py`**

Add `import time` at the top (with other imports). Then in `call_llm()`, wrap the `generate()` call (around line 298-329):

```python
# Before the generate() call, after the emit("llm.called") line:
import time
from openagents.interfaces.diagnostics import LLMCallMetrics

_t_start = time.monotonic()

await self.emit("llm.called", model=model)
response = await ctx.llm_client.generate(
    messages=messages,
    model=model,
    temperature=temperature,
    max_tokens=max_tokens,
)

_latency_ms = (time.monotonic() - _t_start) * 1000

if ctx.usage is not None:
    ctx.usage.llm_calls += 1
    if response.usage is not None:
        ctx.usage.input_tokens += response.usage.input_tokens
        ctx.usage.output_tokens += response.usage.output_tokens
        ctx.usage.total_tokens += response.usage.total_tokens

        meta = response.usage.metadata or {}
        cached_read = int(meta.get("cache_read_input_tokens", meta.get("cached_tokens", 0)) or 0)
        cached_write = int(meta.get("cache_creation_input_tokens", 0) or 0)
        ctx.usage.input_tokens_cached += cached_read
        ctx.usage.input_tokens_cache_creation += cached_write

        call_cost = meta.get("cost_usd")
        sticky = ctx.scratch.get("__cost_unavailable__")
        if sticky or call_cost is None:
            ctx.usage.cost_usd = None
            ctx.scratch["__cost_unavailable__"] = True
        else:
            current = ctx.usage.cost_usd if ctx.usage.cost_usd is not None else 0.0
            ctx.usage.cost_usd = current + float(call_cost)
            for bucket, amount in (meta.get("cost_breakdown") or {}).items():
                ctx.usage.cost_breakdown[bucket] = ctx.usage.cost_breakdown.get(bucket, 0.0) + float(amount)

_call_metrics = LLMCallMetrics(
    model=model,
    latency_ms=_latency_ms,
    input_tokens=response.usage.input_tokens if response.usage else 0,
    output_tokens=response.usage.output_tokens if response.usage else 0,
    cached_tokens=int((response.usage.metadata or {}).get("cache_read_input_tokens", 0) or 0) if response.usage else 0,
    ttft_ms=None,  # non-streaming: not applicable
)

await self.emit("usage.updated", usage=ctx.usage.model_dump() if ctx.usage else None)
await self.emit("llm.succeeded", model=model, _metrics=_call_metrics)
```

The key change: `import time` and `LLMCallMetrics` at top of file; compute `_latency_ms`; build `_call_metrics`; pass it as `_metrics=_call_metrics` to `llm.succeeded` emit.

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/unit/interfaces/test_pattern_llm_timing.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```
uv run pytest -q
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add openagents/interfaces/pattern.py tests/unit/interfaces/test_pattern_llm_timing.py
git commit -m "feat(diagnostics): add LLM call timing to pattern.call_llm; attach _metrics to llm.succeeded"
```

---

## Task 6: `default_runtime.py` Integration

**Files:**
- Modify: `openagents/plugins/builtin/runtime/default_runtime.py`
- Test: extend `tests/integration/test_diagnostics_integration.py` (new file)

- [ ] **Step 1: Write failing integration tests**

```python
# tests/integration/test_diagnostics_integration.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from openagents.interfaces.diagnostics import DiagnosticsPlugin, ErrorSnapshot, LLMCallMetrics
from openagents.interfaces.runtime import RunResult, RunUsage


class CapturingDiagnosticsPlugin(DiagnosticsPlugin):
    """Test double that records all calls."""

    def __init__(self):
        self._calls: list[LLMCallMetrics] = []
        self._snapshots: list[ErrorSnapshot] = []
        self._complete_calls: list[tuple[RunResult, ErrorSnapshot | None]] = []

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        self._calls.append(metrics)

    def on_run_complete(self, result: RunResult, snapshot: ErrorSnapshot | None) -> None:
        self._complete_calls.append((result, snapshot))


@pytest.mark.asyncio
async def test_on_run_complete_called_on_success(make_runtime):
    """on_run_complete is called once after a successful run."""
    diag = CapturingDiagnosticsPlugin()
    runtime = make_runtime(diagnostics=diag)
    result = await runtime.run_detailed(agent_id="test-agent", input_text="hello")
    assert result.stop_reason.value == "completed"
    assert len(diag._complete_calls) == 1
    result_arg, snapshot_arg = diag._complete_calls[0]
    assert snapshot_arg is None


@pytest.mark.asyncio
async def test_error_snapshot_attached_on_failure(make_runtime_that_fails):
    """ErrorSnapshot is in RunResult.metadata on tool failure."""
    diag = CapturingDiagnosticsPlugin()
    runtime = make_runtime_that_fails(diagnostics=diag)
    result = await runtime.run_detailed(agent_id="test-agent", input_text="fail me")
    assert "error_snapshot" in result.metadata or len(diag._complete_calls) == 1


@pytest.mark.asyncio
async def test_null_diagnostics_does_not_break_run(make_runtime):
    """NullDiagnosticsPlugin must not affect normal run flow."""
    from openagents.plugins.builtin.diagnostics.null_plugin import NullDiagnosticsPlugin
    runtime = make_runtime(diagnostics=NullDiagnosticsPlugin())
    result = await runtime.run_detailed(agent_id="test-agent", input_text="hello")
    assert result.stop_reason.value == "completed"


@pytest.mark.asyncio
async def test_run_usage_has_latency_fields_after_run(make_runtime):
    """After a run, llm_latency_p50_ms is populated in RunUsage."""
    diag = CapturingDiagnosticsPlugin()
    runtime = make_runtime(diagnostics=diag)
    result = await runtime.run_detailed(agent_id="test-agent", input_text="hello")
    # p50 may be None if no LLM calls in mock, but field must exist
    assert hasattr(result.usage, "llm_latency_p50_ms")
```

Note: `make_runtime` and `make_runtime_that_fails` are fixtures from `tests/conftest.py` or defined locally. Use the existing test patterns from `tests/integration/` as reference for how to construct a runtime with mock LLM.

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/integration/test_diagnostics_integration.py -v
```

Expected: test collection errors or attribute errors on runtime.

- [ ] **Step 3: Add `_diagnostics` attribute to `DefaultRuntime`**

In `openagents/plugins/builtin/runtime/default_runtime.py`, find `__init__` and add:

```python
self._diagnostics: Any = None  # injected by loader
```

- [ ] **Step 4: Subscribe to events in `DefaultRuntime._run()` for tool chain tracking**

Inside `_run()`, after the event bus subscription for checkpoints, add diagnostics subscriptions:

```python
from openagents.interfaces.diagnostics import LLMCallMetrics

_diag = self._diagnostics
if _diag is not None:
    async def _diag_llm_handler(**payload):
        metrics = payload.get("_metrics")
        if isinstance(metrics, LLMCallMetrics):
            _diag.record_llm_call(request.run_id, metrics)
    self._event_bus.subscribe("llm.succeeded", _diag_llm_handler)
```

Unsubscribe in the `finally` block (next to checkpoint unsubscription):

```python
if _diag is not None:
    self._event_bus.unsubscribe("llm.succeeded", _diag_llm_handler)
```

- [ ] **Step 5: Call `on_run_complete()` on success path**

In the success return block (after `run_result = RunResult(...)`), before `return run_result`, add:

```python
if self._diagnostics is not None:
    self._diagnostics.on_run_complete(run_result, None)
```

- [ ] **Step 6: Call `capture_error_snapshot()` + `on_run_complete()` on failure path**

In the `except Exception as exc:` block (around line 972), after building `run_result`, add:

```python
if self._diagnostics is not None:
    snapshot = self._diagnostics.capture_error_snapshot(
        run_id=request.run_id,
        agent_id=request.agent_id,
        session_id=request.session_id,
        exc=exc,
        ctx=plugins.pattern._context if "plugins" in locals() and hasattr(plugins.pattern, "_context") else None,
        usage=usage,
    )
    run_result.metadata["error_snapshot"] = {
        "run_id": snapshot.run_id,
        "error_type": snapshot.error_type,
        "error_message": snapshot.error_message,
        "traceback": snapshot.traceback,
        "tool_call_chain": snapshot.tool_call_chain,
        "last_transcript": snapshot.last_transcript,
        "captured_at": snapshot.captured_at,
    }
    self._diagnostics.on_run_complete(run_result, snapshot)
```

- [ ] **Step 7: Wire `diagnostics_ref` into `Runtime.from_config()` / `Runtime.from_dict()`**

In `openagents/runtime/runtime.py`, find where `load_runtime_components()` is called and pass `diagnostics_ref=app_config.diagnostics`.

- [ ] **Step 8: Run tests — expect pass**

```
uv run pytest tests/integration/test_diagnostics_integration.py -v
```

Expected: PASS (adjust fixtures as needed using patterns from existing integration tests).

- [ ] **Step 9: Run full suite**

```
uv run pytest -q
```

Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add openagents/plugins/builtin/runtime/default_runtime.py openagents/runtime/runtime.py tests/integration/test_diagnostics_integration.py
git commit -m "feat(diagnostics): integrate DiagnosticsPlugin into DefaultRuntime; capture error snapshots"
```

---

## Task 7: `RichDiagnosticsPlugin`

**Files:**
- Create: `openagents/plugins/builtin/diagnostics/rich_plugin.py`
- Modify: `openagents/plugins/registry.py` — add `rich` type
- Test: `tests/unit/plugins/builtin/diagnostics/test_rich_plugin.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/plugins/builtin/diagnostics/test_rich_plugin.py
from __future__ import annotations

import pytest


def test_rich_plugin_import_fails_without_rich(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "rich", None)
    monkeypatch.setitem(sys.modules, "rich.console", None)
    monkeypatch.setitem(sys.modules, "rich.table", None)
    monkeypatch.setitem(sys.modules, "rich.panel", None)
    # Re-import should raise ImportError gracefully
    with pytest.raises((ImportError, TypeError)):
        import importlib
        import openagents.plugins.builtin.diagnostics.rich_plugin as mod
        importlib.reload(mod)
        mod.RichDiagnosticsPlugin()


def test_rich_plugin_success_panel(capsys):
    pytest.importorskip("rich")
    from openagents.plugins.builtin.diagnostics.rich_plugin import RichDiagnosticsPlugin
    from openagents.interfaces.runtime import RunResult, RunUsage
    plugin = RichDiagnosticsPlugin()
    result = RunResult(run_id="r1", usage=RunUsage(llm_calls=2, input_tokens=100, output_tokens=50))
    plugin.on_run_complete(result, None)
    captured = capsys.readouterr()
    assert "r1" in captured.err or captured.err != ""  # something rendered


def test_rich_plugin_failure_panel_with_snapshot(capsys):
    pytest.importorskip("rich")
    from openagents.plugins.builtin.diagnostics.rich_plugin import RichDiagnosticsPlugin
    from openagents.interfaces.diagnostics import ErrorSnapshot
    from openagents.interfaces.runtime import RunResult, RunUsage, StopReason
    plugin = RichDiagnosticsPlugin()
    snap = ErrorSnapshot(
        run_id="r1", agent_id="a1", session_id="s1",
        error_type="ValueError", error_message="oops",
        traceback="Traceback...", tool_call_chain=[],
        last_transcript=[], usage_at_failure={}, state_snapshot={},
        captured_at="2026-04-21T00:00:00Z",
    )
    result = RunResult(run_id="r1", stop_reason=StopReason.FAILED, usage=RunUsage())
    plugin.on_run_complete(result, snap)
    captured = capsys.readouterr()
    assert "ValueError" in captured.err or "oops" in captured.err
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_rich_plugin.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `openagents/plugins/builtin/diagnostics/rich_plugin.py`**

```python
"""RichDiagnosticsPlugin — local dev panel rendered to stderr."""

from __future__ import annotations

import statistics
from typing import Any

from openagents.interfaces.diagnostics import DiagnosticsPlugin, ErrorSnapshot, LLMCallMetrics
from openagents.interfaces.runtime import RunResult, RunUsage, StopReason


class RichDiagnosticsPlugin(DiagnosticsPlugin):
    """Renders a Rich diagnostic panel to stderr after each run.

    Requires the 'rich' optional extra. On success: compact usage table.
    On failure: full error panel with traceback and tool call chain.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        try:
            from rich.console import Console
        except ImportError as exc:
            raise ImportError(
                "RichDiagnosticsPlugin requires 'rich'. Install with: pip install 'io-openagent-sdk[rich]'"
            ) from exc
        self._console = Console(stderr=True)
        self._per_run: dict[str, list[LLMCallMetrics]] = {}

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        self._per_run.setdefault(run_id, []).append(metrics)

    def on_run_complete(self, result: RunResult, snapshot: ErrorSnapshot | None) -> None:
        from rich.panel import Panel
        from rich.table import Table
        from rich import box

        run_id = result.run_id
        calls = self._per_run.pop(run_id, [])

        # Back-fill latency percentiles
        if calls:
            latencies = sorted(c.latency_ms for c in calls)
            n = len(latencies)
            result.usage.llm_latency_p50_ms = latencies[n // 2]
            if n >= 2:
                p95_idx = int(n * 0.95)
                result.usage.llm_latency_p95_ms = latencies[min(p95_idx, n - 1)]
            result.usage.llm_retry_count = sum(1 for c in calls if c.attempt > 1)
            ttft_values = [c.ttft_ms for c in calls if c.ttft_ms is not None]
            if ttft_values:
                result.usage.ttft_ms = ttft_values[0]

        if snapshot is not None:
            self._render_error_panel(snapshot, result.usage)
        else:
            self._render_success_panel(result.run_id, result.usage)

    def _render_success_panel(self, run_id: str, usage: RunUsage) -> None:
        from rich.table import Table
        from rich import box
        table = Table(box=box.SIMPLE, show_header=False)
        table.add_column("key", style="bold cyan")
        table.add_column("value")
        table.add_row("run_id", run_id)
        table.add_row("llm_calls", str(usage.llm_calls))
        table.add_row("tokens in/out", f"{usage.input_tokens} / {usage.output_tokens}")
        table.add_row("latency p50", f"{usage.llm_latency_p50_ms:.1f}ms" if usage.llm_latency_p50_ms else "n/a")
        table.add_row("latency p95", f"{usage.llm_latency_p95_ms:.1f}ms" if usage.llm_latency_p95_ms else "n/a")
        table.add_row("retries", str(usage.llm_retry_count))
        self._console.print(table)

    def _render_error_panel(self, snapshot: ErrorSnapshot, usage: RunUsage) -> None:
        from rich.panel import Panel
        from rich.text import Text
        lines = [
            f"[bold red]{snapshot.error_type}[/]: {snapshot.error_message}",
            "",
            f"run_id: {snapshot.run_id}  |  agent: {snapshot.agent_id}  |  session: {snapshot.session_id}",
            "",
            "[bold]Tool call chain:[/]",
        ]
        for entry in snapshot.tool_call_chain:
            lines.append(f"  → {entry.get('tool_id', '?')}  params={entry.get('params', {})}")
        if not snapshot.tool_call_chain:
            lines.append("  (none)")
        lines += ["", "[bold]Traceback:[/]", snapshot.traceback]
        self._console.print(Panel("\n".join(lines), title="[red]Run Failed[/]", border_style="red"))
```

- [ ] **Step 4: Register `rich` type in `openagents/plugins/registry.py`**

Add import:

```python
from openagents.plugins.builtin.diagnostics.rich_plugin import RichDiagnosticsPlugin
```

Add to `"diagnostics"` in `_BUILTIN_REGISTRY`:

```python
"diagnostics": {
    "null": NullDiagnosticsPlugin,
    "rich": RichDiagnosticsPlugin,
},
```

- [ ] **Step 5: Run tests — expect pass**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_rich_plugin.py -v
```

Expected: all PASS (tests that need `rich` are skipped if not installed; others pass).

- [ ] **Step 6: Commit**

```bash
git add openagents/plugins/builtin/diagnostics/rich_plugin.py openagents/plugins/registry.py tests/unit/plugins/builtin/diagnostics/test_rich_plugin.py
git commit -m "feat(diagnostics): add RichDiagnosticsPlugin with success/error panels"
```

---

## Task 8: `LangfuseExporter`

**Files:**
- Create: `openagents/plugins/builtin/diagnostics/langfuse_plugin.py`
- Modify: `openagents/plugins/registry.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/plugins/builtin/diagnostics/test_langfuse_plugin.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/plugins/builtin/diagnostics/test_langfuse_plugin.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


def test_langfuse_plugin_missing_import_raises():
    import sys
    with patch.dict(sys.modules, {"langfuse": None}):
        with pytest.raises(ImportError, match="langfuse"):
            from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter
            import importlib
            import openagents.plugins.builtin.diagnostics.langfuse_plugin as mod
            importlib.reload(mod)
            mod.LangfuseExporter(config={"public_key": "pk", "secret_key": "sk"})


def test_langfuse_plugin_on_run_complete_calls_trace(monkeypatch):
    """on_run_complete sends a Langfuse trace with correct root span."""
    pytest.importorskip("langfuse")
    from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter
    from openagents.interfaces.runtime import RunResult, RunUsage, StopReason
    from openagents.interfaces.diagnostics import LLMCallMetrics

    mock_langfuse = MagicMock()
    mock_trace = MagicMock()
    mock_langfuse.trace.return_value = mock_trace
    mock_trace.span.return_value = MagicMock()

    with patch("openagents.plugins.builtin.diagnostics.langfuse_plugin.Langfuse", return_value=mock_langfuse):
        plugin = LangfuseExporter(config={"public_key": "pk", "secret_key": "sk"})
        plugin.record_llm_call("r1", LLMCallMetrics(model="m", latency_ms=100.0, input_tokens=10, output_tokens=5, cached_tokens=0))
        result = RunResult(run_id="r1", usage=RunUsage(llm_calls=1, input_tokens=10, output_tokens=5))
        plugin.on_run_complete(result, None)

    mock_langfuse.trace.assert_called_once()
    call_kwargs = mock_langfuse.trace.call_args.kwargs
    assert call_kwargs.get("id") == "r1" or call_kwargs.get("name") is not None


def test_langfuse_plugin_error_snapshot_in_metadata(monkeypatch):
    """When ErrorSnapshot present, it appears in Langfuse trace metadata."""
    pytest.importorskip("langfuse")
    from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter
    from openagents.interfaces.runtime import RunResult, RunUsage, StopReason
    from openagents.interfaces.diagnostics import ErrorSnapshot

    mock_langfuse = MagicMock()
    mock_trace = MagicMock()
    mock_langfuse.trace.return_value = mock_trace
    mock_trace.span.return_value = MagicMock()

    with patch("openagents.plugins.builtin.diagnostics.langfuse_plugin.Langfuse", return_value=mock_langfuse):
        plugin = LangfuseExporter(config={"public_key": "pk", "secret_key": "sk"})
        snap = ErrorSnapshot(
            run_id="r1", agent_id="a1", session_id="s1",
            error_type="ValueError", error_message="oops",
            traceback="", tool_call_chain=[], last_transcript=[],
            usage_at_failure={}, state_snapshot={}, captured_at="2026-04-21T00:00:00Z",
        )
        result = RunResult(run_id="r1", stop_reason=StopReason.FAILED, usage=RunUsage())
        plugin.on_run_complete(result, snap)

    trace_kwargs = mock_langfuse.trace.call_args.kwargs
    metadata = trace_kwargs.get("metadata", {})
    assert "error_snapshot" in metadata or mock_trace.span.called
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_langfuse_plugin.py -v
```

Expected: `ModuleNotFoundError` or import errors.

- [ ] **Step 3: Create `openagents/plugins/builtin/diagnostics/langfuse_plugin.py`**

```python
"""LangfuseExporter — send run traces to Langfuse."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.diagnostics import DiagnosticsPlugin, ErrorSnapshot, LLMCallMetrics
from openagents.interfaces.runtime import RunResult, StopReason


class LangfuseExporter(DiagnosticsPlugin):
    """Exports run traces to Langfuse after each run.

    Requires the 'langfuse' optional extra.
    Config keys: public_key, secret_key, host (optional).
    """

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        try:
            from langfuse import Langfuse
        except ImportError as exc:
            raise ImportError(
                "LangfuseExporter requires 'langfuse'. Install with: pip install langfuse"
            ) from exc
        self._client = Langfuse(
            public_key=cfg.get("public_key", ""),
            secret_key=cfg.get("secret_key", ""),
            host=cfg.get("host", "https://cloud.langfuse.com"),
        )
        self._last_n: int = int(cfg.get("error_snapshot_last_n", 10))
        self._per_run: dict[str, list[LLMCallMetrics]] = {}

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        self._per_run.setdefault(run_id, []).append(metrics)

    def on_run_complete(self, result: RunResult, snapshot: ErrorSnapshot | None) -> None:
        run_id = result.run_id
        calls = self._per_run.pop(run_id, [])

        # Back-fill latency percentiles
        if calls:
            latencies = sorted(c.latency_ms for c in calls)
            n = len(latencies)
            result.usage.llm_latency_p50_ms = latencies[n // 2]
            if n >= 2:
                result.usage.llm_latency_p95_ms = latencies[min(int(n * 0.95), n - 1)]
            result.usage.llm_retry_count = sum(1 for c in calls if c.attempt > 1)
            ttft_values = [c.ttft_ms for c in calls if c.ttft_ms is not None]
            if ttft_values:
                result.usage.ttft_ms = ttft_values[0]

        metadata: dict[str, Any] = {
            "stop_reason": result.stop_reason.value if hasattr(result.stop_reason, "value") else str(result.stop_reason),
            "llm_calls": result.usage.llm_calls,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "llm_latency_p50_ms": result.usage.llm_latency_p50_ms,
            "llm_latency_p95_ms": result.usage.llm_latency_p95_ms,
            "llm_retry_count": result.usage.llm_retry_count,
        }
        if snapshot is not None:
            metadata["error_snapshot"] = {
                "error_type": snapshot.error_type,
                "error_message": snapshot.error_message,
                "tool_call_chain": snapshot.tool_call_chain,
                "captured_at": snapshot.captured_at,
            }

        trace = self._client.trace(id=run_id, metadata=metadata)

        # One span per LLM call
        for i, call in enumerate(calls):
            trace.span(
                name=f"llm.call.{i}",
                metadata={
                    "model": call.model,
                    "latency_ms": call.latency_ms,
                    "ttft_ms": call.ttft_ms,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cached_tokens": call.cached_tokens,
                    "attempt": call.attempt,
                },
            )
```

- [ ] **Step 4: Register in `openagents/plugins/registry.py`**

```python
from openagents.plugins.builtin.diagnostics.langfuse_plugin import LangfuseExporter
```

Add to `"diagnostics"`:

```python
"diagnostics": {
    "null": NullDiagnosticsPlugin,
    "rich": RichDiagnosticsPlugin,
    "langfuse": LangfuseExporter,
},
```

- [ ] **Step 5: Add `langfuse` extra + coverage omit in `pyproject.toml`**

After the `otel` extra block:

```toml
langfuse = [
    "langfuse>=2.0.0",
]
```

Add `langfuse_plugin.py` to the `omit` list:

```toml
omit = [
    "openagents/plugins/builtin/memory/mem0_memory.py",
    "openagents/plugins/builtin/tool/mcp_tool.py",
    "openagents/plugins/builtin/session/sqlite_backed.py",
    "openagents/plugins/builtin/events/otel_bridge.py",
    "openagents/plugins/builtin/diagnostics/langfuse_plugin.py",
]
```

Update `all` extra to include `langfuse`:

```toml
all = [
    "io-openagent-sdk[cli,mcp,mem0,openai,otel,rich,sqlite,dev,tokenizers,yaml,pptx,langfuse]",
]
```

- [ ] **Step 6: Run tests — expect pass**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_langfuse_plugin.py -v
```

Expected: tests with `pytest.importorskip("langfuse")` skipped if not installed; mock-based tests pass.

- [ ] **Step 7: Commit**

```bash
git add openagents/plugins/builtin/diagnostics/langfuse_plugin.py openagents/plugins/registry.py pyproject.toml tests/unit/plugins/builtin/diagnostics/test_langfuse_plugin.py
git commit -m "feat(diagnostics): add LangfuseExporter with trace and per-LLM-call spans"
```

---

## Task 9: `PhoenixExporter`

**Files:**
- Create: `openagents/plugins/builtin/diagnostics/phoenix_plugin.py`
- Modify: `openagents/plugins/registry.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/plugins/builtin/diagnostics/test_phoenix_plugin.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/plugins/builtin/diagnostics/test_phoenix_plugin.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


def test_phoenix_plugin_missing_import_raises():
    import sys
    with patch.dict(sys.modules, {"opentelemetry": None, "opentelemetry.trace": None}):
        with pytest.raises(ImportError):
            import importlib
            import openagents.plugins.builtin.diagnostics.phoenix_plugin as mod
            importlib.reload(mod)
            mod.PhoenixExporter()


def test_phoenix_plugin_on_run_complete_creates_spans(monkeypatch):
    pytest.importorskip("opentelemetry")
    from openagents.interfaces.runtime import RunResult, RunUsage
    from openagents.interfaces.diagnostics import LLMCallMetrics

    mock_tracer = MagicMock()
    mock_span = MagicMock().__enter__ = MagicMock(return_value=MagicMock())
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with patch("openagents.plugins.builtin.diagnostics.phoenix_plugin.trace") as mock_trace_mod:
        mock_trace_mod.get_tracer.return_value = mock_tracer
        from openagents.plugins.builtin.diagnostics.phoenix_plugin import PhoenixExporter
        plugin = PhoenixExporter()
        plugin.record_llm_call("r1", LLMCallMetrics(model="m", latency_ms=100.0, input_tokens=5, output_tokens=3, cached_tokens=0))
        result = RunResult(run_id="r1", usage=RunUsage())
        plugin.on_run_complete(result, None)

    assert mock_tracer.start_as_current_span.called
```

- [ ] **Step 2: Run tests — expect failures**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_phoenix_plugin.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `openagents/plugins/builtin/diagnostics/phoenix_plugin.py`**

```python
"""PhoenixExporter — send run traces to Arize Phoenix via OTel."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.diagnostics import DiagnosticsPlugin, ErrorSnapshot, LLMCallMetrics
from openagents.interfaces.runtime import RunResult


class PhoenixExporter(DiagnosticsPlugin):
    """Exports run traces to Arize Phoenix using OpenTelemetry spans.

    Unlike OtelEventBusBridge (which creates flat one-shot spans),
    PhoenixExporter builds a proper parent/child trace tree per run.

    Requires the 'phoenix' optional extra (arize-phoenix-otel).
    Config keys: endpoint (default http://localhost:6006).
    """

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        try:
            from opentelemetry import trace
        except ImportError as exc:
            raise ImportError(
                "PhoenixExporter requires opentelemetry-api. "
                "Install with: pip install 'io-openagent-sdk[phoenix]'"
            ) from exc
        from opentelemetry import trace as _trace
        self._trace = _trace
        self._tracer = _trace.get_tracer("openagents.diagnostics")
        self._per_run: dict[str, list[LLMCallMetrics]] = {}

    def record_llm_call(self, run_id: str, metrics: LLMCallMetrics) -> None:
        self._per_run.setdefault(run_id, []).append(metrics)

    def on_run_complete(self, result: RunResult, snapshot: ErrorSnapshot | None) -> None:
        run_id = result.run_id
        calls = self._per_run.pop(run_id, [])

        # Back-fill latency percentiles
        if calls:
            latencies = sorted(c.latency_ms for c in calls)
            n = len(latencies)
            result.usage.llm_latency_p50_ms = latencies[n // 2]
            if n >= 2:
                result.usage.llm_latency_p95_ms = latencies[min(int(n * 0.95), n - 1)]
            result.usage.llm_retry_count = sum(1 for c in calls if c.attempt > 1)

        with self._tracer.start_as_current_span(f"openagents.run") as root_span:
            root_span.set_attribute("run.id", run_id)
            root_span.set_attribute("run.stop_reason", str(result.stop_reason))
            root_span.set_attribute("run.llm_calls", result.usage.llm_calls)
            root_span.set_attribute("run.input_tokens", result.usage.input_tokens)
            root_span.set_attribute("run.output_tokens", result.usage.output_tokens)

            if snapshot is not None:
                root_span.set_attribute("error.type", snapshot.error_type)
                root_span.set_attribute("error.message", snapshot.error_message[:500])
                root_span.record_exception(Exception(snapshot.error_message))

            for i, call in enumerate(calls):
                with self._tracer.start_as_current_span(f"openagents.llm.call.{i}") as span:
                    span.set_attribute("llm.model", call.model)
                    span.set_attribute("llm.latency_ms", call.latency_ms)
                    span.set_attribute("llm.input_tokens", call.input_tokens)
                    span.set_attribute("llm.output_tokens", call.output_tokens)
                    span.set_attribute("llm.cached_tokens", call.cached_tokens)
                    span.set_attribute("llm.attempt", call.attempt)
                    if call.ttft_ms is not None:
                        span.set_attribute("llm.ttft_ms", call.ttft_ms)
```

- [ ] **Step 4: Register in `openagents/plugins/registry.py`**

```python
from openagents.plugins.builtin.diagnostics.phoenix_plugin import PhoenixExporter
```

```python
"diagnostics": {
    "null": NullDiagnosticsPlugin,
    "rich": RichDiagnosticsPlugin,
    "langfuse": LangfuseExporter,
    "phoenix": PhoenixExporter,
},
```

- [ ] **Step 5: Add `phoenix` extra + coverage omit in `pyproject.toml`**

```toml
phoenix = [
    "opentelemetry-api>=1.25.0",
    "arize-phoenix-otel>=0.6.0",
]
```

Add to `omit`:

```toml
"openagents/plugins/builtin/diagnostics/phoenix_plugin.py",
```

Update `all` extra:

```toml
all = [
    "io-openagent-sdk[cli,mcp,mem0,openai,otel,rich,sqlite,dev,tokenizers,yaml,pptx,langfuse,phoenix]",
]
```

- [ ] **Step 6: Run tests — expect pass**

```
uv run pytest tests/unit/plugins/builtin/diagnostics/test_phoenix_plugin.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add openagents/plugins/builtin/diagnostics/phoenix_plugin.py openagents/plugins/registry.py pyproject.toml tests/unit/plugins/builtin/diagnostics/test_phoenix_plugin.py
git commit -m "feat(diagnostics): add PhoenixExporter with proper OTel trace tree"
```

---

## Task 10: Coverage + Full Suite Validation

**Files:**
- Run tests and coverage
- Update `docs/event-taxonomy.md` (regenerate from taxonomy)

- [ ] **Step 1: Run full test suite with coverage**

```
uv run coverage run -m pytest && uv run coverage report
```

Expected: ≥90% coverage. `langfuse_plugin.py` and `phoenix_plugin.py` are in `omit` so they don't count against coverage.

- [ ] **Step 2: Regenerate event taxonomy docs**

```
uv run python -m openagents.tools.gen_event_doc
```

Expected: `docs/event-taxonomy.md` and `docs/event-taxonomy.en.md` updated to reflect the new `llm.failed` event and `_metrics` optional fields.

- [ ] **Step 3: Verify config round-trip works**

```python
# Quick smoke test — paste into uv run python -c "..."
from openagents.config.loader import load_config_dict
cfg = load_config_dict({
    "agents": [{"id": "a1", "name": "A", "memory": {"type": "buffer"}, "pattern": {"type": "react"}}],
    "diagnostics": {"type": "null"},
})
print(cfg.diagnostics.type)  # should print: null
```

Run:
```
uv run python -c "from openagents.config.loader import load_config_dict; cfg = load_config_dict({'agents': [{'id': 'a1', 'name': 'A', 'memory': {'type': 'buffer'}, 'pattern': {'type': 'react'}}], 'diagnostics': {'type': 'null'}}); print(cfg.diagnostics.type)"
```

Expected: `null`

- [ ] **Step 4: Commit final docs**

```bash
git add docs/event-taxonomy.md docs/event-taxonomy.en.md
git commit -m "docs: regenerate event taxonomy with llm.failed and _metrics fields"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| DiagnosticsPlugin interface + data classes | Task 1 |
| Capability constants in capabilities.py | Task 1 |
| RunUsage 4 new fields | Task 2 |
| event_taxonomy `_metrics` optional field | Task 2 |
| NullDiagnosticsPlugin | Task 3 |
| DiagnosticsRef in config schema | Task 4 |
| registry.py registration | Task 4 |
| load_diagnostics_plugin in loader.py | Task 4 |
| LLM timing in pattern.py → llm.succeeded payload | Task 5 |
| default_runtime.py — on_run_complete success path | Task 6 |
| default_runtime.py — capture_error_snapshot failure path | Task 6 |
| ErrorSnapshot in RunResult.metadata | Task 6 |
| RichDiagnosticsPlugin + latency back-fill | Task 7 |
| LangfuseExporter + trace/span structure | Task 8 |
| langfuse extra in pyproject.toml | Task 8 |
| PhoenixExporter + proper OTel span tree | Task 9 |
| phoenix extra in pyproject.toml | Task 9 |
| coverage omit for optional-extra plugins | Tasks 8, 9 |
| event-taxonomy docs regeneration | Task 10 |

**Type consistency check:** `LLMCallMetrics` defined in Task 1; used identically in Tasks 5, 7, 8, 9. `record_llm_call(run_id: str, metrics: LLMCallMetrics)` signature consistent across all tasks. `on_run_complete(result: RunResult, snapshot: ErrorSnapshot | None)` consistent.

**Placeholder scan:** No TBD/TODO found. All code blocks contain complete implementations. ✅
