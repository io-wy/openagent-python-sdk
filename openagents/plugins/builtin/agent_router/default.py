"""Default AgentRouterPlugin: delegate and transfer seam implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal, NoReturn
from uuid import uuid4

from openagents.interfaces.agent_router import (
    AgentRouterPlugin,
    DelegationDepthExceededError,
    HandoffSignal,
)
from openagents.interfaces.runtime import RunRequest

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunBudget, RunResult


class DefaultAgentRouter(AgentRouterPlugin):
    """Default agent_router seam implementation for multi-agent delegation and handoff.

    What: Provides ``delegate()`` (await a sub-agent's result) and
    ``transfer()`` (hand control to another agent, ending the parent run)
    with configurable session isolation (``shared`` / ``isolated`` /
    ``forked``) and recursion depth limiting.

    Usage: Enabled by setting ``multi_agent.enabled: true`` in AppConfig;
    ``Runtime.__init__`` wires ``_run_fn = self.run_detailed`` so the
    router can recursively invoke ``Runtime.run_detailed`` from within a
    running pattern. Patterns and tools receive the router via
    ``ctx.agent_router`` on the ``RunContext``. Nested delegations are
    bounded by ``max_delegation_depth`` (default 5); exceeding the limit
    raises ``DelegationDepthExceededError``. ``transfer()`` raises
    ``HandoffSignal`` (a ``BaseException``) which ``DefaultRuntime.run``
    catches to return the child's ``final_output`` as the parent's result.

    Depends on: ``Runtime.run_detailed`` (injected as ``_run_fn`` post-
    construction); ``RunRequest`` / ``RunResult`` / ``RunContext`` from
    ``openagents.interfaces``.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._max_depth: int = int(cfg.get("max_delegation_depth", 5))
        self._default_isolation: Literal["shared", "isolated", "forked"] = cfg.get(
            "default_session_isolation", "isolated"
        )
        self._run_fn: Callable | None = None
        self._run_depths: dict[str, int] = {}

    async def delegate(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] | None = None,
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> "RunResult":
        isolation = session_isolation if session_isolation is not None else self._default_isolation
        self._check_depth(ctx)
        if self._run_fn is None:
            raise RuntimeError(
                "DefaultAgentRouter._run_fn not set; Runtime wiring incomplete. "
                "Ensure Runtime.__init__ sets agent_router._run_fn = self.run_detailed."
            )
        child_request = RunRequest(
            agent_id=agent_id,
            session_id=self._resolve_session(ctx, isolation),
            input_text=input_text,
            parent_run_id=ctx.run_id,
            budget=budget,
            deps=deps if deps is not None else ctx.deps,
        )
        result = await self._run_fn(request=child_request)
        parent_depth = self._run_depths.get(ctx.run_id, 0)
        self._run_depths[result.run_id] = parent_depth + 1
        return result

    async def transfer(
        self,
        agent_id: str,
        input_text: str,
        ctx: "RunContext",
        *,
        session_isolation: Literal["shared", "isolated", "forked"] | None = None,
        budget: "RunBudget | None" = None,
        deps: Any = None,
    ) -> NoReturn:
        result = await self.delegate(
            agent_id,
            input_text,
            ctx,
            session_isolation=session_isolation,
            budget=budget,
            deps=deps,
        )
        raise HandoffSignal(result)

    def _resolve_session(self, ctx: "RunContext", isolation: str) -> str:
        if isolation == "shared":
            return ctx.session_id
        if isolation == "forked":
            return f"{ctx.session_id}:fork:{ctx.run_id}"
        return f"child:{ctx.run_id}:{uuid4().hex[:8]}"

    def _check_depth(self, ctx: "RunContext") -> None:
        depth = self._run_depths.get(ctx.run_id, 0)
        if depth >= self._max_depth:
            raise DelegationDepthExceededError(depth=depth, limit=self._max_depth)
