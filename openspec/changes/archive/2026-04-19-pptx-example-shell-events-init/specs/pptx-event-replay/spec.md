## ADDED Requirements

### Requirement: Per-project event JSONL log

Every `pptx-agent new` or `pptx-agent resume <slug>` invocation SHALL persist every runtime event emitted during the wizard run to `outputs/<slug>/events.jsonl` using the builtin `FileLoggingEventBus`. The log SHALL be append-only, one JSON record per line, and SHALL use the same `{"name": <event-name>, "payload": <event-payload>, "ts": <iso8601>}` schema that `openagents replay` consumes. Secret-bearing payload keys (`api_key`, `authorization`, `token`, `secret`, `password`) SHALL be redacted per the existing `FileLoggingEventBus.redact_keys` mechanism.

#### Scenario: `events.jsonl` is written during a fresh run

- **WHEN** a `pptx-agent new --topic ...` run reaches any stage that emits at least one event
- **THEN** `outputs/<slug>/events.jsonl` SHALL exist on disk, each line SHALL parse as JSON with `name`/`payload`/`ts` keys, and no line SHALL contain a raw API-key value in any payload leaf

#### Scenario: Resume appends to the existing log

- **WHEN** a previously-interrupted project is resumed via `pptx-agent resume <slug>`
- **THEN** the wizard SHALL continue appending to the existing `events.jsonl` rather than truncating; prior events SHALL remain intact

#### Scenario: PrettyEventBus rendering is unchanged

- **WHEN** events are persisted to `events.jsonl`
- **THEN** the stderr output rendered by `PrettyEventBus` SHALL remain byte-for-byte identical to its pre-change behavior for the same event stream; file persistence SHALL NOT alter the inner bus's rendering contract

### Requirement: `openagents replay` round-trips `events.jsonl`

The file produced at `outputs/<slug>/events.jsonl` SHALL be directly consumable by `openagents replay` without any transformation step. Users SHALL be able to pipe a completed run back through the builtin replay command to re-render the event sequence on the terminal.

#### Scenario: Replay renders a complete run

- **WHEN** `openagents replay outputs/<slug>/events.jsonl` is invoked after a successful `pptx-agent new` completion
- **THEN** the replay command SHALL exit with code 0 and SHALL render at least the `run.started` / `run.finished` / `tool.*` events present in the log

#### Scenario: Malformed lines are skipped without crashing

- **WHEN** a concurrent writer has left a partial-line record at the tail of `events.jsonl`
- **THEN** `openagents replay` SHALL skip the malformed line, render the remaining valid events, and exit 0 with a non-fatal warning to stderr

### Requirement: Event log path override via environment

The `events.jsonl` target path SHALL be injectable via the `PPTX_EVENTS_LOG` environment variable so tests and advanced users can redirect the log without editing `agent.json`. When the variable is absent, `run_wizard` SHALL set it to `outputs/<slug>/events.jsonl` for the duration of the run and restore the prior value (or absence) on exit.

#### Scenario: Test injection

- **WHEN** a test sets `PPTX_EVENTS_LOG=/tmp/custom.jsonl` before invoking `run_wizard`
- **THEN** the events SHALL be written to `/tmp/custom.jsonl` and the default path under `outputs/<slug>/` SHALL NOT be created

#### Scenario: Ambient variable is restored on completion

- **WHEN** `PPTX_EVENTS_LOG` was already set in the caller's environment before `run_wizard` ran
- **THEN** on normal completion or interrupt, the variable SHALL retain its original value (not be cleared by the wizard)
