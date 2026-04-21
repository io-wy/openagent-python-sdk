# OpenAgents SDK

> Config-as-code, async-first, pluggable SDK for building protocol-rich single-agent systems.

[![PyPI](https://img.shields.io/pypi/v/io-openagent-sdk)](https://pypi.org/project/io-openagent-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/io-openagent-sdk)](https://pypi.org/project/io-openagent-sdk/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A592%25-brightgreen)](#development)

**English** | [中文](README_CN.md)

---

## Overview

OpenAgents gives you a small, explicit runtime kernel for single-agent execution. It is designed for developers who want real control over agent behavior — not a black-box framework that hides everything inside one monolithic `execute()` call.

**Designed for:**

- Teams that want a clear, auditable agent runtime instead of a magic framework
- Developers building coding agents, research agents, and workflow agents
- Products that need custom middle protocols, safety rules, and context logic
- Applications that want a stable kernel now and product infrastructure on top

**Deliberately not:**

- A multi-agent control plane (one `run` = one `agent_id`)
- A job scheduler or queue system
- A durable product control plane
- A UI framework

Team orchestration, mailboxes, schedulers, approvals, and product UX belong above this layer.

---

## Why OpenAgents

Most agent frameworks collapse three very different concerns into one abstraction:

1. **Kernel protocol** — what a run *is* (inputs, outputs, state)
2. **Runtime seams** — how a run *behaves* (memory, tools, context assembly)
3. **Product middle protocols** — what only *your application* understands (task envelopes, review contracts, permission models)

OpenAgents keeps them separate:

```
┌─────────────────────────────────────────────────┐
│           App / Product Protocols               │
│  task envelopes · coding plans · approvals      │
│  review contracts · artifact taxonomies         │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│              SDK Runtime Seams (8)              │
│  memory · pattern · tool · tool_executor        │
│  context_assembler · runtime · session          │
│  events · skills                                │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│             Kernel Protocols                    │
│  RunRequest · RunResult · RunContext            │
│  ToolExecutionRequest · SessionArtifact         │
└─────────────────────────────────────────────────┘
```

This separation gives you a small kernel with explicit behavior, stable extension seams instead of ad-hoc monkeypatching, and room to invent app-specific protocols without forking the SDK.

---

## Installation

**Core (zero optional deps):**

```bash
pip install io-openagent-sdk
# or
uv add io-openagent-sdk
```

**Optional extras:**

| Extra | Installs | Use when |
|---|---|---|
| `cli` | `rich`, `questionary`, `watchdog`, `pyyaml` | Interactive CLI, hot reload, colour output |
| `openai` | `openai`, `httpx` | OpenAI-compatible LLM providers |
| `mem0` | `mem0ai` | Persistent cross-session memory |
| `mcp` | `mcp` | MCP tool bridge |
| `otel` | `opentelemetry-api` | OpenTelemetry event bridge |
| `sqlite` | `aiosqlite` | SQLite-backed session persistence |
| `tokenizers` | `tiktoken` | Accurate token counting for OpenAI |
| `yaml` | `pyyaml` | YAML config files |
| `all` | Everything above | Development / full-featured deployments |

```bash
uv add "io-openagent-sdk[cli]"
uv add "io-openagent-sdk[openai,mcp]"
uv add "io-openagent-sdk[all]"
```

**Requires Python ≥ 3.10.**

---

## Quick Start

### 1. Define your agent in JSON

```json
{
  "version": "1.0",
  "agents": [
    {
      "id": "assistant",
      "name": "demo-agent",
      "memory": {"type": "window_buffer", "on_error": "continue"},
      "pattern": {"type": "react"},
      "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
      "tools": [
        {"id": "search", "type": "builtin_search"},
        {"id": "files", "type": "read_file"}
      ]
    }
  ]
}
```

### 2. Run via CLI

```bash
# Single-shot turn
openagents run agent.json --input "hello"

# Interactive multi-turn REPL
openagents chat agent.json

# Hot-reload on config change (dev mode)
openagents dev agent.json
```

### 3. Run via Python (async)

```python
import asyncio
from openagents import Runtime

async def main() -> None:
    runtime = Runtime.from_config("agent.json")
    result = await runtime.run(
        agent_id="assistant",
        session_id="demo",
        input_text="What files are in the current directory?",
    )
    print(result.output)

asyncio.run(main())
```

### 4. Run via Python (sync helpers)

```python
from openagents import run_agent, run_agent_detailed, run_agent_with_dict

# Simple sync wrapper
result = run_agent("agent.json", agent_id="assistant", session_id="s1", input_text="hello")

# Detailed result (usage, artifacts, stop reason)
result = run_agent_detailed("agent.json", agent_id="assistant", session_id="s1", input_text="hello")
print(result.usage.cost_usd, result.stop_reason)

# Inline config (no file needed)
result = run_agent_with_dict(
    {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "demo",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react"},
                "llm": {"provider": "mock"},
                "tools": []
            }
        ]
    },
    agent_id="assistant",
    session_id="demo",
    input_text="hello",
)
```

### 5. Streaming

```python
from openagents import Runtime, RunRequest

async def stream_example() -> None:
    runtime = Runtime.from_config("agent.json")
    request = RunRequest(agent_id="assistant", session_id="s1", input_text="hello")
    async for chunk in runtime.run_stream(request):
        print(chunk.kind, chunk.data)
```

---

## Core Concepts

### Runtime Flow

```
Caller
  → Runtime facade (Runtime.run / run_stream)
    → Runtime plugin (DefaultRuntime)
      → Session manager + Event bus
      → Context assembler (what enters this run?)
      → Pattern.setup() → Memory.inject()
      → Pattern.execute() ↔ Tool calls (policy + executor)
      → Memory.writeback() → Context assembler.finalize()
      → RunResult
```

### Key Objects

| Object | Role |
|---|---|
| `RunRequest` | Inputs to a single run (agent_id, session_id, input_text, context_hints, budget) |
| `RunResult[OutputT]` | Output of a run (output, usage, artifacts, stop_reason, error) |
| `RunContext[DepsT]` | Per-run state carrier available to tools and patterns |
| `RunUsage` | Token counts + `cost_usd` + cache stats |
| `RunBudget` | Limits: `max_cost_usd`, `max_tokens`, `max_turns`, `max_validation_retries` |
| `RunArtifact` | Named artifact emitted during a run (carries `metadata`) |
| `StopReason` | Typed termination state (`end_turn`, `budget_exhausted`, `error`, …) |

### Plugin Selectors

Every plugin is loaded by either:

```json
{"type": "react"}                              // builtin or decorator-registered name
{"impl": "myapp.patterns.custom.MyPattern"}   // Python dotted path
```

If both are set, `impl` wins. Top-level `runtime`, `session`, and `events` each take exactly one selector; agent-level plugins require at least one.

---

## Builtin Components

### Memory

| Name | Description |
|---|---|
| `buffer` | Full in-memory conversation history |
| `window_buffer` | Sliding window over recent turns |
| `markdown_memory` | File-backed long-term memory (MEMORY.md index) |
| `mem0` | Persistent semantic memory via mem0ai (`[mem0]` extra) |
| `chain` | Compose multiple memory plugins in sequence |

### Pattern (Reasoning Loop)

| Name | Description |
|---|---|
| `react` | ReAct: think → act → observe loop |
| `plan_execute` | Plan first, then execute steps |
| `reflexion` | Self-reflection and iterative refinement |

### Context Assembler

| Name | Description |
|---|---|
| `truncating` | Simple head truncation to fit token budget |
| `head_tail` | Keep head + tail, drop middle |
| `sliding_window` | Rolling window over recent messages |
| `importance_weighted` | Score-based retention |

### Builtin Tools

| Category | Tools |
|---|---|
| **Search** | `builtin_search`, `tavily_search` |
| **Files** | `read_file`, `write_file`, `list_files`, `delete_file` |
| **Text** | `grep_files`, `ripgrep`, `json_parse`, `text_transform` |
| **HTTP** | `http_request`, `url_parse`, `url_build`, `query_param`, `host_lookup` |
| **System** | `shell_exec`, `execute_command`, `get_env`, `set_env` |
| **Time** | `current_time`, `date_parse`, `date_diff` |
| **Random** | `random_int`, `random_choice`, `random_string`, `uuid` |
| **Math** | `calc`, `percentage`, `min_max` |
| **Memory** | `remember_preference` |
| **MCP** | `mcp` (bridge to any MCP server, `[mcp]` extra) |

### App Infrastructure

| Seam | Builtin | Description |
|---|---|---|
| `runtime` | `default` | DefaultRuntime orchestrator |
| `session` | `in_memory` | In-process session storage |
| `events` | `async` | Async event bus |

---

## CLI Reference

Install the `cli` extra for the full CLI experience:

```bash
uv add "io-openagent-sdk[cli]"
```

| Command | Description |
|---|---|
| `openagents run <path>` | Execute a single-shot turn |
| `openagents chat <path>` | Interactive multi-turn REPL |
| `openagents dev <path>` | Hot-reload runtime on config change |
| `openagents validate <path>` | Validate an agent.json without running |
| `openagents schema` | Dump the full AppConfig JSON Schema |
| `openagents list-plugins` | List all registered plugins by seam |
| `openagents config show <path>` | Print the fully-resolved AppConfig |
| `openagents init <name>` | Scaffold a new project from a template |
| `openagents new plugin <seam> <name>` | Scaffold a plugin skeleton + test stub |
| `openagents replay <path>` | Re-render a persisted transcript |
| `openagents doctor` | Environment health check |
| `openagents version` | Print SDK / Python / extras / plugin counts |
| `openagents completion <shell>` | Emit a shell-completion script |

**Exit codes:** `0` success · `1` usage error · `2` validation error · `3` runtime error

---

## Writing a Custom Plugin

Any seam can be extended by implementing the plugin interface and pointing to it via `impl`:

```python
# myapp/patterns/my_pattern.py
from openagents.interfaces import PatternPlugin, RunContext

class MyPattern(PatternPlugin):
    async def execute(self, context: RunContext) -> str:
        # your reasoning loop here
        return "done"
```

```json
{
  "pattern": {"impl": "myapp.patterns.my_pattern.MyPattern"}
}
```

Or use the decorator registry for named plugins:

```python
from openagents.decorators import pattern

@pattern("my_react")
class MyReact(PatternPlugin):
    ...
```

```json
{"pattern": {"type": "my_react"}}
```

See [Plugin Development](docs/plugin-development.md) for the full guide.

---

## App-Defined Middle Protocols

The SDK provides carriers for product-specific state — no need to fork the kernel:

| Carrier | Use for |
|---|---|
| `RunRequest.context_hints` | Pass structured hints into context assembly |
| `RunRequest.metadata` | Caller metadata (task IDs, trace IDs, …) |
| `RunContext.state` | Mutable per-run app state shared across tools/pattern |
| `RunContext.scratch` | Ephemeral scratchpad within a run |
| `RunContext.assembly_metadata` | Signals from context assembler to pattern |
| `RunArtifact.metadata` | Structured metadata on emitted artifacts |

---

## Examples

| Example | Description |
|---|---|
| [`examples/quickstart/`](examples/quickstart) | Minimal builtin-only setup — first contact with the kernel |
| [`examples/production_coding_agent/`](examples/production_coding_agent) | Production coding agent: task packets, persistent memory, follow-up recovery, delivery artifacts |
| [`examples/pptx_generator/`](examples/pptx_generator) | Interactive 7-stage PPT generator CLI (`pptx-agent`) with MCP + multi-pattern pipeline |

Full example guide: [docs/examples.md](docs/examples.md)

---

## Documentation

Developer docs live in [`docs/`](docs/README.md).

| Document | Description |
|---|---|
| [Developer Guide](docs/developer-guide.md) | Architecture boundaries, runtime lifecycle, state carriers |
| [Repository Layout](docs/repository-layout.md) | Directory structure, doc topology, test conventions |
| [Seams & Extension Points](docs/seams-and-extension-points.md) | Decision tree: which seam for which problem |
| [Configuration Reference](docs/configuration.md) | JSON schema, selector rules, builtin names |
| [Plugin Development](docs/plugin-development.md) | Loader mechanics, plugin contracts, testing patterns |
| [API Reference](docs/api-reference.md) | Package exports, runtime methods, protocol objects |
| [CLI Reference](docs/cli.en.md) | Full CLI surface and exit codes |
| [Examples Guide](docs/examples.md) | What each example demonstrates |
| [Migration 0.2 → 0.3](docs/migration-0.2-to-0.3.md) | Upgrade guide |

**Recommended reading order for new users:**
1. [Repository Layout](docs/repository-layout.md)
2. [Developer Guide](docs/developer-guide.md)
3. [Seams & Extension Points](docs/seams-and-extension-points.md)
4. [Configuration Reference](docs/configuration.md)

---

## Development

This project uses [`uv`](https://github.com/astral-sh/uv) for dependency management.

```bash
# Clone and install all dev deps
git clone https://github.com/your-org/openagent-python-sdk
cd openagent-python-sdk
uv sync

# Run the full test suite
uv run pytest -q

# Run a single test
uv run pytest -q tests/unit/test_runtime_core.py::MyTest::test_case

# Coverage (floor: 92%)
uv run coverage run -m pytest && uv run coverage report

# Lint
uv run ruff check .
uv run ruff format --check .
```

**Rule:** When adding, removing, or changing code under `openagents/`, you **must** add/update/remove the corresponding tests in the same change. The test suite and source are co-evolved.

---

## What's New in 0.4.0

- **`shell_exec`** — allowlist-aware subprocess tool with cwd/env/timeout controls
- **`tavily_search`** — REST-based Tavily search tool (reads `TAVILY_API_KEY`)
- **`markdown_memory`** — file-backed long-term memory (MEMORY.md index + per-section files)
- **`remember_preference`** — companion tool for agent-side preference capture
- **`openagents.utils.env_doctor`** — reusable environment health check framework
- **`openagents.cli.wizard`** — Rich + questionary wizard component for interactive CLIs
- **`examples/pptx_generator/`** — production-grade 7-stage PPT generator (`pptx-agent`)

Full changelog: [CHANGELOG.md](CHANGELOG.md) | Migration guide: [0.2 → 0.3](docs/migration-0.2-to-0.3.md)

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
