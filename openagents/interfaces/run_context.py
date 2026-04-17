"""Typed runtime context shared across tools, patterns, and policies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .runtime import RunArtifact, RunRequest, RunUsage
    from .session import SessionArtifact


DepsT = TypeVar("DepsT")


class RunContext(BaseModel, Generic[DepsT]):
    """Typed execution context injected into tools, patterns, and policies."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent_id: str
    session_id: str
    run_id: str = ""
    input_text: str
    deps: DepsT | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, Any] = Field(default_factory=dict)
    llm_client: Any | None = None
    llm_options: Any | None = None
    event_bus: Any
    memory_view: dict[str, Any] = Field(default_factory=dict)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    scratch: dict[str, Any] = Field(default_factory=dict)
    active_skill: str | None = None
    skill_metadata: dict[str, Any] = Field(default_factory=dict)
    system_prompt_fragments: list[str] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    session_artifacts: list["SessionArtifact"] = Field(default_factory=list)
    assembly_metadata: dict[str, Any] = Field(default_factory=dict)
    run_request: "RunRequest | None" = None
    tool_executor: Any | None = None
    usage: "RunUsage | None" = None
    artifacts: list["RunArtifact"] = Field(default_factory=list)


if not TYPE_CHECKING:
    from .runtime import RunArtifact, RunRequest, RunUsage
    from .session import SessionArtifact

    RunContext.model_rebuild()
