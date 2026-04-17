# OpenAgents SDK

Build protocol-rich agents on top of a small, explicit runtime kernel.

OpenAgents is a config-as-code, async-first, pluggable SDK for developers who
want real control over agent behavior without burying everything inside one giant
`Pattern.execute()` method.

It is designed for:

- teams that want a clear agent runtime instead of a black-box framework
- developers building protocol-heavy coding, research, and workflow agents
- products that need their own middle protocols, safety rules, and context logic
- applications that want a stable kernel now and product infrastructure on top

It is deliberately **not** a multi-agent control plane. One `run` executes one
`agent_id`. Team orchestration, mailboxes, schedulers, approvals, and product UX
belong above this layer.

## Why OpenAgents

Most agent frameworks collapse three very different things into one abstraction:

1. the kernel protocol that defines what a run is
2. the runtime seams that decide how a run behaves
3. the product-specific middle protocols that only your application understands

OpenAgents keeps them separate.

```text
App / Product Protocols
    task envelopes, coding plans, review contracts, approvals, UI semantics
            |
            v
SDK Runtime Seams (post 2026-04-18 consolidation: 8 seams)
    memory, pattern, tool, tool_executor, context_assembler,
    runtime, session, events, skills
(folded into other seams: execution_policy -> tool_executor.evaluate_policy,
 followup_resolver -> PatternPlugin.resolve_followup,
 response_repair_policy -> PatternPlugin.repair_empty_response)
            |
            v
Kernel Protocols
    RunRequest, RunResult, RunContext,
    ToolExecutionRequest, ToolExecutionResult, SessionArtifact
```

That separation gives you:

- a small kernel with explicit runtime behavior
- stable extension seams instead of ad-hoc monkeypatching
- room to invent app-specific protocols without forking the SDK
- documentation and tests that can describe the system as a protocol stack

## What It Is

- a **single-agent runtime kernel**
- a **plugin-based execution model** for memory, pattern, tool, session, runtime, events, and top-level skills
- a **middle-protocol host** for execution policy, tool execution, context assembly, follow-up resolution, and response repair
- a **structured runtime contract** built around `RunRequest`, `RunResult`, `RunUsage`, `RunArtifact`, and `RunContext`

## What It Is Not

- not a built-in multi-agent platform
- not a job scheduler or queue system
- not a durable product control plane
- not a UI opinion
- not a giant catalog of seams for every possible product concern

The intended architecture is:

- OpenAgents SDK owns the kernel and a few high-value seams
- your product owns durable infra, UX, team orchestration, and business semantics
- your application invents its own middle protocols on top of the kernel carriers

## Core Mental Model

An OpenAgents application has three layers.

### 1. Kernel Protocols

These are the stable runtime objects that define what the system moves around:

- `RunRequest`
- `RunResult`
- `RunUsage`
- `RunArtifact`
- `RunContext`
- `ToolExecutionRequest`
- `ToolExecutionResult`
- `ContextAssemblyResult`
- `SessionArtifact`

### 2. SDK Seams

These are the official extension points where the runtime intentionally allows
behavior changes:

- capability seams:
  - `memory`
  - `pattern`
  - `tool`
- execution seams:
  - `tool_executor` (policy owned via `evaluate_policy()`)
  - `context_assembler`
- app infrastructure seams:
  - `runtime`
  - `session`
  - `events`
  - `skills`

Pattern-subclass method overrides (no longer standalone seams since 2026-04-18):

- `PatternPlugin.resolve_followup()` — local follow-up short-circuit
- `PatternPlugin.repair_empty_response()` — empty/bad response recovery

### 3. App-Defined Middle Protocols

This is where most high-design-density agents should live.

Examples:

- coding-task envelopes
- review contracts
- action summaries
- permission envelopes
- retrieval plans
- artifact taxonomies

OpenAgents does not try to predefine all of these. Instead, it gives you carriers:

- `RunRequest.context_hints`
- `RunRequest.metadata`
- `RunContext.state`
- `RunContext.scratch`
- `RunContext.assembly_metadata`
- `RunArtifact.metadata`

## Runtime Architecture

The runtime is intentionally explicit:

```text
Caller
  -> Runtime facade
    -> Runtime plugin
      -> Session manager + Event bus
      -> Context assembler
      -> Pattern setup
      -> Memory inject / writeback
      -> Bound tools (policy + executor)
      -> LLM provider
      -> RunResult
```

At the code level:

- `Runtime` is the public facade
- `DefaultRuntime` is the builtin orchestrator
- `RunContext` is the per-run state carrier for tools and patterns
- plugins are loaded from builtin registry, decorator registry, or `impl` paths

## Quick Start

Install:

```bash
uv add io-openagent-sdk
```

