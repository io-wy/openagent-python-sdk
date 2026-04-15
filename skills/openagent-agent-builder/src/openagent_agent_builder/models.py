"""Structured input and output models for the OpenAgent builder skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OpenAgentSkillInput:
    task_goal: str
    agent_role: str
    agent_mode: str
    workspace_root: str | None = None
    available_tools: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    handoff_expectation: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)
    smoke_run: bool = True


@dataclass
class OpenAgentSkillOutput:
    agent_spec: dict[str, Any]
    agent_prompt_summary: str
    design_rationale: str
    handoff_contract: dict[str, Any]
    integration_hints: dict[str, Any]
    smoke_result: dict[str, Any]
    next_actions: list[str] = field(default_factory=list)
