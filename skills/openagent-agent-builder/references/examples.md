# Examples

## Reviewer

Input:

```json
{
  "task_goal": "Review a patch and return findings.",
  "agent_role": "reviewer",
  "agent_mode": "team-role",
  "workspace_root": "C:/repo",
  "available_tools": ["read_file", "ripgrep", "list_files"]
}
```

Expected output shape:

- `agent_spec.agent_key = "reviewer"`
- `sdk_config.agents[0].pattern.type = "react"`
- `smoke_result.status = "passed" | "failed"`

## Planner

Input:

```json
{
  "task_goal": "Plan how to refactor the runtime module.",
  "agent_role": "planner",
  "agent_mode": "subagent",
  "available_tools": ["search", "read_file", "list_files"]
}
```

Expected output shape:

- `agent_spec.agent_key = "planner"`
- `sdk_config.agents[0].pattern.type = "plan_execute"`
- `handoff_contract.expected_output = "plan"`
