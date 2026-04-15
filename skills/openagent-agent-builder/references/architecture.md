# Architecture

`openagent-agent-builder` sits above `openagent-sdk`.

```text
openagent-sdk
  = single-agent kernel

skills/openagent-agent-builder/src/openagent_agent_builder
  = normalize input
  + choose archetype
  + render sdk config
  + smoke run

host adapters
  = Codex/Claude skill
  = app-owned main-agent tool
```

The builder always returns a single-agent spec.

It does not schedule a whole team. Team orchestration remains outside the SDK and outside this skill.
