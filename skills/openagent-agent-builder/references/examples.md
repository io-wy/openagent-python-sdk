# Examples

All inputs map one-to-one onto `OpenAgentSkillInput` (see `src/openagent_agent_builder/models.py`). The entrypoint returns the dict form of `OpenAgentSkillOutput`.

## Reviewer (team-role, read-only)

```json
{
  "task_goal": "Review a patch and return findings.",
  "agent_role": "reviewer",
  "agent_mode": "team-role",
  "workspace_root": "C:/repo",
  "available_tools": ["read_file", "ripgrep", "list_files"],
  "handoff_expectation": {
    "input": "patch",
    "output": "findings"
  }
}
```

Expected output shape:

- `agent_spec.agent_key == "reviewer"`
- `agent_spec.sdk_config.agents[0].pattern.type == "react"`
- `agent_spec.sdk_config.agents[0].tool_executor.type == "filesystem_aware"`
  (`read_roots = ["C:/repo"]`, no `write_roots` because no write tool requested)
- `handoff_contract.expected_output == "findings"`
- `smoke_result.status == "passed"` (mock LLM)

## Planner (subagent)

```json
{
  "task_goal": "Plan how to refactor the runtime module.",
  "agent_role": "planner",
  "agent_mode": "subagent",
  "available_tools": ["search", "read_file", "list_files"]
}
```

Expected output shape:

- `agent_spec.agent_key == "planner"`
- `agent_spec.sdk_config.agents[0].pattern.type == "plan_execute"`
- `handoff_contract.expected_output == "plan"`
- `integration_hints.preferred_position == "upstream"`

## Coder (team-role, with write access)

```json
{
  "task_goal": "Apply the reviewer's findings as minimal diffs.",
  "agent_role": "coder",
  "agent_mode": "team-role",
  "workspace_root": "C:/repo",
  "available_tools": ["read_file", "write_file", "list_files", "ripgrep"],
  "constraints": {
    "max_steps": 12,
    "step_timeout_ms": 45000
  }
}
```

Expected output shape:

- `agent_spec.sdk_config.agents[0].pattern.type == "react"`
- `agent_spec.sdk_config.agents[0].tool_executor.type == "filesystem_aware"`
- `agent_spec.sdk_config.agents[0].tool_executor.config.write_roots == ["C:/repo"]`
  (write root is added because `write_file` is present and `read_only` is not set)
- `agent_spec.sdk_config.agents[0].runtime.max_steps == 12`

Dropping `write_file` from `available_tools`, or adding `"constraints": {"read_only": true}`, suppresses `write_roots`.

## Researcher (subagent, reflexion)

```json
{
  "task_goal": "Investigate how other SDKs handle streaming tool-call deltas.",
  "agent_role": "researcher",
  "agent_mode": "subagent",
  "available_tools": ["search", "http_request", "url_parse", "query_param"]
}
```

Expected output shape:

- `agent_spec.sdk_config.agents[0].pattern.type == "reflexion"`
- `agent_spec.sdk_config.agents[0].pattern.config.max_retries == 2`
- `integration_hints.preferred_position == "upstream"`

## Swapping the Mock LLM for a Real Provider

Archetypes default to `llm.provider = "mock"` so the smoke run never needs network. Use `overrides.llm` to point at a real provider; the permitted values are `mock`, `anthropic`, and `openai_compatible`.

```json
{
  "task_goal": "Review a patch and return findings.",
  "agent_role": "reviewer",
  "agent_mode": "team-role",
  "workspace_root": "C:/repo",
  "overrides": {
    "llm": {
      "provider": "anthropic",
      "model": "${LLM_MODEL}",
      "api_base": "${LLM_API_BASE}",
      "api_key_env": "LLM_API_KEY",
      "temperature": 0.1,
      "max_tokens": 2048
    }
  },
  "smoke_run": false
}
```

Notes:

- `${VAR}` placeholders are substituted by the config loader when the spec is read from disk; when calling `Runtime.from_dict(...)` in-process, substitute them yourself.
- Set `smoke_run: false` if the environment has no credentials for the real provider.
- For `openai_compatible`, `api_base` is required.

## Overriding the Context Assembler

```json
{
  "task_goal": "Produce a focused patch report.",
  "agent_role": "reviewer",
  "agent_mode": "team-role",
  "workspace_root": "C:/repo",
  "overrides": {
    "context_assembler": {
      "type": "head_tail",
      "config": { "head_messages": 4, "tail_messages": 8 }
    }
  }
}
```

Any per-agent seam (`memory`, `pattern`, `tool_executor`, `context_assembler`, `runtime`, `tools`)
can be overridden this way. Dict values deep-merge into the archetype default; `tools` as a list
replaces the list entirely.

### Follow-up and Response Repair

Post seam-consolidation (2026-04-18), follow-up resolution and empty-response repair are
**pattern-subclass method overrides** (`PatternPlugin.resolve_followup()` /
`PatternPlugin.repair_empty_response()`) rather than standalone seams. To customize them,
pass a custom pattern via `overrides.pattern.impl`:

```json
{
  "overrides": {
    "pattern": {
      "impl": "myapp.agents.patterns.StrictJsonReActPattern",
      "config": { "max_steps": 6 }
    }
  }
}
```

See `examples/research_analyst/app/followup_pattern.py` for a working override.

## Pointing at a Custom Plugin Class

Use `impl` instead of `type` in an override when the caller brings its own plugin:

```json
{
  "overrides": {
    "pattern": {
      "impl": "myapp.agents.patterns.StructuredPlanPattern",
      "config": { "max_steps": 6 }
    }
  }
}
```

`type` and `impl` are mutually exclusive per plugin ref (`AppConfig` rejects configs that set both).
