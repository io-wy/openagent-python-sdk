"""Agent router seam - delegate and transfer control between agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NoReturn

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunBudget, RunResult


# Reserved key used by DefaultAgentRouter to propagate delegation depth through
# RunRequest.metadata instead of a process-level dict. Consumers SHOULD NOT
# overwrite this key; it is managed entirely by the router.
DELEGATION_DEPTH_KEY = "__openagents_delegation_depth__"


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


class TaskInfo:
    """Snapshot of a background task's current state."""

    def __init__(self, task_id: str, agent_id: str, status: str):
        self.task_id = task_id
        self.agent_id = agent_id
        self.status = status      # "running" | "completed" | "failed"
        self.output: str = ""      # populated on completion
        self.error: str = ""


class AgentRouterPlugin:
    """Protocol for the agent_router seam.

    Implementations must provide delegate(), transfer(), and task_status().
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
        background: bool = False,
    ) -> "RunResult":
        """Invoke a sub-agent and return its result.

        When ``background=False`` (default): await the child run and return
        the completed RunResult.

        When ``background=True``: launch the child as a background task
        and return immediately with a RunResult whose ``task_id`` field
        identifies the running task.  Query ``task_status(task_id)`` to
        poll for completion.
        """
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

    async def task_status(self, task_id: str) -> TaskInfo | None:
        """Query the status of a background task by its ID.

        Returns None if the task_id is unknown.
        """
        raise NotImplementedError
