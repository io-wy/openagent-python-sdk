"""Thin host adapter for main-agent or tool-based invocation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .builder import build_openagent_skill_output
from .models import OpenAgentSkillInput


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    input_obj = OpenAgentSkillInput(**payload)
    result = await build_openagent_skill_output(input_obj)
    return asdict(result)
