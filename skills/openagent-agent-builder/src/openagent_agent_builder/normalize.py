"""Input normalization helpers for the OpenAgent builder skill."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import OpenAgentSkillInput


_VALID_AGENT_MODES = {"subagent", "team-role"}


def _normalize_identifier(value: str) -> str:
    text = value.strip().lower().replace("_", "-").replace(" ", "-")
    parts = [ch for ch in text if ch.isalnum() or ch == "-"]
    cleaned = "".join(parts).strip("-")
    return cleaned


def _normalize_tool_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = _normalize_identifier(value)
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _require_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"'{field_name}' must be an object")
    return dict(value)


def normalize_input(payload: OpenAgentSkillInput) -> OpenAgentSkillInput:
    task_goal = str(payload.task_goal).strip()
    if not task_goal:
        raise ValueError("'task_goal' must be a non-empty string")

    agent_role = _normalize_identifier(str(payload.agent_role))
    if not agent_role:
        raise ValueError("'agent_role' must be a non-empty string")

    agent_mode = _normalize_identifier(str(payload.agent_mode))
    if agent_mode not in _VALID_AGENT_MODES:
        raise ValueError(f"'agent_mode' must be one of {sorted(_VALID_AGENT_MODES)}")

    workspace_root = payload.workspace_root
    if workspace_root is not None:
        workspace_root = str(Path(str(workspace_root))).replace("\\", "/")

    return replace(
        payload,
        task_goal=task_goal,
        agent_role=agent_role,
        agent_mode=agent_mode,
        workspace_root=workspace_root,
        available_tools=_normalize_tool_ids(list(payload.available_tools)),
        constraints=_require_dict(payload.constraints, "constraints"),
        handoff_expectation=_require_dict(payload.handoff_expectation, "handoff_expectation"),
        overrides=_require_dict(payload.overrides, "overrides"),
    )
