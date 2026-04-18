# Changelog

## [0.4.0] - 2026-04-19

### Added

- New builtin tool `shell_exec`: allowlist-aware `asyncio.create_subprocess_exec` wrapper with cwd/env/timeout/capture-bytes controls. (`openagents/plugins/builtin/tool/shell_exec.py`)
- New builtin tool `tavily_search`: REST fallback for Tavily MCP. Reads `TAVILY_API_KEY` from env. (`openagents/plugins/builtin/tool/tavily_search.py`)
- New builtin memory `markdown_memory`: human-readable file-backed long-term memory (MEMORY.md index + per-section files) for user goals / feedback / decisions / references. Supports `capture / forget / list_entries / inject / writeback / retrieve`. (`openagents/plugins/builtin/memory/markdown_memory.py`)
- New builtin tool `remember_preference`: companion to `markdown_memory` for agent-side preference capture via `context.state["_pending_memory_writes"]`. (`openagents/plugins/builtin/tool/memory_tools.py`)
- New utility `openagents.utils.env_doctor`: reusable environment check framework with built-in Python/Node/npm/CLI/EnvVar checks and atomic dotenv persistence. (`openagents/utils/env_doctor.py`)
- New CLI helper `openagents.cli.wizard`: Rich + questionary `Wizard` component for building multi-step interactive CLIs with Protocol-based `WizardStep`. (`openagents/cli/wizard.py`)
- New example app `examples/pptx_generator/`: production-grade 7-stage interactive PPT generator CLI (`pptx-agent`). Includes 5 agent patterns (intent / research / outline / theme / slide-generator), 7 wizard steps, 5 PptxGenJS slide templates, and the vendored pptx-generator skill as reference.

### Changed

- Bumped `version` to 0.4.0.
- New `pptx` optional-dependency group: `questionary`, `python-dotenv`, `httpx`, plus the `rich` and `mcp` extras.
- Registered 4 new builtins in `plugins/registry.py` (`markdown_memory`, `shell_exec`, `tavily_search`, `remember_preference`).
- Added new console script: `pptx-agent = "examples.pptx_generator.cli:main_sync"`.

### Docs

- Added `docs/pptx-agent-cli.md` (Chinese) + `docs/pptx-agent-cli.en.md` (English).
- Updated `docs/examples.md`, `docs/seams-and-extension-points.md`, `docs/builtin-tools.md` (and their English mirrors).

## 0.3.0 — 2026-04-16

Kernel completeness release. Deepens existing contracts without adding new seams.
See `docs/superpowers/specs/2026-04-16-openagents-sdk-kernel-completeness-design.md`
for the design and `docs/migration-0.2-to-0.3.md` for upgrade guidance.

### Breaking

- **`RunResult` is now generic: `RunResult[OutputT]`.** Existing untyped callers keep
  equivalent behavior through the implicit `RunResult[Any]`.
- **`context_assembler.type = "summarizing"` is rejected at plugin load time.** The old
  implementation never summarized, only truncated. Rename to `"truncating"`, or pick
  one of the new strategies: `"head_tail"`, `"sliding_window"`, `"importance_weighted"`.
- Module `openagents.plugins.builtin.context.summarizing` renamed to `...context.truncating`.
  `SummarizingContextAssembler` class renamed to `TruncatingContextAssembler`.

### Added

- **`Runtime.run_stream(request) -> AsyncIterator[RunStreamChunk]`** — unified
  event-level stream projection of runtime events. Synchronous equivalents:
  `stream_agent_with_dict`, `stream_agent_with_config`.
- **`RunRequest.output_type` + `Pattern.finalize()`** for typed structured output.
  Runtime validates the pattern's raw output against a pydantic `BaseModel` and
  auto-retries on `ModelRetryError` up to `RunBudget.max_validation_retries`
  (default 3).
- **Tool-side `ModelRetryError`** routed through `pattern.call_tool` with a per-
  tool retry counter; repeated retries beyond the budget escalate to
  `PermanentToolError`. Emits `tool.retry_requested` on each retry.
- **Cost tracking** on `RunUsage`: `cost_usd`, `cost_breakdown`,
  `input_tokens_cached`, `input_tokens_cache_creation`. None-sticky
  semantics propagate unknown cost through the run.
- **`RunBudget.max_cost_usd`** enforced centrally at pre- and post-call
  checkpoints; cost-unavailable path emits a single `budget.cost_skipped`
  event.
- **Provider-declared pricing** on Anthropic and OpenAI-compatible clients;
  `LLMOptions.pricing` threads per-field overrides through the registry.
- **`LLMClient.count_tokens`** with tiktoken override for OpenAI-compatible
  providers, `len//4` fallback elsewhere with one-time WARN per client.
- **Three new token-aware context assemblers**: `HeadTailContextAssembler`,
  `SlidingWindowContextAssembler`, `ImportanceWeightedContextAssembler`.
- **`openagents` CLI** with three subcommands (zero runtime side-effects):
  `schema`, `validate`, `list-plugins`. Install entry via
  `[project.scripts]`; also invokable as `python -m openagents`.
- **`RunStreamChunk` / `RunStreamChunkKind`** kernel models.
- **`OutputValidationError`** under `ExecutionError`; extended
  `BudgetExhausted` with typed `kind/current/limit`; extended
  `ModelRetryError` with `validation_error`.
- **New events**: `llm.delta`, `usage.updated`, `validation.retry`,
  `tool.retry_requested`, `budget.cost_skipped`, `artifact.emitted`.

### Optional dependencies

- `[tokenizers]` — installs `tiktoken>=0.7.0` for accurate
  OpenAI-compatible token counting.
- `[yaml]` — installs `pyyaml>=6.0` for `openagents schema --format yaml`.
- `[all]` now includes both.

### Removed

- `openagents/config/validator.py` — dead code left over from the 0.2.0
  Pydantic migration; Pydantic validators now own config validation.

### Version

- `pyproject.toml`: `0.2.0` → `0.3.0`.
