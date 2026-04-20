## MODIFIED Requirements

### Requirement: Rich Layout shell around every stage

The `pptx-agent` wizard SHALL render every stage inside a four-region Rich `Layout`: a top status bar (slug · stage n/7 · elapsed time), a left sidebar showing all 7 steps annotated with ✓ (done) / ▶ (active) / ○ (pending), a main region that hosts the current stage panel, and a bottom log tail capped at the last 5 log lines. Between stages the console SHALL be cleared and the `Layout` SHALL be redrawn from current `DeckProject` state so the sidebar, status bar, and log tail stay consistent with `project.stage`. The wizard SHALL NOT hold an open `rich.Live` context across any interactive prompt issued via `openagents.cli.wizard.Wizard.select/confirm/text/password` — the shell MUST coexist with `questionary.ask_async()` on Windows without terminal corruption. Stages MAY call `console.print(layout_renderer.render(project))` at any logical sub-step boundary (e.g. after each Tavily query completes) to give the user time-updated feedback without a background ticker thread.

#### Scenario: Layout redraws on stage transition

- **WHEN** a stage completes and advances `project.stage` to the next stage
- **THEN** the console is cleared, the sidebar marks the prior step ✓ and the new step ▶, the status bar updates to `stage n/7`, and the main region shows the new stage's panel

#### Scenario: Log tail shows only the latest 5 entries

- **WHEN** more than 5 log lines have been emitted in the current stage
- **THEN** only the most recent 5 SHALL appear in the bottom region and older lines SHALL be scrolled out of view without truncating the retained `project.json` / on-disk logs

#### Scenario: Elapsed time updates on stage boundary or log append

- **WHEN** a stage transitions or a new log line is pushed into the `LogRing`
- **THEN** the status bar SHALL redraw with the current elapsed time (computed from the wizard start monotonic timestamp) on the very next `console.print(layout_renderer.render(project))` call

#### Scenario: Layout and questionary coexist on Windows

- **WHEN** a stage uses `Wizard.select` or `Wizard.text` to prompt the user on a Windows conhost
- **THEN** the `questionary` prompt SHALL render without corrupted cursor positioning or interleaved Rich redraws, because no `rich.Live` context is active across the prompt call

#### Scenario: `KeyboardInterrupt` repaints the Layout before exit

- **WHEN** the user presses Ctrl+C at a stage boundary or during a prompt
- **THEN** `run_wizard` SHALL catch the signal, repaint the Layout once to reflect the final persisted stage (current stage marked ▶, not ✓), print the resume hint, and exit with code 130
