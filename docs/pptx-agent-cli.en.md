# pptx-agent CLI Guide

## Install

```bash
uv add "io-openagent-sdk[pptx]"
```

System-level dependencies required: Python ≥3.10, Node.js ≥18, npm, `markitdown` (Python). On first run the CLI detects and guides you to install missing items.

## Commands

- `pptx-agent new [--topic "..."] [--slug ...]` — start a new deck
- `pptx-agent resume <slug>` — resume an interrupted deck
- `pptx-agent memory [--section user_feedback]` — list saved preferences

## 7-stage pipeline

1. **Intent Analysis** — turns your natural-language description into a structured IntentReport
2. **Environment Check** — checks Python / Node / npm / markitdown / API keys; interactively fixes missing ones
3. **Research** — searches the web via Tavily MCP (with REST fallback)
4. **Outline** — generates slide-by-slide outline; accept / regenerate / abort
5. **Theme** — picks palette / fonts / style from the built-in catalog
6. **Slide Generation** — each slide is its own parallel agent run; schema-validated JSON with freeform fallback
7. **Compile + QA** — renders PptxGenJS source, runs `node compile.js`, verifies via `markitdown`

## Resume

All project state is persisted to `outputs/<slug>/project.json` (atomic write with backup). Any Ctrl+C lets you resume from the same stage with `pptx-agent resume <slug>`.

## Keys & `.env`

- Required: `LLM_API_KEY`, `LLM_API_BASE`, `LLM_MODEL`
- Optional: `TAVILY_API_KEY` (enables web research)
- User-level `.env`: `~/.config/pptx-agent/.env` (shared across projects)
- Project-level `.env`: `outputs/<slug>/.env` (overrides user-level)
