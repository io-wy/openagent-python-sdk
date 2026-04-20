# Built-in CLI

The `openagents` binary ships with the SDK. `pip install io-openagent-sdk`
is enough for basic use; for the richer experience (colour output,
interactive prompts, hot reload) install the `cli` extra:

```bash
pip install 'io-openagent-sdk[cli]'     # or uv sync --extra cli
```

The `cli` extra pulls in `rich`, `questionary`, `watchdog`, and
`PyYAML`. Any missing optional dependency degrades gracefully — no
subcommand raises `ImportError` at the user.

## Subcommands at a glance

| Command | Purpose |
| --- | --- |
| `openagents schema` | Dump `AppConfig` / plugin JSON Schema |
| `openagents validate <path>` | Validate an `agent.json` without running it |
| `openagents list-plugins` | Enumerate registered plugins per seam |
| `openagents version` | Print SDK / Python / extras / plugin counts |
| `openagents doctor` | Environment health check |
| `openagents config show <path>` | Print the fully-resolved `AppConfig` |
| `openagents init <name>` | Scaffold a new project from a template |
| `openagents new plugin <seam> <name>` | Scaffold a plugin skeleton + test stub |
| `openagents run <path>` | Execute one single-shot turn |
| `openagents chat <path>` | Interactive multi-turn REPL |
| `openagents dev <path>` | Reload runtime on config change |
| `openagents replay <path>` | Re-render a persisted transcript |
| `openagents completion <shell>` | Emit a shell-completion script |

`openagents --version` / `-V` is equivalent to `openagents version`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Usage error (missing args, file not found, multi-agent without `--agent`, unknown slash) |
| `2` | Validation error (`load_config` failed, bad JSON/YAML, strict-mode unresolved plugin) |
| `3` | Runtime error (LLM raised, plugin raised, `run.error` non-empty) |

## Common workflows

### Single-shot run

```bash
openagents run agent.json --input "hello"

# Non-TTY stdout defaults to JSONL so `jq` works out of the box:
openagents run agent.json --input "hi" | jq -c .

# Read prompt from a file:
openagents run agent.json --input-file ./prompt.txt

# Or from stdin:
echo "hello" | openagents run agent.json

# Final-output-only (scripts / CI):
openagents run agent.json --input "hi" --format text --no-stream
```

Multi-agent configs require `--agent <id>`:

```bash
openagents run multi.json --agent coder --input "implement X"
```

### Interactive chat

```bash
openagents chat agent.json
```

Built-in slash commands:

| Command | Behaviour |
| --- | --- |
| `/exit`, `/quit` | Clean exit (code `0`) |
| `/reset` | Rotate the `session_id` and drop context |
| `/save <path>` | Dump the last turn to JSON; re-readable by `openagents replay` |
| `/context` | Print the previous turn's `final_output` and `stop_reason` |
| `/tools` | List the agent's tool IDs and types |

### Hot reload

```bash
openagents dev agent.json
```

Internally calls `Runtime.reload()`. **Note:** per the kernel contract,
`dev` does **not** hot-swap top-level `runtime` / `session` / `events`
plugins — restart the process for those.

Uses `watchdog` when available; falls back to `--poll-interval` polling
otherwise.

### Replay a transcript

```bash
openagents replay ./transcript.jsonl
openagents replay ./session.json --turn 2
openagents replay ./transcript.jsonl --format json > normalized.json
```

Accepted inputs: JSONL events (from `openagents run --format events`),
a top-level JSON array of `{name, payload}` objects, a
`{"events": [...]}` envelope (from `/save`), or a session transcript
(from the `jsonl_file` session backend).

### Scaffolding

```bash
openagents init my-agent --template minimal --provider mock --yes
openagents new plugin tool calculator
```

Templates: `minimal` (default), `coding-agent`, `pptx-wizard`.

> The `pptx-wizard` scaffold is a two-agent slice (intent-analyst + slide-generator, `chain` memory + markdown persistence) of `examples/pptx_generator/` — it runs against the mock provider out of the box. For the complete 7-stage wizard (environment doctor, Tavily research, outline, theme gallery, parallel slide generation, compile-QA), clone the SDK repo and see `examples/pptx_generator/README.md`.

### Environment diagnostics

```bash
openagents doctor
openagents doctor --config agent.json --format json
```

`doctor` **never** prints API key values — only whether they're set.

### Resolved config

```bash
openagents config show agent.json
openagents config show agent.json --redact   # api_key/token/password/secret → ***
openagents config show agent.json --format yaml
```

`impl` fields are resolved to concrete Python dotted paths.

### Shell completion

```bash
openagents completion bash  > /etc/bash_completion.d/openagents
openagents completion zsh   > ~/.zsh/completions/_openagents
openagents completion fish  > ~/.config/fish/completions/openagents.fish
openagents completion powershell >> $PROFILE
```

Scripts are generated from the live argparse tree, so newly-registered
subcommands appear automatically.

## JSONL event-stream stability

`openagents run --format events` prints one line per event:

```json
{"schema": 1, "name": "tool.called", "payload": {...}}
```

* `schema` — the `EVENT_SCHEMA_VERSION` (`1` today).
* Breaking wire-shape changes bump `schema`; additive field changes do
  **not** bump. Downstream parsers should ignore unknown fields.
* A terminal `{"name": "run.finished", ...}` line is always emitted,
  carrying `run_id`, `stop_reason`, `final_output`, and `error`.

## Adding a new subcommand

`openagents/cli/main.py` is just a registry dispatcher:

```python
# openagents/cli/commands/__init__.py
COMMANDS: list[str] = [
    "schema", "validate", "list-plugins", ...
]
```

To add a new subcommand: drop a module under
`openagents/cli/commands/` exporting `add_parser(subparsers)` and
`run(args) -> int`, then append the display name to `COMMANDS`. No
other edit is needed in `main.py`.