Optional extras:

```bash
uv add "io-openagent-sdk[openai]"
uv add "io-openagent-sdk[mem0]"
uv add "io-openagent-sdk[mcp]"
uv add "io-openagent-sdk[all]"
```

Minimal config:

```json
{
  "version": "1.0",
  "agents": [
    {
      "id": "assistant",
      "name": "demo-agent",
      "memory": {"type": "window_buffer", "on_error": "continue"},
      "pattern": {"type": "react"},
      "llm": {"provider": "mock"},
      "tools": [
        {"id": "search", "type": "builtin_search"}
      ]
    }
  ]
}
```

Async usage:

```python
import asyncio

from openagents import Runtime


async def main() -> None:
    runtime = Runtime.from_config("agent.json")
    result = await runtime.run(
        agent_id="assistant",
        session_id="demo",
        input_text="hello",
    )
    print(result)


asyncio.run(main())
```

Sync usage:

```python
from openagents import run_agent

result = run_agent(
    "agent.json",
    agent_id="assistant",
    session_id="demo",
    input_text="hello",
)
print(result)
```

Structured sync usage:

```python
from openagents import run_agent_detailed, run_agent_with_dict

result = run_agent_detailed(
    "agent.json",
    agent_id="assistant",
    session_id="demo",
    input_text="hello",
)

inline = run_agent_with_dict(
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

## Builtin Components

Builtin memory:

- `buffer`
- `window_buffer`
- `mem0`
- `chain`

Builtin pattern:

- `react`
- `plan_execute`
- `reflexion`

Builtin app infrastructure:

- runtime: `default`
- session manager: `in_memory`
- event bus: `async`

Builtin execution seams:

- tool executor: `safe`
- execution policy: `filesystem`
- context assembler: `summarizing`
- follow-up resolver: `basic`
- response repair policy: `basic`

Builtin tools:

- Search: `builtin_search`
- Files: `read_file`, `write_file`, `list_files`, `delete_file`
- Text: `grep_files`, `ripgrep`, `json_parse`, `text_transform`
- HTTP / network: `http_request`, `url_parse`, `url_build`, `query_param`, `host_lookup`
- System: `execute_command`, `get_env`, `set_env`
- Time: `current_time`, `date_parse`, `date_diff`
- Random: `random_int`, `random_choice`, `random_string`, `uuid`
- Math: `calc`, `percentage`, `min_max`
- MCP bridge: `mcp`

## Selector Rules

OpenAgents uses two selectors:

- `type`
  - choose a builtin plugin or a decorator-registered plugin
- `impl`
  - import a Python symbol by dotted path

Rules:

- top-level `runtime`, `session`, and `events` must choose one selector
- agent-level plugins and seams must set at least one of `type` or `impl`
- if an agent-level selector sets both, loader prefers `impl`

## Why The Seams Are Limited

OpenAgents does **not** try to ship a seam for every product problem.

That is intentional. The current rule is:

- keep kernel protocols fixed
- expose a small number of high-value runtime seams
- expose a top-level `skills` component for host-style skill packages
- let developers invent application protocols themselves

If your problem is:

- "how should this tool run, and is it allowed?"
  - use `tool_executor` (override `evaluate_policy()` for permission)
- "what context enters this run?"
  - use `context_assembler`
- "can this follow-up be answered locally?"
  - override `PatternPlugin.resolve_followup()` on your pattern subclass
- "how should a bad provider response degrade?"
  - override `PatternPlugin.repair_empty_response()` on your pattern subclass
- "how should my coding agent represent review tasks, work plans, or product state?"
  - design an app protocol on top of the kernel carriers

## Examples

This repo currently ships two maintained examples:

- [examples/quickstart](examples/quickstart)
  - builtin-only setup for first contact with the kernel
- [examples/production_coding_agent](examples/production_coding_agent)
  - a production-style coding agent showing task packets, persistent memory, follow-up recovery, delivery artifacts, and app-defined protocols built above the SDK

For the full example guide, read [docs/examples.md](docs/examples.md).

## Documentation

Developer docs live in [docs/](docs/README.md).

Recommended reading order:

1. [Repository Layout](docs/repository-layout.md)
2. [Developer Guide](docs/developer-guide.md)
3. [Seams And Extension Points](docs/seams-and-extension-points.md)
4. [Configuration](docs/configuration.md)
5. [Plugin Development](docs/plugin-development.md)
6. [API Reference](docs/api-reference.md)
7. [Examples](docs/examples.md)

## Current Boundary

OpenAgents is already a strong base for protocol-rich single-agent systems.

The next layer up should be:

- multi-agent orchestration
- background jobs
- approvals
- durable infra
- UI and product workflows

That layer should consume this SDK, not be forced into it.
