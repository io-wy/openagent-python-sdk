# pptx-agent

Interactive CLI that drives a 7-stage PPT generation pipeline on the openagents SDK.

```bash
uv add "io-openagent-sdk[pptx]"
pptx-agent new --topic "your deck topic"
```

## What it does

1. **Intent** — LLM turns your free-form description into a structured `IntentReport`; you confirm or edit field-by-field.
2. **Environment** — checks Python / Node / npm / markitdown / API keys; missing pieces get an interactive fix.
3. **Research** — calls Tavily (MCP first, REST fallback) and lets you multi-select kept sources.
4. **Outline** — generates a slide-by-slide outline with `accept / add slide / remove slide / reorder / edit slide / regenerate all / abort` actions.
5. **Theme** — agent returns 3–5 theme candidates side-by-side; pick one or open the full custom editor.
6. **Slide generation** — each slide runs as its own agent call in parallel, with slot-schema validation, up to 2 retries, and freeform fallback.
7. **Compile + QA** — writes the PptxGenJS JS files, runs `node compile.js`, and reads the PPTX back via `markitdown`.

## Commands

```bash
pptx-agent new --topic "..."             # start a new deck
pptx-agent resume <slug>                 # resume an interrupted deck
pptx-agent memory list [--section ...]   # list stored preferences
pptx-agent memory forget <entry_id>      # delete one preference
```

## Replay a finished run

Every `pptx-agent new` / `resume` run appends its full event stream to `outputs/<slug>/events.jsonl` via the builtin `FileLoggingEventBus`. The file is append-only NDJSON with one record per line in the `{"name", "payload", "ts"}` shape that `openagents replay` consumes directly:

```bash
openagents replay outputs/<slug>/events.jsonl
```

Payload keys matching `api_key` / `authorization` / `token` / `secret` / `password` are redacted on write, so sharing the JSONL with collaborators is safe. To redirect the log to a custom path for a single run, set `PPTX_EVENTS_LOG` before invoking `pptx-agent`.

## Resume safety

Project state is persisted to `outputs/<slug>/project.json` with atomic writes and a rolling `project.json.bak` backup. Ctrl+C at any stage flushes state (exit code 130) and `pptx-agent resume <slug>` picks up from there. If `project.json` is corrupt, the CLI offers to restore from backup, start fresh, or abort.

## Memory

Cross-session preferences live in `~/.config/pptx-agent/memory/` as human-readable markdown (`user_goals.md`, `user_feedback.md`, `decisions.md`, `references.md`, with a `MEMORY.md` index). Stages 1 / 3 / 5 / 6 each offer an optional "save as preference" prompt; future runs inject the stored preferences back into the agent context.

## Docs

- CLI guide: [`docs/pptx-agent-cli.md`](../../docs/pptx-agent-cli.md) · [EN](../../docs/pptx-agent-cli.en.md)
- Original design: [`docs/superpowers/specs/2026-04-18-pptx-agent-design.md`](../../docs/superpowers/specs/2026-04-18-pptx-agent-design.md)
- OpenSpec change: [`openspec/changes/pptx-example-full-interactions/`](../../openspec/changes/pptx-example-full-interactions/)

## Environment variables

| Name | Required | Notes |
|------|----------|-------|
| `LLM_API_KEY` | yes | OpenAI-compatible key (e.g. MiniMax, Anthropic-compatible, OpenAI). |
| `LLM_API_BASE` | yes | Base URL of the provider. |
| `LLM_MODEL` | yes | Model name. |
| `TAVILY_API_KEY` | no | Enables the research stage. |
| `PPTX_AGENT_OUTPUTS` | no | Override the project output directory (default: `examples/pptx_generator/outputs`). |
| `PPTX_EVENTS_LOG` | no | Override the per-project event log path (default: `outputs/<slug>/events.jsonl`). |
