# Builtin Plugins Expansion + Research-Analyst Example — Design

- Status: approved via brainstorm 2026-04-17
- Scope: single spec, single implementation plan, single PR-shape
- Non-goals: changing kernel protocol, adding new capability constants, adding new `PluginRef` types

## 1. Motivation

Seven of the SDK's seams currently ship only one builtin, which makes the "pluggable" story thin on the ground:

| seam | builtin(s) today |
|---|---|
| `tool_executor` | `safe` |
| `execution_policy` | `filesystem` |
| `followup_resolver` | `basic` |
| `response_repair_policy` | `basic` |
| `session` | `in_memory` |
| `events` | `async` |
| `skills` | `local` |

Meanwhile, examples cover only the extremes — a builtin-only `quickstart` and a product-heavy `production_coding_agent`. There is no mid-density example that demonstrates **multiple new builtins composing**.

This spec adds one additional builtin to each thin seam (except `skills`, intentionally deferred) and a new `examples/research_analyst/` that exercises all of them together with zero external-network dependency.

## 2. High-level plan

Seven new builtins + one new example.

| # | seam | new builtin | problem it solves |
|---|---|---|---|
| 1 | `tool_executor` | `retry` → `RetryToolExecutor` | wraps another executor, retries on classified errors with exponential backoff |
| 2 | `execution_policy` | `composite` → `CompositeExecutionPolicy` | AND/OR composition of child policies |
| 3 | `execution_policy` | `network_allowlist` → `NetworkAllowlistExecutionPolicy` | host/scheme allowlist for network-flavored tools (e.g. `http_request`) |
| 4 | `followup_resolver` | `rule_based` → `RuleBasedFollowupResolver` | regex → template rules; resolve multi-turn Q without LLM roundtrip |
| 5 | `session` | `jsonl_file` → `JsonlFileSessionManager` | append-only NDJSON session persistence |
| 6 | `events` | `file_logging` → `FileLoggingEventBus` | wraps another event bus, appends every event to NDJSON file |
| 7 | `response_repair_policy` | `strict_json` → `StrictJsonResponseRepairPolicy` | salvage JSON from fenced/bare text when response came back as text-only |

Example **`examples/research_analyst/`** simulates a research agent that queries a local stub HTTP server, reads local fixtures, aggregates results, and writes a markdown report. It exercises all 7 new builtins in one natural flow.

## 3. Approach and global conventions

- **Composition, not inheritance**: combinator builtins (`retry`, `composite`, `file_logging`) hold inner plugin refs as `dict[str, Any]` and call the existing `_load_plugin(kind, ref)` from `openagents/plugins/loader.py` during `__init__`. We do not fork the loader.
- **`Config(BaseModel)` on every new builtin** — continues the 0.3.0 schema-exposure pattern so `openagents schema` / `openagents validate` / `openagents list-plugins` pick them up automatically.
- **No kernel protocol changes**: `interfaces/*.py` is untouched; no new capability constants, no new `PluginRef` subclasses in `config/schema.py` beyond existing ones (they already model `dict[str, Any]` config, which is all these builtins need).
- **Pure async**, single-runtime scope — semantics match existing in-memory builtins. No multi-process locking, no distributed coordination.
- **Fail-fast config**: invalid config raises `pydantic.ValidationError` at construction, caught by existing CLI `validate` flow.
- **Zero external network**: example ships an in-process aiohttp stub and never contacts the real internet.

## 4. New builtin specs

### 4.1 `retry` (tool_executor seam)

File: `openagents/plugins/builtin/tool_executor/retry.py`.

```python
class RetryToolExecutor(ToolExecutorPlugin):
    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "safe"})
        max_attempts: int = 3
        initial_delay_ms: int = 200
        backoff_multiplier: float = 2.0
        max_delay_ms: int = 5_000
        retry_on_timeout: bool = True
        retry_on: list[str] = ["RetryableToolError", "ToolTimeoutError"]
```

Behavior:

- `execute(request)` calls `inner.execute(request)`. On `result.success is False` and `type(result.exception).__name__` in `retry_on` (or `ToolTimeoutError` with `retry_on_timeout=True`), sleeps `min(max_delay_ms, initial_delay_ms * backoff_multiplier**i)` ms and retries. Up to `max_attempts` total attempts.
- Last failure returns the original `ToolExecutionResult`, mutated/rebuilt with `metadata.retry_attempts=N`, `metadata.retry_delays_ms=[...]`, `metadata.retry_reason=[...]`.
- `execute_stream` does not retry; it transparently delegates to `inner.execute_stream`.
- `asyncio.CancelledError` during backoff sleep propagates.

