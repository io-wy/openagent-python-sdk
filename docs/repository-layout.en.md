# Repository Layout

This document answers one question: what is each directory in this repository responsible for?

## Top Level

```text
openagent-py-sdk/
  README.md
  README_EN.md
  README_CN.md
  pyproject.toml
  uv.lock
  openagents/
  docs/
  examples/
  skills/
  tests/
```

## Directory Responsibilities

### `openagents/`

Primary SDK source code.

Contains:

- `config/` — config loader / schema / validator (`AppConfig` Pydantic models)
- `runtime/` — Runtime facade and DefaultRuntime; `stream_projection.py` handles event → `RunStreamChunk` mapping
- `plugins/` — builtin plugin registry and loader (`plugins/loader.py`)
- `plugins/builtin/` — builtin plugins grouped by seam: `runtime/`, `session/`, `events/`, `skills/`, `memory/`, `pattern/`, `tool/`, `tool_executor/`, `execution_policy/`, `context/`, `followup/`, `response_repair/`
- `llm/providers/` — `anthropic`, `openai_compatible`, `mock` LLM clients sharing `_http_base.py`
- `interfaces/` — stable kernel protocol dataclasses (`RunRequest`, `RunResult`, `RunContext`, …); `typed_config.py` provides `TypedConfigPluginMixin`; `event_taxonomy.py` declares schema for all events
- `observability/` — structured logging subsystem: `LoggingConfig`, `configure()`, filters (`filters.py`), rich renderer (`_rich.py`), loguru multi-sink backend (`_loguru.py`, optional extra), redaction (`redact.py`), error formatting (`errors.py`)
- `cli/` — CLI subcommand implementations: `schema_cmd.py`, `validate_cmd.py`, `list_plugins_cmd.py`; entry point at `__main__.py` / `main.py`
- `errors/` — error hierarchy and "did you mean?" hint helpers (`exceptions.py`, `suggestions.py`)
- `utils/` — `hotreload.py` (backing `Runtime.reload()`), and other general utilities

### `docs/`

The single developer documentation tree.

Recommended entry points:

- [docs/README.md](README.md)
- [docs/developer-guide.md](developer-guide.md)
- [docs/seams-and-extension-points.md](seams-and-extension-points.md)
- [docs/examples.md](examples.md)

Other key documents:

- [docs/configuration.md](configuration.md) — JSON configuration reference
- [docs/plugin-development.md](plugin-development.md) — plugin development guide
- [docs/api-reference.md](api-reference.md) — Python API reference
- [docs/builtin-tools.md](builtin-tools.md) — builtin tools catalog
- [docs/stream-api.md](stream-api.md) — streaming API (`run_stream`) reference
- [docs/cli-reference.md](cli-reference.md) — CLI command reference (`openagents schema/validate/list-plugins`)
- [docs/observability.md](observability.md) — structured logging and observability
- [docs/event-taxonomy.md](event-taxonomy.md) — event taxonomy table
- [docs/migration-0.2-to-0.3.md](migration-0.2-to-0.3.md) — migration guide

!!! note
    `docs/superpowers/` contains internal design documents (specs / plans) and is not published.

### `examples/`

Actively maintained runnable examples in this repository.

Currently only two groups are maintained:

- `quickstart/`
  - Minimal builtin-only kernel entry point
- `production_coding_agent/`
  - High-density design, app-defined protocol style example

`examples/README.md` handles example navigation; `docs/examples.md` provides a more complete learning path and placement guide.

### `skills/`

App-layer skills directory. Currently contains:

- `skills/openagent-agent-builder/` — agent construction helper skill; see [docs/openagent-agent-builder.md](openagent-agent-builder.md)

### `tests/`

Validates the current repo truth, not historical legacy structures. Coverage floor: **92%** (`pyproject.toml` `[tool.coverage.report].fail_under`).

- `tests/unit/`
  - Unit validation for loader, runtime, providers, repo structure, etc.
  - `tests/unit/test_builtin_docstrings_are_three_section.py` — regression guard: all builtin plugin docstrings must use three-section Google-style format
- `tests/integration/`
  - Config/example-level integration validation
- `tests/fixtures/`
  - Custom plugin samples (`custom_plugins.py`, `runtime_plugins.py`), which also serve as plugin development references

## Documentation Topology

To avoid duplication and drift, the documentation responsibilities are fixed:

- `README.md`
  - Package entry point, minimal quickstart, navigation
- `README_EN.md` / `README_CN.md`
  - Full project description
- `docs/`
  - Developer and structural documentation
- `examples/README.md`
  - Example directory navigation

## What Is Intentionally Absent

The repository no longer treats the following historical surfaces as active structure:

- `docs-v2/`
- `openagent_cli/`
- Deleted legacy example directories

If these are ever restored in the future, they should be restored with real directories and real tests, not just documentation references.
