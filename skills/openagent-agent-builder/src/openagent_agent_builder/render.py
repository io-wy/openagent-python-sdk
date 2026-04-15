"""Render a normalized builder input into a runnable OpenAgents config bundle."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import OpenAgentSkillInput


def _merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_mapping(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _build_execution_policy(payload: OpenAgentSkillInput, tool_ids: list[str]) -> dict[str, Any] | None:
    if not payload.workspace_root:
        return None

    writes = any(tool_id in {"write_file", "delete_file"} for tool_id in tool_ids)
    read_only = bool(payload.constraints.get("read_only"))
    config: dict[str, Any] = {
        "allow_tools": tool_ids,
        "read_roots": [payload.workspace_root],
    }
    if writes and not read_only:
        config["write_roots"] = [payload.workspace_root]
    return {"type": "filesystem", "config": config}


def _filter_tools(archetype_tools: list[dict[str, Any]], allowed_tools: list[str]) -> list[dict[str, Any]]:
    if not allowed_tools:
        return deepcopy(archetype_tools)
    allowed = set(allowed_tools)
    return [deepcopy(tool) for tool in archetype_tools if tool.get("id") in allowed]


def render_agent_spec(payload: OpenAgentSkillInput, archetype: dict[str, Any]) -> dict[str, Any]:
    agent_key = payload.overrides.get("agent_key") or payload.agent_role
    agent_name = payload.overrides.get("agent_name") or archetype["agent_name"]
    tools = _filter_tools(archetype.get("tools", []), payload.available_tools)
    tool_ids = [tool["id"] for tool in tools]
    execution_policy = archetype.get("execution_policy")
    if execution_policy is None:
        execution_policy = _build_execution_policy(payload, tool_ids)

    runtime_config = _merge_mapping(archetype["runtime"], payload.constraints)
    agent_config: dict[str, Any] = {
        "id": agent_key,
        "name": agent_name,
        "memory": deepcopy(archetype["memory"]),
        "pattern": deepcopy(archetype["pattern"]),
        "llm": deepcopy(archetype["llm"]),
        "skill": deepcopy(archetype.get("skill")),
        "tool_executor": deepcopy(archetype.get("tool_executor")),
        "execution_policy": deepcopy(execution_policy),
        "context_assembler": deepcopy(archetype.get("context_assembler")),
        "followup_resolver": deepcopy(archetype.get("followup_resolver")),
        "response_repair_policy": deepcopy(archetype.get("response_repair_policy")),
        "tools": tools,
        "runtime": runtime_config,
    }

    overrides = payload.overrides
    for key in (
        "memory",
        "pattern",
        "llm",
        "skill",
        "tool_executor",
        "execution_policy",
        "context_assembler",
        "followup_resolver",
        "response_repair_policy",
        "runtime",
    ):
        override_value = overrides.get(key)
        if isinstance(override_value, dict):
            agent_config[key] = _merge_mapping(agent_config.get(key) or {}, override_value)
        elif key in overrides:
            agent_config[key] = deepcopy(override_value)
    if isinstance(overrides.get("tools"), list):
        agent_config["tools"] = deepcopy(overrides["tools"])

    sdk_config = {
        "version": "1.0",
        "runtime": {"type": "default", "config": {}},
        "session": {"type": "in_memory"},
        "events": {"type": "async"},
        "agents": [agent_config],
    }

    return {
        "agent_key": agent_key,
        "purpose": payload.task_goal,
        "sdk_config": sdk_config,
        "run_request_template": {
            "agent_id": agent_key,
            "input_text": "<filled by caller>",
            "context_hints": {
                "task_goal": payload.task_goal,
                "agent_role": payload.agent_role,
                "agent_mode": payload.agent_mode,
                "workspace_root": payload.workspace_root,
                "handoff_expectation": deepcopy(payload.handoff_expectation),
                "constraints": deepcopy(payload.constraints),
            },
            "metadata": {
                "generated_by": "openagent-agent-builder",
                "agent_role": payload.agent_role,
                "agent_mode": payload.agent_mode,
            },
        },
    }