Error handling:

- Inner plugin is instantiated at `__init__`; invalid `inner` ref → `PluginLoadError` at load time (early failure).

### 4.2 `composite` (execution_policy seam)

File: `openagents/plugins/builtin/execution_policy/composite.py`.

```python
class CompositeExecutionPolicy(ExecutionPolicyPlugin):
    class Config(BaseModel):
        policies: list[dict[str, Any]]  # each = {"type": "...", "config": {...}} or {"impl": "...", "config": {...}}
        mode: Literal["all", "any"] = "all"
```

Behavior:

- `evaluate(request)`:
  - `mode="all"`: ask each child in order; first `allowed=False` short-circuits and returns `PolicyDecision(allowed=False, reason=<child reason>, metadata={"policy":"composite","decided_by":i,"children":[...]})`. If all pass, return `allowed=True` with merged metadata.
  - `mode="any"`: first `allowed=True` short-circuits to allow. If all deny, return first deny's reason.
  - Empty `policies`: `allowed=True`, metadata `{"policy":"composite","children":[]}`.
- Child raising exception → wrapped as `PolicyDecision(allowed=False, reason=f"child <i> raised: {exc}")` with `metadata.error_type`. Bugs in a sub-policy cannot crash the run.

### 4.3 `network_allowlist` (execution_policy seam)

File: `openagents/plugins/builtin/execution_policy/network.py`.

```python
class NetworkAllowlistExecutionPolicy(ExecutionPolicyPlugin):
    class Config(BaseModel):
        allow_hosts: list[str] = []                       # literal or fnmatch pattern (e.g. "*.example.com")
        allow_schemes: list[str] = ["http", "https"]
        applies_to_tools: list[str] = ["http_request"]
        deny_private_networks: bool = True
```

Behavior:

- If `request.tool_id not in applies_to_tools`: `allowed=True`.
- Extract `url` from `request.params`; `urlparse` to get scheme/host.
- Host matched against `allow_hosts` via `fnmatch.fnmatchcase` (case-insensitive after lowercasing both sides).
- `deny_private_networks`: literal-prefix check for `127.`, `10.`, `192.168.`, `172.16.`–`172.31.`, `::1`, `localhost`. No DNS lookup (no side effects).
- Unparseable URL / missing host → `allowed=False, reason="unparseable URL"`.
- Metadata always: `{"policy":"network_allowlist","host":...,"scheme":...}`.

### 4.4 `rule_based` (followup_resolver seam)

File: `openagents/plugins/builtin/followup/rule_based.py`.

```python
class RuleBasedFollowupResolver(FollowupResolverPlugin):
    class Rule(BaseModel):
        name: str
        pattern: str                      # regex, applied with re.IGNORECASE
        template: str                     # supports {tool_ids} {last_input} {last_output}
        requires_history: bool = True

    class Config(BaseModel):
        rules_file: str | None = None     # JSON file path; loaded and prepended before `rules`
        rules: list[Rule] = []
```

Behavior:

- `resolve(context)`:
  - Walk rules in order. First `re.search(rule.pattern, context.input_text, re.IGNORECASE)` hit wins.
  - If `requires_history` and no `memory_view.history`: return `FollowupResolution(status="abstain", reason="no history")`.
  - Format `template` with variables pulled from `history[-1]`: `tool_ids` joined by `, `; `last_input`; `last_output`.
  - Missing keys in format → substituted via `collections.defaultdict(str, ...)` wrapper; never raises.
  - Return `FollowupResolution(status="resolved", output=rendered, metadata={"rule":rule.name})`.
- No rule matches → return `None` (SDK falls back to model).
- `rules_file` invalid path / invalid JSON → `PluginLoadError` at construction.

### 4.5 `jsonl_file` (session seam)

File: `openagents/plugins/builtin/session/jsonl_file.py`.

```python
class JsonlFileSessionManager(SessionManagerPlugin):
    class Config(BaseModel):
        root_dir: str
        fsync: bool = False
```

Behavior:

