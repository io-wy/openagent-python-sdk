"""Response repair contracts for provider/runtime recovery."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseRepairDecision(BaseModel):
    """Structured decision for repairing a bad or empty model response."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: str = "abstain"
    output: str = ""
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
