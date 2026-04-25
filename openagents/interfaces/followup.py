"""Follow-up resolution contracts for multi-turn semantic recovery."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FollowupResolution(BaseModel):
    """Structured result for resolving a follow-up question locally."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: str = "abstain"
    output: Any = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
