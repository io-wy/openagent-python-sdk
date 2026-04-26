"""ToolExecutor with filesystem policy embedded."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openagents.interfaces.tool import (
    PolicyDecision,
    ToolExecutionRequest,
    ToolExecutorPlugin,
)
from openagents.interfaces.typed_config import TypedConfigPluginMixin
from openagents.plugins.builtin.execution_policy.filesystem import FilesystemExecutionPolicy


class FilesystemAwareExecutor(TypedConfigPluginMixin, ToolExecutorPlugin):
    """ToolExecutor with filesystem policy built in.

    What:
        Wraps the :class:`FilesystemExecutionPolicy` helper and exposes
        it as a standalone ``tool_executor``. Delegates policy checks
        to the helper and falls back to the base
        :class:`ToolExecutorPlugin.execute` implementation for the
        actual tool invocation, which honors the policy decision and
        returns a ``ToolExecutionResult`` whose ``error`` embeds the
        helper's reason string (e.g. ``'... not in allow_tools'``,
        ``'... outside read_roots'``).

    Usage:
        Replaces the older ``execution_policy: filesystem`` seam::

            tool_executor:
              type: filesystem_aware
              config:
                allow_tools: [read_file, list_files]
                deny_tools: []
                read_roots: ["./src"]
                write_roots: ["./out"]

    Depends on:
        - :class:`FilesystemExecutionPolicy` (same config shape).
    """

    class Config(BaseModel):
        allow_tools: list[str] = Field(default_factory=list)
        deny_tools: list[str] = Field(default_factory=list)
        read_roots: list[str] = Field(default_factory=list)
        write_roots: list[str] = Field(default_factory=list)

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()
        self._policy = FilesystemExecutionPolicy(config=config)

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        return await self._policy.evaluate_policy(request)
