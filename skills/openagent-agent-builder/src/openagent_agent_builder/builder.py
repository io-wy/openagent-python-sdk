"""Shared builder pipeline for the OpenAgent skill."""

from __future__ import annotations

from .archetypes import resolve_archetype
from .models import OpenAgentSkillInput, OpenAgentSkillOutput
from .normalize import normalize_input
from .render import render_agent_spec
from .smoke import smoke_run_agent_spec


def _build_design_rationale(payload: OpenAgentSkillInput, archetype: dict, spec: dict) -> str:
    tool_ids = [tool["id"] for tool in spec["sdk_config"]["agents"][0]["tools"]]
    return (
        f"Built the '{payload.agent_role}' archetype for {payload.agent_mode} work. "
        f"{archetype['design_rationale']} Selected tools: {', '.join(tool_ids) or 'none'}."
    )


def _build_handoff_contract(payload: OpenAgentSkillInput, archetype: dict) -> dict:
    base = dict(archetype.get("handoff_contract", {}))
    expected_input = payload.handoff_expectation.get("input") or base.get("expected_input") or "task brief"
    expected_output = payload.handoff_expectation.get("output") or base.get("expected_output") or "agent artifact"
    artifact_format = (
        payload.handoff_expectation.get("artifact_format")
        or base.get("artifact_format")
        or "markdown"
    )
    return {
        "expected_input": expected_input,
        "expected_output": expected_output,
        "artifact_format": artifact_format,
    }


def _build_integration_hints(payload: OpenAgentSkillInput, archetype: dict, handoff_contract: dict) -> dict:
    hints = dict(archetype.get("integration_hints", {}))
    notes = list(hints.get("notes", []))
    notes.append(
        f"Pass `{handoff_contract['expected_input']}` into this agent and expect `{handoff_contract['expected_output']}` back."
    )
    return {
        "agent_mode": payload.agent_mode,
        "workspace_root": payload.workspace_root,
        "preferred_position": hints.get("preferred_position", "middle"),
        "artifact_format": handoff_contract["artifact_format"],
        "notes": notes,
    }


def _build_next_actions(smoke_result: dict, payload: OpenAgentSkillInput) -> list[str]:
    if smoke_result.get("status") == "passed":
        return [
            f"Wire this {payload.agent_role} agent into the main agent's team design.",
            "Replace the default mock LLM with provider overrides when moving to non-local execution.",
        ]
    return [
        "Inspect the generated sdk_config and fix the failing component.",
        "Re-run the openagent skill after adjusting overrides or available tools.",
    ]


async def build_openagent_skill_output(payload: OpenAgentSkillInput) -> OpenAgentSkillOutput:
    normalized = normalize_input(payload)
    archetype = resolve_archetype(normalized.agent_role)
    spec = render_agent_spec(normalized, archetype)
    smoke = (
        await smoke_run_agent_spec(spec, smoke_input=normalized.task_goal)
        if normalized.smoke_run
        else {"status": "skipped"}
    )
    handoff_contract = _build_handoff_contract(normalized, archetype)
    integration_hints = _build_integration_hints(normalized, archetype, handoff_contract)

    return OpenAgentSkillOutput(
        agent_spec=spec,
        agent_prompt_summary=archetype["prompt_summary"],
        design_rationale=_build_design_rationale(normalized, archetype, spec),
        handoff_contract=handoff_contract,
        integration_hints=integration_hints,
        smoke_result=smoke,
        next_actions=_build_next_actions(smoke, normalized),
    )
