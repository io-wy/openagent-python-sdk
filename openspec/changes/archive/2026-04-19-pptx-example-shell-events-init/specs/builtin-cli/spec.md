## ADDED Requirements

### Requirement: `init --template pptx-wizard` scaffold reflects the real pptx example

The `openagents init --template pptx-wizard` scaffold SHALL emit an `agent.json` that demonstrates the two-agent / chain-memory / truncating-assembler pattern used by the real `examples/pptx_generator/` example, rather than a single-agent placeholder. The scaffold SHALL be self-contained (no `impl:` references to modules that do not exist in the scaffolded project), runnable against the `mock` provider without additional setup, and the accompanying `README.md` SHALL point explicitly at `examples/pptx_generator/` as the full-pipeline reference.

The emitted `agent.json` SHALL satisfy all of:

- declares at least two agents whose `id` values convey pipeline roles (e.g. `intent-analyst` and `slide-generator`)
- uses `chain` memory with at least one `window_buffer` layer and one `markdown_memory` layer per agent
- uses `truncating` `context_assembler` with a bounded `max_messages`
- references only builtin plugin `type` values — no `impl:` paths to example-private code
- parses as a valid `AppConfig` via `openagents.config.loader.load_config`
- runs `openagents run ./agent.json --input "hello" --agent intent-analyst` successfully under the `mock` provider out of the scaffold with zero code edits

#### Scenario: Scaffold emits a multi-agent config

- **WHEN** the user runs `openagents init myproj --template pptx-wizard --provider mock --yes`
- **THEN** `myproj/agent.json` SHALL be a valid `AppConfig` containing at least 2 entries in `agents`, each with `memory.type == "chain"` and a `markdown_memory` sub-memory, and the file SHALL NOT reference any `impl:` path outside the `openagents.*` namespace

#### Scenario: Scaffold README directs users to the real example

- **WHEN** the scaffold is emitted
- **THEN** `myproj/README.md` SHALL contain a section that names `examples/pptx_generator/` as the source of the complete 7-stage wizard (env doctor, research, outline, theme, slides, compile-QA)

#### Scenario: Scaffold runs against mock provider

- **WHEN** the scaffolded project is exercised via `openagents run ./agent.json --input "hello" --agent intent-analyst` with `LLM_API_KEY=anything` in the environment and provider set to `mock`
- **THEN** the command SHALL exit 0 and emit at least one `run.started` / `run.finished` event pair

#### Scenario: Existing scaffolds remain valid on upgrade

- **WHEN** a user runs `openagents init` with the new scaffold after previously running the old single-agent scaffold into a separate directory
- **THEN** the prior directory's `agent.json` SHALL still load successfully (no AppConfig-level deprecation is introduced by this change)
