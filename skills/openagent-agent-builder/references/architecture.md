# Architecture

`openagent-agent-builder` sits **above** the OpenAgents SDK. It is an app-layer skill, not a seam inside the kernel.

```text
OpenAgents SDK (openagents/)
  = single-agent kernel
  = stable protocol (RunRequest / RunResult / RunContext / StopReason)
  = pluggable seams loaded from AppConfig (post 2026-04-18 consolidation: 8 seams)
        top-level: runtime, session, events, skills
        per-agent: memory, pattern, llm, tools,
                   tool_executor (embeds evaluate_policy),
                   context_assembler
        pattern-subclass overrides (no longer seams):
                   resolve_followup(), repair_empty_response()

skills/openagent-agent-builder/src/openagent_agent_builder/  (this core)
  = normalize_input      -> validate + canonicalise
  -> resolve_archetype   -> pick planner|coder|reviewer|researcher defaults
  -> render_agent_spec   -> emit a full AppConfig payload
  -> smoke_run_agent_spec -> Runtime.from_dict(...).run_detailed(...)

host adapters
  = Codex/Claude skill runner
  = an app-owned main-agent tool
  both call openagent_agent_builder.entrypoint.run_openagent_skill
```

The builder always returns **one** single-agent spec (one `AppConfig.agents[0]`). It does not schedule a team or manage cross-agent state; multi-agent orchestration remains in user code.

## Why It Lives in `skills/`, Not in the Kernel

- The single-agent kernel boundary in `CLAUDE.md` and `docs/seams-and-extension-points.md` says: do not push product semantics into the kernel. Archetype selection is a product-shaped helper, so it lives above the SDK.
- The top-level `skills` component (builtin: `LocalSkillsManager`) discovers this directory from disk and calls `skills.prepare_session()` at session start to warm the description. The Python entrypoint loads lazily.
- `tests/conftest.py` injects `skills/openagent-agent-builder/src` onto `sys.path`, so the builder ships in-tree and is testable under the main pytest run.

## What Each Stage Does

1. **normalize_input** (`normalize.py`)
   - Validates `task_goal`, `agent_role`, `agent_mode` are non-empty.
   - Slugifies role/mode, converts path separators.
   - Deduplicates tool ids.
   - Rejects non-dict `constraints`, `handoff_expectation`, `overrides`.

2. **resolve_archetype** (`archetypes.py`)
   - Deep-copies one of `planner | coder | reviewer | researcher`.
   - Each archetype supplies `memory`, `pattern`, `llm` (mock by default), `tools`, `runtime`, `handoff_contract`, `integration_hints`, and — for coder/reviewer/researcher — a `safe` `tool_executor`.

3. **render_agent_spec** (`render.py`)
   - Filters archetype `tools` to the caller's `available_tools` (empty list = keep all).
   - Swaps the archetype's `tool_executor` for a `filesystem_aware` executor when `workspace_root` is set; writes `write_roots` only when a write-capable tool is present and `constraints.read_only` is not set. (This replaces the pre-consolidation `execution_policy: filesystem` emission.)
   - Merges `constraints` into the agent's `runtime` options.
   - Deep-merges `overrides` per seam; replaces `tools` wholesale if `overrides["tools"]` is a list.
   - Emits the top-level bundle `{ version: "1.0", runtime, session, events, agents: [ ... ] }`. `skills` falls back to the schema default (`{type: "local"}`).
   - Returns `{agent_key, purpose, sdk_config, run_request_template}`.

4. **smoke_run_agent_spec** (`smoke.py`)
   - Builds `Runtime.from_dict(sdk_config)`, calls `run_detailed(request=RunRequest(...))` with the template's `agent_id`, `context_hints`, and `metadata`, and always `close()`s the runtime.
   - Reports `passed` iff `exception is None` and `stop_reason != "failed"`.

## Stability Guarantees

- `OpenAgentSkillInput` / `OpenAgentSkillOutput` shapes are the public contract of this core; host adapters depend on them.
- The rendered `sdk_config` conforms to `openagents.config.schema.AppConfig`. When the SDK schema evolves, update `render.py` and the examples file here together.
- Smoke defaults to the `mock` LLM provider so CI and local test runs never need network or credentials.
