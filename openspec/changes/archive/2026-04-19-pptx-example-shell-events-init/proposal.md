## Why

The `examples/pptx_generator/` example is advertised as the SDK's flagship CLI demo, but it under-consumes SDK capabilities in three places that together make the example feel unfinished relative to its own previously-approved design:

1. **`LayoutRenderer` is orphaned** — `examples/pptx_generator/wizard/_layout.py` already implements the Rich `Layout` shell (status bar / sidebar tree / main / log tail) and it has unit-test coverage, yet `cli.py::run_wizard` never attaches it. The prior archived change (`archive/2026-04-19-pptx-example-full-interactions/` task 3.1) deferred the wiring work citing Windows `Live` + `questionary` interplay risk, leaving a dangling piece of UI that the `pptx-wizard-ui` spec already requires.
2. **No event artifact on disk** — the example persists `project.json` (deck state) and ships `PrettyEventBus` (stderr rendering), but does not persist the event stream to a file. Meanwhile, `openagents replay <path>` (landed in the active `enhance-builtin-cli` change) accepts JSONL event streams and the `FileLoggingEventBus` / `jsonl_file` session plugins are already in the builtin registry. Users have no artifact to hand to `openagents replay` and debugging a finished run is opaque.
3. **`openagents init --template pptx-wizard` scaffolds an unrelated stub** — `openagents/cli/commands/init.py::_AGENT_PPTX` emits a single-agent `react` config with an empty tools list, not the 5-agent / `IntentAnalystPattern` / Tavily-MCP structure that the real example uses. A new user running `openagents init --template pptx-wizard` gets something that bears no resemblance to `examples/pptx_generator/`, defeating the template's discovery value.

This is a pure additive polish pass — no kernel changes, no new seams, no PatternPlugin surgery. It consumes SDK capabilities (`rich.Layout`, `FileLoggingEventBus` / `jsonl_file` session, `openagents replay`) that the SDK already ships but the example has not yet adopted.

## What Changes

- **Persistent Layout shell wired into `run_wizard`** — `examples/pptx_generator/cli.py::run_wizard` constructs one `LayoutRenderer` plus a `LogRing(max_lines=5)` before entering `Wizard.run()`, passes the console-facing renderable + log ring down to each stage's `render()`, and ensures the layout repaints on every stage transition. The Windows-safe pattern: **do not** hold an open `Live(...)` context across `questionary.ask_async()` — instead, each stage manually re-prints the layout snapshot (`console.print(layout_renderer.render(project))`) before its own interactive prompt, and the `LogRing` captures the stage's stdout tail via a small `RingLogHandler` attached to the example's logger. No `rich.Live` is used.
- **`outputs/<slug>/events.jsonl` event persistence** — `cli.py::run_wizard` augments the runtime's event bus with a second subscriber that writes every event to `outputs/<slug>/events.jsonl` using the builtin `FileLoggingEventBus` plugin. The JSONL follows the `EVENT_SCHEMA_VERSION` contract documented in `docs/cli.md`. `README.md` gains a "Replay a finished run" section instructing users to invoke `openagents replay outputs/<slug>/events.jsonl`. The `PrettyEventBus` stderr rendering remains unchanged.
- **`openagents init --template pptx-wizard` scaffold alignment** — `openagents/cli/commands/init.py::_AGENT_PPTX` is rewritten to emit a minimal-but-representative agent.json that: (a) declares at least an `intent-analyst` and a `slide-generator` agent, (b) uses `chain` memory with `window_buffer + markdown_memory`, (c) references `PrettyEventBus` (or its moved `openagents.cli._events`-backed twin), and (d) the generated `README.md` explicitly points to `examples/pptx_generator/` for the full pipeline. The scaffold remains runnable against the `mock` provider — no local Tavily key required.
- **Docs** — `examples/pptx_generator/README.md` gains a "Replay" + "Layout" section; `docs/pptx-agent-cli.md` (+ `.en.md`) documents the new behavior; `docs/cli.md` adds a cross-link from the `init` section to this example.
- **Tests** — new unit tests cover (i) layout-renderer-is-called-between-stages in `run_wizard` (via mock console), (ii) `events.jsonl` is written + round-trips through `openagents replay` subcommand dispatch, (iii) the new scaffold output parses as valid `AppConfig` and includes the multi-agent structure. Integration test (`tests/integration/test_pptx_generator_example.py`) gains an assertion that `events.jsonl` exists after a mock-LLM wizard completion.

