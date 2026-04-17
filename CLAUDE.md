# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment & Commands

This project is managed with `uv`; do not use `pip`/`venv` directly.

```bash
uv sync                                    # install/sync deps (including dev extras)
uv run pytest -q                           # full test suite
uv run pytest -q tests/unit/test_runtime_core.py::TestName::test_case   # single test
uv run pytest -q tests/integration                                       # only integration tests
uv run coverage run -m pytest && uv run coverage report                  # coverage (floor: 90%)
uv add <pkg>                               # add a runtime dependency
```

Examples require `MINIMAX_API_KEY` (MiniMax's Anthropic-compatible endpoint):

```bash
uv run python examples/quickstart/run_demo.py
uv run python examples/production_coding_agent/run_demo.py
uv run python examples/production_coding_agent/run_benchmark.py
```

Coverage is configured in `pyproject.toml` with `fail_under = 90`; `mem0_memory.py` and `mcp_tool.py` are intentionally omitted (optional extras). Tests inject `skills/openagent-agent-builder/src` onto `sys.path` via `tests/conftest.py` — the agent-builder skill is tested as if it were in-tree.

## Repo-Wide Rule (from AGENTS.md)

When adding, removing, or changing code under `openagents/`, you **must** add/update/remove the corresponding tests in the same change. The test suite and the source are co-evolved; do not land source changes alone.

## Architecture: Three-Layer Mental Model

This SDK is a **single-agent runtime kernel**. It deliberately does *not* own multi-agent teams, mailboxes, approval UX, or product workflow. The single most important rule when extending it: **don't push product semantics into the kernel.** The layering is:

1. **Kernel protocol** (`openagents/interfaces/`) — stable dataclasses: `RunRequest`, `RunResult`, `RunContext[DepsT]`, `ToolExecutionRequest`, `ContextAssemblyResult`, `SessionArtifact`, `StopReason`. Change these rarely.
2. **SDK seams** — the fixed set of pluggable roles loaded by `plugins/loader.py`:
   - *capability*: `memory`, `pattern`, `tool`
   - *execution*: `tool_executor`, `execution_policy`, `context_assembler`
   - *semantic recovery*: `followup_resolver`, `response_repair_policy`
   - *app infra (top-level)*: `runtime`, `session`, `events`, `skills`
3. **App-defined middle protocol** — product semantics (task envelopes, planner state, permission models, artifact taxonomies) live in user code and ride on `RunContext.state` / `.scratch` / `.assembly_metadata`, `RunRequest.context_hints`, and `RunArtifact.metadata`. Adding a new seam requires: cross-app reuse, runtime-behavior impact, independent selector + lifecycle, and a committed builtin default + tests. Otherwise keep it in the app layer.

See `docs/seams-and-extension-points.md` for the decision tree ("which seam does this belong to?").

## Runtime Flow (builtin)

`Runtime.from_config(path)` / `Runtime.from_dict(payload)` → `load_runtime_components` wires top-level `runtime`/`session`/`events`/`skills`. `Runtime.run_detailed(request)` then drives `DefaultRuntime.run()` which (in order): prepares skills for the session, acquires session lock, runs `context_assembler.assemble()`, rebinds tools through `execution_policy + tool_executor`, calls `pattern.setup()`/`memory.inject()`/`pattern.execute()`/`memory.writeback()`, persists transcript/artifacts, calls `context_assembler.finalize()`, returns `RunResult`.

Two cache lifetimes to keep straight:
- Agent plugin bundles are keyed by `(session_id, agent_id)`.
- Builtin LLM clients are keyed by `agent.id` only.
`Runtime.reload()` re-parses config and invalidates LLM clients for changed agents, but does **not** hot-swap top-level `runtime`/`session`/`events`.

## Plugin Loader

`openagents/plugins/loader.py` resolves every plugin ref via:
1. `impl` (Python dotted path) if present, else `type` looked up in the builtin registry (`plugins/registry.py`) or decorator registry (`decorators.py`).
2. Instantiation tries `factory(config=config)` → `factory(config)` → `factory()`.
3. Capability + required-method checks (see `interfaces/capabilities.py`).

Decorator registries (`@tool`, `@memory`, `@pattern`, `@runtime`, `@session`, `@event_bus`, `@tool_executor`, `@execution_policy`, `@context_assembler`, `@followup_resolver`, `@response_repair_policy`) are process-local — the module declaring them must be imported before config load, or the `type` name won't resolve. Class-based plugins are the recommended shape.

## Where Things Live

- `openagents/runtime/runtime.py` — `Runtime` facade
- `openagents/plugins/builtin/` — default implementations grouped by seam (`runtime/`, `session/`, `events/`, `skills/`, `memory/`, `pattern/`, `tool/`, `tool_executor/`, `execution_policy/`, `context/`, `followup/`, `response_repair/`)
- `openagents/config/{loader,schema,validator}.py` — JSON config parsing and `AppConfig` pydantic models
- `openagents/llm/providers/` — `anthropic`, `openai_compatible`, `mock` clients sharing `_http_base.py`
- `openagents/utils/hotreload.py` — powers `Runtime.reload()`
- `tests/fixtures/` — reference custom plugins; `tests/unit/test_plugin_loader.py` and `test_runtime_orchestration.py` double as plugin-author examples

## Docs Topology

Developer docs are consolidated under `docs/` (Chinese-primary, with English/Chinese READMEs at root). Key entry points: `docs/developer-guide.md`, `docs/seams-and-extension-points.md`, `docs/configuration.md`, `docs/plugin-development.md`, `docs/api-reference.md`, `docs/examples.md`, `docs/repository-layout.md`. Only `quickstart` and `production_coding_agent` examples are currently maintained — don't reference or re-add the deleted historical example directories.
