---
name: openagent-agent-builder
description: Build one runnable OpenAgents single-agent spec, smoke test it with the real Runtime, and return a handoff contract plus integration hints for using it as a subagent or team-role agent.
---

# OpenAgent Agent Builder

Use this skill when the user or main agent needs to build **one** OpenAgents-based agent quickly.

OpenAgents SDK is a **single-agent kernel**. This skill stays inside that boundary: it synthesises one runnable `AppConfig` (one `agents[...]` entry plus the top-level `runtime/session/events/skills` selectors), smoke runs it through the real `Runtime`, and hands back the spec. It does **not** schedule a multi-agent team.

It builds:

- one `subagent` — an agent the main agent calls as a tool, or
- one role agent inside a larger team — one seat in a team the user assembles elsewhere.

## What To Collect

Before building the agent, gather:

- `task_goal` (required) — the one-line job this agent is being built for.
- `agent_role` (required) — one of `planner`, `coder`, `reviewer`, `researcher` (archetype keys; see `src/openagent_agent_builder/archetypes.py`).
- `agent_mode` (required) — `subagent` or `team-role`.
- `workspace_root` — absolute or repo-relative path; when present a `filesystem_aware` tool_executor is emitted automatically (embeds the filesystem sandbox).
- `available_tools` — list of tool ids the caller permits. Filters the archetype's default tool list; unknown ids are dropped.
- `constraints` — free-form dict merged into `runtime:` options (e.g. `max_steps`, `step_timeout_ms`) plus recognised flags like `read_only` (suppresses `write_roots`).
- `handoff_expectation` — `{input, output, artifact_format}`; fills the generated `handoff_contract`.
- `overrides` — per-seam overrides: `agent_key`, `agent_name`, `memory`, `pattern`, `llm`, `tool_executor`, `context_assembler`, `runtime`, `tools`. Dict values deep-merge; lists (`tools`) replace.
- `smoke_run` — leave `true` to actually execute one `Runtime.run_detailed(...)` against the generated spec.

## What The Skill Returns

A single `OpenAgentSkillOutput` (see `src/openagent_agent_builder/models.py`):

- `agent_spec` — `{ agent_key, purpose, sdk_config, run_request_template }` where `sdk_config` is a valid `AppConfig` payload.
- `agent_prompt_summary` — one-line description of the archetype's intent.
- `design_rationale` — why this archetype + tool set was chosen.
- `handoff_contract` — `{ expected_input, expected_output, artifact_format }`.
- `integration_hints` — `{ agent_mode, workspace_root, preferred_position, artifact_format, notes }`.
- `smoke_result` — `{ status: "passed" | "failed" | "skipped", ... }`.
- `next_actions` — short list of what the caller should do next (e.g. swap in a real LLM provider).

## How To Build

Always go through the shared core:

```python
from openagent_agent_builder.entrypoint import run_openagent_skill

result = await run_openagent_skill({
    "task_goal": "Review a patch and return findings.",
    "agent_role": "reviewer",
    "agent_mode": "team-role",
    "workspace_root": "C:/repo",
    "available_tools": ["read_file", "ripgrep", "list_files"],
})
```

That entrypoint is the source of truth for: archetype selection, config rendering, smoke execution. Do not invent a separate host-specific config shape — host adapters (Codex/Claude skill, app-owned tool) call this entrypoint and forward its dict result.

Pipeline (see `src/openagent_agent_builder/`):

1. `normalize.normalize_input` — validates required fields, slugifies ids, dedupes tool ids.
2. `archetypes.resolve_archetype` — returns a deep copy of the role's default `memory/pattern/llm/tool_executor/tools/runtime/handoff_contract/integration_hints`.
3. `render.render_agent_spec` — filters tools to `available_tools`, merges `overrides`, swaps in a `filesystem_aware` tool_executor (with `read_roots` / `write_roots`) when `workspace_root` is set, emits the `AppConfig` bundle.
4. `smoke.smoke_run_agent_spec` — spins up `Runtime.from_dict(sdk_config)`, runs one `RunRequest`, closes the runtime.

