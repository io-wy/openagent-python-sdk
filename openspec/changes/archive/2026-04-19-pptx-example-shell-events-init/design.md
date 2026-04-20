## Context

The `examples/pptx_generator/` example already owns three artifacts that *almost* deliver what this change needs:

1. `wizard/_layout.py` implements `LayoutRenderer` (four-region Rich `Layout`) and `LogRing(max_lines=5)`, with a unit test (`tests/unit/pptx_generator/test_wizard_layout.py`) that covers sidebar glyph selection, log-ring truncation, and status-bar formatting. It is never instantiated from `run_wizard`.
2. `agent.json` wires `PrettyEventBus` (delegates to the SDK's `openagents.cli._events.EventFormatter` since the 04-19 archive) for stderr rendering but never persists events to disk.
3. `openagents/cli/commands/init.py::_AGENT_PPTX` emits a single-agent `react` stub that bears no resemblance to the real pipeline.

Meanwhile, the SDK already ships every primitive needed to close these gaps:

- `openagents.plugins.builtin.events.file_logging.FileLoggingEventBus` wraps any inner event bus, forwards every emit, and appends an NDJSON record to `log_path`. It supports an inner-bus reference via the same loader used by `agent.json`, so it nests cleanly over `PrettyEventBus`.
- `openagents run` and `openagents replay` (active `enhance-builtin-cli` change) both read the `EventFormatter` schema and re-render JSONL streams.
- `openagents/cli/wizard.py::Wizard.select/confirm/text` already call `questionary.ask_async()`, so the Layout wiring only has to be compatible with *this* call shape, not arbitrary prompt libraries.

The prior archive (`archive/2026-04-19-pptx-example-full-interactions/`) deferred the Layout wiring (task 3.1) with the comment *"persistent-Live/questionary interplay is risky on Windows"*. That risk is real: `rich.Live` owns the terminal output stream, and `questionary.ask_async()` writes its own prompt characters to the same stream; on Windows conhost the two collide and produce garbled UI or a hung prompt. The design below sidesteps the interaction entirely.

## Goals / Non-Goals

**Goals:**
- Wire `LayoutRenderer` into `run_wizard` such that every stage transition redraws the four-region layout, without introducing terminal races on Windows.
- Persist every event emitted by `PrettyEventBus` to `outputs/<slug>/events.jsonl` in the same schema as `openagents run --format events`, so `openagents replay outputs/<slug>/events.jsonl` replays the run verbatim.
- Rewrite `openagents init --template pptx-wizard` so the scaffolded project is a faithful (but minimal) slice of the real example: two agents (intent analyst + slide generator), chain memory, mock-provider-friendly.
- Zero kernel changes. Zero new seams. Zero new `PatternPlugin` subclasses.
- Preserve the existing `pptx-agent` CLI contract (`new / resume / memory list / memory forget`) unchanged.

**Non-Goals:**
- Reworking `compile_qa.py` into Rich `Live` sub-steps (archive task 2.5 — remains deferred; covered by a future *Step B*).
- Hoisting the five custom `PatternPlugin`s onto `repair_empty_response` (the "Step B" refactor the user explicitly deferred).
- Adding `FileLoggingEventBus` as a default for `examples/quickstart/` or `examples/production_coding_agent/` — those examples have their own lifecycle.
- Adding `SQLiteBackedSession` — `project.json + .bak` already satisfies resume semantics.
- Smoke-testing the integration path against a real LLM endpoint (archive task 7.3 — out of scope).

## Decisions

### D1. Layout shell: manual repaint between stages, never a long-lived `rich.Live`

**Chosen pattern:**

```python
# examples/pptx_generator/cli.py (sketch)
layout_renderer = LayoutRenderer(project=project, log=LogRing(max_lines=5))

# Attach a small logger handler that appends to the LogRing.
log_handler = RingLogHandler(layout_renderer.log)
logging.getLogger("examples.pptx_generator").addHandler(log_handler)

for step in steps:
    step.layout = layout_renderer        # NEW optional attribute; default None
    step.log_ring = layout_renderer.log  # NEW optional attribute; default None
console.clear()
console.print(layout_renderer.render(project))  # one shot, no Live context

# Wizard.run() iterates; each step's render() can call
# console.print(layout_renderer.render(project)) at its own boundary
# (e.g. before invoking Wizard.select / Wizard.text).
# No stage opens a Live context; each stage only calls console.print.
```

**Rationale:**
- `rich.Live` holds the terminal stream open and rewrites cursor position every tick. When `questionary` is asked to render a prompt inside a `Live`, Windows conhost interleaves the two writers and produces a corrupted display — documented in `rich`'s own issue tracker and the exact symptom that caused archive task 3.1 to be deferred.
- Manual repaint (plain `console.print(renderable)` once per stage transition) is idempotent, has no background thread, and coexists with `questionary` the same way the current `cli.py` does.
- The existing spec scenario *"Elapsed time ticks while a stage is running"* (pptx-wizard-ui §Requirement "Rich Layout shell around every stage" / third scenario) is revised — see the pptx-wizard-ui delta spec — from "at least once per second" to "on every stage boundary and log-tail append". The user-perceived loss is a continuously-ticking clock; the gain is a UI that never garbles on Windows.

**Alternatives considered:**
- *(rejected)* `rich.Live` with a `transient=True` context that starts/stops around each stage — still leaks into `questionary.ask_async()` when a stage yields inside the context.
- *(rejected)* `rich.Live` + asyncio.Event that stops Live before each prompt — doubles the state machine and makes stages responsible for Live lifecycle, cross-cutting concern leak.
- *(rejected)* `asciimatics` or `textual` for real async TUI — major new dependency, out of scope for a polish pass.

### D2. Events JSONL persistence: wrap `PrettyEventBus` in `FileLoggingEventBus` via env-var slug injection

**Chosen pattern:**

`agent.json` becomes:

```json
"events": {
  "type": "file_logging",
  "config": {
    "log_path": "${PPTX_EVENTS_LOG}",
    "inner": {
      "impl": "examples.pptx_generator.app.events.PrettyEventBus",
      "config": { "inner": { "type": "async" }, "stream": "stderr", "show_details": true }
    },
    "redact_keys": ["api_key", "authorization", "token", "secret", "password"]
  }
}
```

`cli.py::run_wizard` sets `os.environ["PPTX_EVENTS_LOG"]` to `str(outputs / project.slug / "events.jsonl")` *before* the `Runtime.from_config(...)` call, then unsets it in a `try/finally` (only when it was not already set by the caller — respect upstream override).

**Rationale:**
- `FileLoggingEventBus` already forwards to its inner bus before appending, so `PrettyEventBus` stderr rendering is untouched.
- `${PPTX_EVENTS_LOG}` rides the config-layer env-substitution that `openagents/config/loader.py` already performs, so no new plumbing.
- The JSONL schema is exactly what `openagents replay` consumes (`{"name", "payload", "ts"}` per line), so round-trip is automatic.
- Redaction keys reuse the set declared in the existing `logging.redact_keys` field, so secrets in tool payloads (API keys in URLs, etc.) are not written to disk.

**Alternatives considered:**
- *(rejected)* Programmatically wrap `runtime._events` after `Runtime.from_config()` — breaks the invariant that top-level `runtime/session/events` are owned by config. Leaks kernel internals into the example.
- *(rejected)* Write a custom example-side event subscriber that tails every event — duplicates `FileLoggingEventBus` (violates *feedback_avoid_duplicate_implementation*).
- *(rejected)* Use `jsonl_file` Session plugin for the same purpose — `Session` captures transcript turns (RunRequest / RunResult envelopes), not the fine-grained `tool.*` / `run.*` events that `openagents replay` wants. Wrong seam.

### D3. Init template alignment: emit a two-agent scaffold with `chain` memory

**Chosen pattern:**

`_AGENT_PPTX` is rewritten to:

```json
{
  "version": "1.0",
  "events": { "type": "async" },
  "agents": [
    {
      "id": "intent-analyst",
      "name": "Intent Analyst",
      "memory": {
        "type": "chain",
        "on_error": "continue",
        "config": {
          "memories": [
            { "type": "window_buffer", "config": { "window_size": 12 } },
            { "type": "markdown_memory", "config": { "memory_dir": "./memory" } }
          ]
        }
      },
      "pattern": { "type": "react", "config": { "max_steps": 3 } },
      "context_assembler": { "type": "truncating", "config": { "max_messages": 8 } },
      "llm": { "provider": "{{ provider }}", "model": "PLACEHOLDER_MODEL_NAME", "api_key_env": "{{ api_key_env }}", "temperature": 0.3 },
      "tools": []
    },
    {
      "id": "slide-generator",
      "name": "Slide Generator",
      "memory": { "type": "chain", "on_error": "continue", "config": { "memories": [
        { "type": "window_buffer", "config": { "window_size": 12 } },
        { "type": "markdown_memory", "config": { "memory_dir": "./memory" } }
      ]}},
      "pattern": { "type": "react", "config": { "max_steps": 2 } },
      "context_assembler": { "type": "truncating", "config": { "max_messages": 6 } },
      "llm": { "provider": "{{ provider }}", "model": "PLACEHOLDER_MODEL_NAME", "api_key_env": "{{ api_key_env }}", "temperature": 0.3 },
      "tools": []
    }
  ]
}
```

Accompanying `README.md` gains a "See the full pipeline" pointer:

```
This scaffold is a minimal two-agent slice that mirrors the intent/slide-generator pair
in the real `examples/pptx_generator/` example. For the complete 7-stage wizard
(env doctor, research, outline, theme, slides, compile-QA), clone the SDK repo and
see examples/pptx_generator/README.md.
```

**Rationale:**
- Uses only **builtin** seam types (`react`, `chain`, `window_buffer`, `markdown_memory`, `truncating`), so the scaffold runs against the `mock` provider out of the box — no custom-Python imports required in a freshly-scaffolded project.
- Demonstrates the multi-agent pattern (two agents in `agents: [...]`) so new users immediately see that `openagents run --agent <id>` applies.
- Documents `markdown_memory` as the cross-session-memory primitive, matching what the real example uses.
- Pointer to `examples/pptx_generator/` in the README avoids overgrowing the inline scaffold.

**Alternatives considered:**
- *(rejected)* Copy the full 5-agent `agent.json` verbatim from the real example — pulls in `impl:` paths to `examples.pptx_generator.app.plugins.*` which do not exist in a freshly-scaffolded project; the scaffold would fail to load.
- *(rejected)* Leave the stub as-is, document the mismatch in README — degrades discoverability of the real example; contradicts "scaffold reflects what it advertises".

## Risks / Trade-offs

- **Risk:** Losing the sub-second elapsed-time tick will feel less polished than `rich.Live` when stages run for minutes.
  - **Mitigation:** Each stage that does background work SHOULD call `self.layout.render()` + `console.print()` on logical sub-step boundaries (e.g. `ResearchWizardStep` after each query completes). This gives the user time-updated feedback without a ticker thread. Documented in the updated `pptx-wizard-ui` spec.

- **Risk:** `FileLoggingEventBus` opens `events.jsonl` for append on every `emit`, which is slower than a held file handle.
  - **Mitigation:** Already swallowed by the builtin's `OSError` handler; for the expected 7-stage run volume (~200-500 events) the overhead is immaterial. Not a hot path.

- **Risk:** Env-var `PPTX_EVENTS_LOG` collides with an external caller that already sets it.
  - **Mitigation:** `run_wizard` saves any preexisting value via `os.environ.get("PPTX_EVENTS_LOG")` and restores it in `finally`, so the outer caller's value survives. An explicit override is honored when present.

- **Risk:** The scaffold template references `markdown_memory` with `memory_dir: ./memory`, but the directory does not exist yet when `openagents init` exits.
  - **Mitigation:** `markdown_memory` creates its target directory on first write (`memory_dir.mkdir(parents=True, exist_ok=True)`); verified by reading its `__init__`. The scaffolded project's `./memory/` appears on first `openagents run` invocation.

- **Trade-off:** `pptx-wizard-ui` spec loses the "ticks once per second" scenario.
  - **Accepted:** The Windows-safety win (task 3.1 finally closes) outweighs the lost ticker. The replacement scenario *"elapsed time updates on each stage boundary and log-tail append"* is strictly weaker but captures the user-facing intent.

- **Trade-off:** The init scaffold uses `react` pattern for both agents, not the custom `IntentAnalystPattern` / `SlideGenPattern` the real example uses.
  - **Accepted:** A faithful scaffold would pull in code that does not exist in the scaffolded project. The README cross-link is the right place to direct users who want the full example.

## Migration Plan

- No migration needed for existing `pptx-agent` projects: resume path will simply start writing `events.jsonl` from the next event onward; absence of the file before the current run is expected.
- No migration for projects scaffolded by the pre-change `openagents init --template pptx-wizard`: those projects remain valid `AppConfig`s; they simply won't gain the new multi-agent shape. Re-scaffolding is opt-in.
- Rollback: revert the three diffs (cli.py, agent.json, init.py) — zero state on disk depends on this change.
