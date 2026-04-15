---
name: openagent-agent-builder
description: Build one runnable OpenAgents single-agent spec, smoke test it, and return integration hints for using it as a subagent or team-role agent.
---

# OpenAgent Agent Builder

Use this skill when the user or main agent needs to build **one** OpenAgents-based agent quickly.

This skill does not build a whole multi-agent runtime. It builds:

- one `subagent`
- or one role agent inside a larger team

and then runs one smoke test against the generated spec.

## What To Collect

Before building the agent, gather:

- `task_goal`
- `agent_role`
- `agent_mode`
  - `subagent`
  - `team-role`
- `workspace_root`
- `available_tools`
- `constraints`
- `handoff_expectation`
- `overrides`
- whether `smoke_run` should stay enabled

## What To Produce

Return:

- one `agent_spec`
- one short `agent_prompt_summary`
- one `design_rationale`
- one `handoff_contract`
- one `integration_hints`
- one `smoke_result`
- one `next_actions`

## How To Build

Use the shared Python core:

- `openagent_agent_builder.entrypoint.run_openagent_skill`

That adapter must remain the source of truth for:

- archetype selection
- config rendering
- smoke execution

Do not invent a separate host-specific config format.

## Important Boundary

OpenAgents SDK remains a single-agent kernel.

This skill helps build one runnable single-agent spec. It does not itself become a multi-agent team runner.

## References

- Architecture: [references/architecture.md](references/architecture.md)
- Examples: [references/examples.md](references/examples.md)