- Same in-memory `_states`/`_locks` structure as `InMemorySessionManager` plus persistence.
- Must **override the mutation methods directly** — `append_message`, `save_artifact`, `create_checkpoint`, `set_state` — rather than inheriting base-class implementations that chain through `set_state`. Otherwise a single `append_message` would emit both a `transcript` line and a redundant `state` line.
  - `append_message` → appends `{"type":"transcript","data":message,"ts":ISO}`, mutates in-memory transcript directly.
  - `save_artifact` → appends `{"type":"artifact","data":artifact.to_dict(),"ts":ISO}`, mutates in-memory artifacts directly.
  - `create_checkpoint` → appends `{"type":"checkpoint","data":checkpoint.to_dict(),"ts":ISO}`, mutates in-memory checkpoints.
  - `set_state` → appends one `{"type":"state","data":state,"ts":ISO}` (used when caller stores non-transcript/non-artifact keys).
- On first access to a session (`get_state` or `session()` entry): if `<root>/<sid>.jsonl` exists, replay line-by-line applying each event type to rebuild `_session_transcript`, `_session_artifacts`, `_session_checkpoints`, and `state` dict; then mark the session as "loaded" via an internal `self._loaded: set[str]` guard so subsequent `get_state` does not replay again.
- `fsync=True` flushes+fsyncs every write.
- Startup: `Path(root_dir).mkdir(parents=True, exist_ok=True)`.
- `delete_session(sid)` removes the file and drops in-memory state and `_loaded` membership.
- `list_sessions` returns the union of in-memory session IDs and session IDs inferred from `*.jsonl` filenames under `root_dir`.
- Corrupted line while replaying → `logger.warning("jsonl_file: skipped bad line %d in %s")` and continue.
- Write failure bubbles up (disk full etc.).

Declared capabilities match `InMemorySessionManager`: `{SESSION_MANAGE, SESSION_STATE, SESSION_TRANSCRIPT, SESSION_ARTIFACTS, SESSION_CHECKPOINTS}`.

### 4.6 `file_logging` (events seam)

File: `openagents/plugins/builtin/events/file_logging.py`.

```python
class FileLoggingEventBus(EventBusPlugin):
    class Config(BaseModel):
        inner: dict[str, Any] = Field(default_factory=lambda: {"type": "async"})
        log_path: str
        include_events: list[str] | None = None  # None = all events
        max_history: int = 10_000
```

Behavior:

- `subscribe` / `get_history` / `clear_history` fully delegate to inner.
- `emit(name, **payload)`:
  - `await inner.emit(name, **payload)` first (so subscribers still run).
  - If `include_events is None` or `name in include_events`, append `{ "name": name, "payload": payload, "ts": ISO }` as one JSON line to `log_path` (open in `"a"`, `ensure_ascii=False`, `default=str` for non-serializable).
  - File write error → `logger.error("file_logging: append failed: %s", exc)`; does not raise. Event delivery is the primary guarantee.

### 4.7 `strict_json` (response_repair_policy seam)

File: `openagents/plugins/builtin/response_repair/strict_json.py`.

```python
class StrictJsonResponseRepairPolicy(ResponseRepairPolicyPlugin):
    class Config(BaseModel):
        min_text_length: int = 8
        strip_code_fence: bool = True
        fallback_to_basic: bool = True
```

Behavior:

- `repair_empty_response(...)`:
  - Concatenate all `text` fields from `assistant_content` blocks where `type=="text"`.
  - If concatenated text length < `min_text_length`: fallback or abstain per `fallback_to_basic`.
  - If `strip_code_fence`: regex-match ```` ```(?:json)?\n(.*?)\n``` ```` (DOTALL); first capture becomes candidate.
  - Else scan for the first `{...}` or `[...]` balanced span.
  - Try `json.loads(candidate)`. On success: `ResponseRepairDecision(status="repaired", output=[{"type":"text","text": json.dumps(obj, ensure_ascii=False)}], metadata={"salvaged_from":"fenced_code|bare_json","keys":[...]})`.
  - On failure:
    - `fallback_to_basic=True`: delegate to `BasicResponseRepairPolicy().repair_empty_response(...)`.
    - `fallback_to_basic=False`: `ResponseRepairDecision(status="abstain", reason="no JSON extractable")`.

## 5. Registry wiring

`openagents/plugins/registry.py` — add:

```python
# imports
from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor
from openagents.plugins.builtin.execution_policy.composite import CompositeExecutionPolicy
from openagents.plugins.builtin.execution_policy.network import NetworkAllowlistExecutionPolicy
from openagents.plugins.builtin.followup.rule_based import RuleBasedFollowupResolver
from openagents.plugins.builtin.session.jsonl_file import JsonlFileSessionManager
from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus
from openagents.plugins.builtin.response_repair.strict_json import StrictJsonResponseRepairPolicy
```

Then extend `_BUILTIN_REGISTRY`:

```python
"tool_executor":         {"safe": SafeToolExecutor, "retry": RetryToolExecutor},
"execution_policy":      {"filesystem": FilesystemExecutionPolicy,
                          "composite": CompositeExecutionPolicy,
                          "network_allowlist": NetworkAllowlistExecutionPolicy},
"followup_resolver":     {"basic": BasicFollowupResolver,
                          "rule_based": RuleBasedFollowupResolver},
"session":               {"in_memory": InMemorySessionManager,
                          "jsonl_file": JsonlFileSessionManager},
"events":                {"async": AsyncEventBus,
                          "file_logging": FileLoggingEventBus},
"response_repair_policy":{"basic": BasicResponseRepairPolicy,
                          "strict_json": StrictJsonResponseRepairPolicy},
```

Matching `__init__.py` exports per seam directory (follow existing conventions).

## 6. Example: `examples/research_analyst/`

**Pattern choice.** The builtin patterns (`react`, `plan_execute`, `reflexion`) do **not** automatically consult `ctx.followup_resolver`. In the current codebase that hook is called by the app-layer pattern (see `examples/production_coding_agent/app/plugins.py:265`). For the followup resolver to actually short-circuit a run, `research_analyst` must ship a thin app-layer pattern that wraps `react` and calls `ctx.followup_resolver.resolve(context=ctx)` first, falling through to the wrapped pattern if the resolution is `None` or `abstain`. This mirrors the production example and is the accepted idiom.

### 6.1 Layout

```
examples/research_analyst/
├── __init__.py
├── README.md                       # usage + what each new builtin demonstrates
├── agent.json
├── app/
│   ├── __init__.py
│   ├── stub_server.py              # aiohttp stub on 127.0.0.1:<random>
│   ├── followup_pattern.py         # thin wrapper: followup_resolver first, then ReAct
│   ├── fixtures/
│   │   ├── knowledge/topic-a.md
│   │   ├── knowledge/topic-b.md
│   │   └── knowledge/index.json
│   └── followup_rules.json
├── run_demo.py
└── sessions/                       # written to at runtime; add to .gitignore
```

### 6.2 Stub server (`app/stub_server.py`)

- `async def start_stub_server() -> AsyncContextManager[str]`: starts an `aiohttp.web.Application` bound to `127.0.0.1:0`, reads the actual port, yields `f"http://127.0.0.1:{port}"`, cleans up on exit.
- Routes:
  - `GET /pages/topic-a` — returns fixed JSON `{"title": ..., "summary": ...}`.
  - `GET /pages/topic-b` — returns fixed markdown `text/markdown`.
  - `GET /pages/flaky` — uses a per-server `_flaky_counter` (module-local counter reset per server start); returns `503` on attempts 1–2, `200` with payload on attempt 3.
- Counter is instance-scoped (not module-global with contextvars — simpler and sufficient given each test / demo starts a fresh server).

### 6.3 `agent.json`

Uses all 7 new builtins. `pattern` uses `impl: "examples.research_analyst.app.followup_pattern:FollowupFirstReActPattern"` so the pattern can consult `ctx.followup_resolver` before invoking `react`. Provider defaults to `mock`; `--live` flag in `run_demo.py` switches to `minimax` if `MINIMAX_API_KEY` present.

### 6.4 Followup rules (`app/followup_rules.json`)

Two rules: `queried_urls` and `last_tools`. Template uses `{tool_ids}` / `{last_input}`.

### 6.5 `run_demo.py`

