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


def _build_tool_executor(payload: OpenAgentSkillInput, tool_ids: list[str]) -> dict[str, Any] | None:
    """Build a filesystem-aware tool executor config from workspace constraints.

    The legacy `execution_policy` seam has been consolidated into the
    `tool_executor` seam via the `FilesystemAwareExecutor` builtin. When the
    payload specifies a workspace root, we surface it as read/write sandbox
    roots so path-mutating tools remain scoped to the intended project.

    Returns ``None`` when neither a workspace root nor a tool allowlist is
    present (in that case the archetype default — typically the plain ``safe``
    executor — is preferred).
    """
    workspace_root = getattr(payload, "workspace_root", None)
    if not workspace_root and not tool_ids:
        return None

    config: dict[str, Any] = {}
    if tool_ids:
        config["allow_tools"] = list(tool_ids)
    if workspace_root:
        config["read_roots"] = [workspace_root]
        config["write_roots"] = [workspace_root]

    if not config:
        return None

    return {
        "type": "filesystem_aware",
        "config": config,
    }


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
    archetype_tool_executor = archetype.get("tool_executor")
    if archetype_tool_executor is None:
        archetype_tool_executor = _build_tool_executor(payload, tool_ids)
    else:
        # If the archetype supplied its own executor but the payload carries
        # workspace/tool constraints, the filesystem_aware config still wins —
        # an explicit workspace_root is a stronger signal than a generic
        # archetype default.
        filesystem_aware = _build_tool_executor(payload, tool_ids)
        if filesystem_aware is not None and getattr(payload, "workspace_root", None):
            archetype_tool_executor = filesystem_aware

    runtime_config = _merge_mapping(archetype["runtime"], payload.constraints)
    agent_config: dict[str, Any] = {
        "id": agent_key,
        "name": agent_name,
        "memory": deepcopy(archetype["memory"]),
        "pattern": deepcopy(archetype["pattern"]),
        "llm": deepcopy(archetype["llm"]),
        "tool_executor": deepcopy(archetype_tool_executor),
        "context_assembler": deepcopy(archetype.get("context_assembler")),
        "tools": tools,
        "runtime": runtime_config,
    }

    overrides = payload.overrides
    for key in (
        "memory",
        "pattern",
        "llm",
        "tool_executor",
        "context_assembler",
        "runtime",
    ):
        override_value = overrides.get(key)
        if isinstance(override_value, dict):
            agent_config[key] = _merge_mapping(agent_config.get(key) or {}, override_value)
        elif key in overrides:
            agent_config[key] = deepcopy(override_value)
    if isinstance(overrides.get("tools"), list):
        agent_config["tools"] = deepcopy(overrides["tools"])

    # Drop optional per-agent seams that resolved to None; AppConfig defaults handle them.
    for optional_key in (
        "tool_executor",
        "context_assembler",
    ):
        if agent_config.get(optional_key) is None:
            agent_config.pop(optional_key, None)

    sdk_config = {
        "version": "1.0",
        "runtime": {"type": "default", "config": {}},
        "session": {"type": "in_memory"},
        "events": {"type": "async"},
        "skills": {"type": "local"},
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