## SDK Surface It Targets

The rendered `sdk_config` conforms to `openagents.config.schema.AppConfig` (current version `1.0`):

- **Top-level seams**: `runtime` (`default`), `session` (`in_memory` / `jsonl_file` / `sqlite`), `events` (`async` / `file_logging` / `otel_bridge`), `skills` (`local`). The renderer fills in `default` / `in_memory` / `async` / `local`; override via `overrides["runtime"]` etc.
- **Agent seams** (post 2026-04-18 consolidation, 11→8): `memory`, `pattern`, optional `tool_executor`, `context_assembler`, `tools`, `llm`, `runtime`.

The former `execution_policy` / `followup_resolver` / `response_repair_policy` agent seams were
folded into existing seams:

- tool policy → `ToolExecutorPlugin.evaluate_policy()` method (builtin `filesystem_aware` shows it)
- follow-up resolution → `PatternPlugin.resolve_followup()` method override
- empty-response repair → `PatternPlugin.repair_empty_response()` method override

Registered builtin `type:` keys you can request via `overrides`:

| Seam | Valid `type:` keys |
| --- | --- |
| `memory` | `buffer`, `window_buffer`, `chain`, `mem0` |
| `pattern` | `react`, `plan_execute`, `reflexion` |
| `tool_executor` | `safe`, `retry`, `filesystem_aware` |
| `context_assembler` | `truncating`, `head_tail`, `sliding_window`, `importance_weighted` |
| `tool` | `builtin_search`, `read_file`, `write_file`, `list_files`, `delete_file`, `grep_files`, `ripgrep`, `json_parse`, `text_transform`, `http_request`, `execute_command`, `get_env`, `set_env`, `current_time`, `date_parse`, `date_diff`, `random_int`, `random_choice`, `random_string`, `uuid`, `url_parse`, `url_build`, `query_param`, `host_lookup`, `calc`, `percentage`, `min_max`, `mcp` |

Use `impl: "pkg.module.ClassName"` in an override when the caller wants a custom plugin class
(e.g. a custom `tool_executor` that combines filesystem + network policies — see
`examples/research_analyst/app/executor.py`). `type` and `impl` are mutually exclusive per plugin ref.

## LLM Defaults (Important)

Archetypes default to `llm = {"provider": "mock", "temperature": 0.0}` so the smoke run never calls a real provider. Any real deployment **must** override `llm`. The permitted providers are `mock`, `anthropic`, and `openai_compatible` (see `LLMOptions` in `openagents/config/schema.py`); `openai_compatible` additionally requires `api_base`.

## Smoke Run Semantics

`smoke_run_agent_spec` reports `status = "passed"` when:

- `Runtime.from_dict(sdk_config)` accepts the config (i.e. config validation + plugin loading succeed), **and**
- `run_detailed(...)` returns a `RunResult` whose `stop_reason` is not `failed` and whose `exception` is `None`.

Any other outcome (exception during construction, `stop_reason == "failed"`, etc.) returns `status = "failed"` with an `error` string. The smoke result reports `stop_reason` so callers can distinguish `completed` from `max_steps`.

## Important Boundary

- The builder is an app-layer helper; it **does not** register a seam inside the kernel.
- The top-level `skills` component (`LocalSkillsManager`) discovers this skill from disk and calls `skills.prepare_session()` on session start to warm the description; the full entrypoint loads on demand.
- Team orchestration (mailboxes, schedulers, cross-agent retry, approval UX) remains outside the SDK and outside this skill.

## References

- Architecture & pipeline: [references/architecture.md](references/architecture.md)
- Worked examples (reviewer, planner, coder, researcher; LLM override; read-only): [references/examples.md](references/examples.md)
- Top-level doc for consumers: `docs/openagent-agent-builder.md`
- SDK layering + seams catalogue: `docs/seams-and-extension-points.md`