No **BREAKING** changes. The existing `pptx-agent new/resume/memory` CLI contract is unchanged. The existing `openagents init --template pptx-wizard` command keeps the same flags and exit codes; only the scaffold content changes.

## Capabilities

### New Capabilities

- `pptx-event-replay`: Project-scoped event-stream persistence for the pptx-generator example. Every `pptx-agent new` / `resume` run writes an append-only JSONL event log to `outputs/<slug>/events.jsonl` that the builtin `openagents replay` command consumes without further transformation.

### Modified Capabilities

- `pptx-wizard-ui`: The "Rich Layout shell around every stage" requirement becomes actually enforced — `run_wizard` must attach a `LayoutRenderer` and re-render between stages without holding an open `rich.Live` across `questionary` prompts (Windows-safe pattern). Scenarios added for log-ring truncation during a real wizard run and for layout repaint on `KeyboardInterrupt` save.
- `builtin-cli`: The `init --template pptx-wizard` scaffold requirement gains a "scaffold reflects the real example" qualifier — the emitted `agent.json` declares at least two agents (intent analyst + slide generator), uses chain memory with markdown persistence, and the accompanying `README.md` points at `examples/pptx_generator/` for the full pipeline.

## Impact

- **Code**
  - `examples/pptx_generator/cli.py::run_wizard` — wires `LayoutRenderer` + `LogRing` + logger handler + secondary file-logging event subscriber. Net +60..90 lines (new small `_shell.py` helper module acceptable if `cli.py` passes 360 lines).
  - `examples/pptx_generator/wizard/_layout.py` — no change (already done in the prior archive).
  - `examples/pptx_generator/wizard/*.py` (per-stage modules) — each stage's `render(console, project)` optionally receives the layout-renderer / log-ring via a new attribute on the step instance; default None keeps back-compat with existing unit tests.
  - `examples/pptx_generator/persistence.py` — no change.
  - `openagents/cli/commands/init.py::_AGENT_PPTX` — rewritten as a multi-agent scaffold (~30..50 lines).
  - `openagents/cli/commands/init.py::_README_TEMPLATE` — `pptx-wizard`-specific addition pointing at `examples/pptx_generator/`.
  - No touch to `openagents/interfaces/`, `openagents/plugins/builtin/`, or `Runtime` — this is an example + scaffold change.
- **Dependencies** — none added. `rich`, `questionary`, `python-dotenv` already in the `pptx` / `cli` extras.
- **APIs** — no change to kernel protocols; `RunRequest` / `RunResult` / `EventBus` consumed as-is.
- **Docs** — `examples/pptx_generator/README.md` (+ screenshot-equivalent ASCII for Layout), `docs/pptx-agent-cli.md` (+ `.en.md`), `docs/cli.md` cross-link, `docs/examples.md` cross-link.
- **Tests** — new unit tests: `tests/unit/test_pptx_wizard_shell.py` (layout wiring in `run_wizard`), `tests/unit/test_pptx_events_jsonl.py` (events.jsonl roundtrip), amended `tests/unit/cli/commands/test_init.py` (scaffold multi-agent shape). Amended `tests/integration/test_pptx_generator_example.py` for `events.jsonl` assertion.
- **Coverage floor** — `source = ["openagents"]` still excludes `examples/`, so the example-side additions don't directly count; the `init.py` diff is small and covered by existing `test_init.py` expansions; overall floor remains ≥ 90 %.
- **Runtime / kernel** — zero change.
- **Migration** — zero. Existing projects created by prior `openagents init --template pptx-wizard` runs continue to work unchanged (they will not auto-upgrade to the new scaffold; this is intentional). The `outputs/<slug>/events.jsonl` is written on every new run; absence on resume is handled gracefully (file is created on first write).
