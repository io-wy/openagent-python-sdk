"""Custom ToolExecutor for the research-analyst example.

Combines:
- filesystem sandboxing (``FilesystemExecutionPolicy``)
- network allowlist (``NetworkAllowlistExecutionPolicy``)
- retry/backoff on classified errors (``RetryToolExecutor``)
- basic timeouts via the inner ``SafeToolExecutor``

This shows how to fold the former ``execution_policy`` seam into a custom
``tool_executor`` subclass by overriding ``evaluate_policy()`` and delegating
execution to an inner chain. The same pattern applies to any app that needs
more than one policy helper.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openagents.interfaces.tool import (
    PolicyDecision,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutorPlugin,
)
from openagents.plugins.builtin.execution_policy.composite import CompositePolicy
from openagents.plugins.builtin.execution_policy.filesystem import FilesystemExecutionPolicy
from openagents.plugins.builtin.execution_policy.network import NetworkAllowlistExecutionPolicy
from openagents.plugins.builtin.tool_executor.retry import RetryToolExecutor


class SandboxedResearchExecutor(ToolExecutorPlugin):
    """ToolExecutor with filesystem + network policies and retry semantics."""

    class Config(BaseModel):
        read_roots: list[str] = Field(default_factory=list)
        write_roots: list[str] = Field(default_factory=list)
        allow_hosts: list[str] = Field(default_factory=list)
        allow_schemes: list[str] = Field(default_factory=lambda: ["http", "https"])
        applies_to_tools: list[str] = Field(default_factory=lambda: ["http_request"])
        deny_private_networks: bool = False
        default_timeout_ms: int = 200
        max_attempts: int = 3
        initial_delay_ms: int = 50
        retry_on: list[str] = Field(default_factory=lambda: ["RetryableToolError", "ToolTimeoutError"])

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        cfg = self.Config.model_validate(self.config)
        self._policy = CompositePolicy(
            children=[
                FilesystemExecutionPolicy(
                    config={
                        "read_roots": cfg.read_roots,
                        "write_roots": cfg.write_roots,
                    }
                ),
                NetworkAllowlistExecutionPolicy(
                    config={
                        "allow_hosts": cfg.allow_hosts,
                        "allow_schemes": cfg.allow_schemes,
                        "applies_to_tools": cfg.applies_to_tools,
                        "deny_private_networks": cfg.deny_private_networks,
                    }
                ),
            ],
            mode="all",
        )
        self._inner = RetryToolExecutor(
            config={
                "inner": {
                    "type": "safe",
                    "config": {"default_timeout_ms": cfg.default_timeout_ms},
                },
                "max_attempts": cfg.max_attempts,
                "initial_delay_ms": cfg.initial_delay_ms,
                "retry_on": cfg.retry_on,
            }
        )

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        return await self._policy.evaluate_policy(request)

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            reason = decision.reason or "policy denied"
            exc = PermissionError(reason)
            return ToolExecutionResult(
                tool_id=request.tool_id,
                success=False,
                error=f"policy denied: {reason}",
                exception=exc,
                metadata={"policy": decision.metadata},
            )
        return await self._inner.execute(request)

    async def execute_stream(self, request: ToolExecutionRequest):
        decision = await self.evaluate_policy(request)
        if not decision.allowed:
            yield {
                "type": "error",
                "error": f"policy denied: {decision.reason}",
            }
            return
        async for chunk in self._inner.execute_stream(request):
            yield chunk