1. `async with start_stub_server() as base_url:` — spin up stub.
2. Load `agent.json`, substitute `{BASE_URL}` placeholder in system prompt / `context_hints` with `base_url`.
3. `runtime = Runtime.from_dict(config)`.
4. **Run #1**: user asks "Research topic-a and topic-b, include the flaky source, and write a report."
   - Mock provider emits a scripted tool_use sequence: `http_request /pages/topic-a`, `http_request /pages/flaky` (x3 due to retry), `read_file fixtures/knowledge/index.json`, `write_file sessions/report.md`, then a final assistant message.
5. **Run #2**: user asks "你刚才查了哪些 URL?" — `rule_based_followup` resolves locally; mock provider should not be called.
6. Print session path, events.ndjson path, artifact list. Teardown.

## 7. Error handling (cross-cutting)

- All builtins are internally defensive — see per-builtin specs. No new exception types introduced.
- Example uses `try/finally` around server lifecycle.
- Stub server's flaky counter is per-instance to prevent cross-test pollution.
- CLI validate flow surfaces `Config` validation errors unchanged.

## 8. Testing plan

### 8.1 Unit tests (`tests/unit/`)

One new file per builtin:

| file | key scenarios |
|---|---|
| `test_retry_tool_executor.py` | first-success no-retry / retryable-to-success / exhaustion records `retry_attempts` / timeout gated by `retry_on_timeout` / non-retryable returns immediately / `execute_stream` passthrough / `CancelledError` propagates during sleep |
| `test_composite_execution_policy.py` | all-mode first-deny wins / all-allow passes / any-mode first-allow wins / empty policies allows / child exception wrapped as deny / merged metadata |
| `test_network_allowlist_policy.py` | exact host hit / fnmatch `*.example.com` / scheme denied / non-allowlist denied / `applies_to_tools` filter skips / `deny_private_networks` for `127.`, `10.`, `192.168.`, `172.20.` / unparseable URL denied |
| `test_rule_based_followup.py` | regex hit → resolved / no hit → None / hit but no history → abstain / missing template key safe / rules_file loaded and merged / invalid rules_file raises `PluginLoadError` |
| `test_jsonl_file_session.py` | append+reload transcript / artifacts round-trip / checkpoint round-trip / reopen new manager recovers state / corrupted line skipped with warning / delete_session removes file / list_sessions scans |
| `test_file_logging_event_bus.py` | inner forwarded / each NDJSON line parses / `include_events` filter / non-serializable payload survives via `default=str` / open-failure monkeypatch does not disrupt inner emit |
| `test_strict_json_response_repair.py` | fenced `json` salvage / bare JSON salvage / non-JSON + fallback path / non-JSON + abstain path / `min_text_length` floor |

Each file also asserts registration: `get_builtin_plugin_class("<kind>", "<name>") is <ClassName>`.

### 8.2 Integration test

`tests/integration/test_research_analyst_example.py` (matches `test_production_coding_agent_example.py` style), 3 cases:

1. `test_research_analyst_end_to_end`
   - Happy-path across 2 runs. Assertions:
     - `events.ndjson` contains a tool-execution event whose metadata records `retry_attempts >= 2` for the `/pages/flaky` call.
     - Report markdown artifact exists under `sessions/`.
     - Run #2 is resolved by the followup path: `RunResult.metadata` (or equivalent, e.g. a dedicated `run.resolved_by` event emitted by the wrapper pattern) indicates `"resolved_by": "followup_resolver"` and the mock provider's call counter did not increment during run #2.
     - A fresh `JsonlFileSessionManager` pointing at the same `root_dir` can replay and finds the full transcript + artifact + a markdown report.
2. `test_research_analyst_policy_denial`
   - With stub URL rewritten to a non-allowlisted host, tool call is denied. Assert `PolicyDecision.reason` contains `"network_allowlist"`; no write under disallowed path.
3. `test_research_analyst_strict_json_repair`
   - Mock provider returns fenced JSON in a text block; assert `strict_json` repairs to valid JSON and flow continues.

### 8.3 CLI smoke

Extend existing `tests/unit/test_cli_schema.py` (or closest equivalent) to assert `openagents list-plugins` includes the 7 new keys, and `openagents schema` produces schemas for each.

### 8.4 Coverage

- `uv run pytest -q` must be green.
- `uv run coverage run -m pytest && uv run coverage report` ≥ 90% overall and ≥ 90% per new file.
- Integration tests run offline.

## 9. Docs updates

- `examples/README.md`: add `research_analyst/` section alongside the existing two.
- `docs/examples.md`: add a section walking through which builtin each part of the example exercises.
- `docs/developer-guide.md`: brief pointer to each new builtin type key.
- No new top-level doc files.

