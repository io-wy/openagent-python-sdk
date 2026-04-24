"""Agent router seam - delegate and transfer control between agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NoReturn

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunBudget, RunResult


class HandoffSignal(BaseException):
    """Raised by AgentRouterPlugin.transfer() to terminate the parent run with the child's result."""

    def __init__(self, result: "RunResult") -> None:
        super().__init__()
        self.result = result


class AgentNotFoundError(Exception):
    """Raised when the target agent_id is not found in the loaded config."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' not found in config")
        self.agent_id = agent_id


class DelegationDepthExceededError(Exception):
    """Raised when max_delegation_depth is exceeded to prevent infinite recursion."""

    def __init__(self, depth: int, limit: int) -> None:
        super().__init__(f"Delegation depth {depth} exceeds limit {limit}")
        self.depth = depth
        self.limit = limit


class AgentRouterPlugin:
    """Protocol for the agent_router seam.

    Implementations must provide delegate() and transfer().
    """

    async def delegate(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] = "isolated",
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> "RunResult":
        """Invoke a sub-agent and await its result before continuing."""
        raise NotImplementedError

    async def transfer(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] = "isolated",
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> NoReturn:
        """Transfer control to another agent permanently. Raises HandoffSignal."""
        raise NotImplementedError