## 10. Out of scope (deferred)

- Second `skills` builtin (still has only `local`). Left alone because adding a multi-root skills manager would drag in skill-resolution semantics that belong in a separate spec.
- Any `context_assembler` addition — the seam already has 4 builtins, and the repo deliberately keeps this seam LLM-free (see `TruncatingContextAssembler` docstring).
- Retry semantics for streaming tool execution.
- Persistent (cross-process) session locking.

## 11. Risks and mitigations

| risk | mitigation |
|---|---|
| Combinator builtins depend on loader internals | Reuse existing `_load_plugin`; add a thin re-export if needed rather than duplicating resolution logic |
| Integration test flakiness from aiohttp port binding | Bind to `127.0.0.1:0`, read actual port; one-shot per test |
| Stub counter cross-test pollution | Per-instance counter (new server per test/demo); explicit teardown |
| File-logging event bus tying up I/O on hot path | Sync append per emit is OK for single-run demos; document that high-throughput production should use a real async sink |
| `deny_private_networks` false positives for users on LAN | Flag is user-opt-in per config; example sets `deny_private_networks=false` because stub runs on 127.0.0.1 |

## 12. Rollout

Single PR-shape, single plan. Order of implementation will be established by the writing-plans skill; expected shape is:

1. Builtins bottom-up (no inter-dependency between the seven — they can land in any order).
2. Registry wiring after each builtin so tests can import via `type` key.
3. Example wired last, once all seven builtins are in place.
4. Docs last.

## 13. Implementation errata

Two deviations from the original spec were accepted during implementation. Both are traceable to SDK-level constraints rather than gaps in the changeset, and are documented here so future readers can reason about the observable-behavior envelope.

### 13.1 Integration test §8.2 Case 3 not written

The third integration test (`test_research_analyst_strict_json_repair`) was not added. Driving `StrictJsonResponseRepairPolicy` through `Runtime.run_detailed` would require the LLM to return an empty `assistant_content` after all internal retry fallbacks, a state that is hard to synthesize with a plain scripted `LLMClient` without monkeypatching internal pattern machinery.

**Mitigation:** `tests/unit/test_strict_json_response_repair.py` covers every salvage branch directly (fenced / bare / mixed-case fence / fallback-to-basic / abstain-when-flag-false / `min_text_length` floor). The builtin is wired into `examples/research_analyst/agent.json` and loads successfully at runtime.

### 13.2 `retry_attempts >= 2` assertion in `events.ndjson` replaced with indirect proof

`RetryToolExecutor.execute` stamps `metadata.retry_attempts` onto `ToolExecutionResult`, but `_BoundTool.invoke` in `openagents/plugins/builtin/runtime/default_runtime.py` returns only `result.data` when feeding the result into the ReAct loop — the metadata is discarded before any `tool.*` event is emitted. Surfacing executor metadata in events would require an SDK-internal change to widen the bound-tool → event-payload path, which is out of scope for this changeset.

**Mitigation:** `test_research_analyst_end_to_end` proves retry fired indirectly. The stub's `/pages/flaky` route sleeps past the 200 ms executor timeout on the first two attempts; `report.md` can only be written if `RetryToolExecutor` actually retried the timed-out call on its third attempt (at which point the stub returns `_FLAKY_OK`). The inline comment in the test makes this causal chain explicit.

### 13.3 Follow-up for a future changeset

- Add a public re-export of `_load_plugin` in `openagents/plugins/loader.py` (e.g. `load_child_plugin(seam, ref)`). There are now four external callers (`memory/chain.py`, `tool_executor/retry.py`, `execution_policy/composite.py`, `events/file_logging.py`) reaching into a private symbol; a thin public wrapper would remove the underscore-violation and preserve the call pattern.
- Consider widening `_BoundTool`'s boundary so `ToolExecutionResult.metadata` is surfaced to `tool.*` events, which would enable direct `retry_attempts` assertions and give observability to other executor-layer diagnostics.
- Consider aligning pre-existing builtins (`SafeToolExecutor`, `FilesystemExecutionPolicy`) to the `class Config(BaseModel)` / `model_validate` pattern that the 0.3.x additions established, so the new convention does not leave the old code stylistically behind.
