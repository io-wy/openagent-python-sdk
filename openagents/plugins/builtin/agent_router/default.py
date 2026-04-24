"""Default AgentRouterPlugin: delegate and transfer seam implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal, NoReturn
from uuid import uuid4

from openagents.interfaces.agent_router import (
    DELEGATION_DEPTH_KEY,
    AgentNotFoundError,
    AgentRouterPlugin,
    DelegationDepthExceededError,
    HandoffSignal,
)
from openagents.interfaces.runtime import RunBudget, RunRequest

if TYPE_CHECKING:
    from openagents.interfaces.run_context import RunContext
    from openagents.interfaces.runtime import RunResult
    from openagents.interfaces.session import SessionManagerPlugin


class DefaultAgentRouter(AgentRouterPlugin):
    """Default agent_router seam implementation for multi-agent delegation and handoff.

    What: Provides ``delegate()`` (await a sub-agent's result) and
    ``transfer()`` (hand control to another agent, ending the parent run)
    with configurable session isolation (``shared`` / ``isolated`` /
    ``forked``), recursion depth limiting via ``RunRequest.metadata``, and
    a per-call / default budget fallback chain.

    Usage: Enabled by setting ``multi_agent.enabled: true`` in AppConfig;
    ``Runtime.__init__`` wires three post-construction fields:
    ``_run_fn = runtime.run_detailed`` (so the router can recurse),
    ``_session_manager = runtime._session`` (needed for ``forked`` mode's
    snapshot copy), and ``_agent_exists = lambda aid: aid in agents_by_id``
    (to raise ``AgentNotFoundError`` before any child run is launched).
    Patterns and tools receive the router via ``ctx.agent_router``.
    Nested delegations are bounded by ``max_delegation_depth`` (default 5);
    the depth of each child run is stored in
    ``RunRequest.metadata[DELEGATION_DEPTH_KEY]``, so no process-level
    mutable state is required. ``transfer()`` raises ``HandoffSignal``
    (a ``BaseException``) which ``DefaultRuntime.run`` catches to return
    the child's ``final_output`` as the parent's result.

    Depends on: ``Runtime.run_detailed`` / the session manager / the
    agents_by_id snapshot, all injected post-construction;
    ``RunRequest`` / ``RunResult`` / ``RunContext`` from
    ``openagents.interfaces``.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._max_depth: int = int(cfg.get("max_delegation_depth", 5))
        self._default_isolation: Literal["shared", "isolated", "forked"] = cfg.get(
            "default_session_isolation", "isolated"
        )
        raw_default_budget = cfg.get("default_child_budget")
        self._default_child_budget: RunBudget | None = self._coerce_budget(raw_default_budget)
        self._run_fn: Callable | None = None
        self._session_manager: "SessionManagerPlugin | None" = None
        self._agent_exists: Callable[[str], bool] | None = None

    @staticmethod
    def _coerce_budget(value: Any) -> RunBudget | None:
        if value is None:
            return None
        if isinstance(value, RunBudget):
            return value
        if isinstance(value, dict):
            return RunBudget.model_validate(value)
        raise TypeError(f"default_child_budget must be None, a RunBudget, or a dict, got {type(value).__name__}")

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
        if self._agent_exists is not None and not self._agent_exists(agent_id):
            raise AgentNotFoundError(agent_id)

        target_sid = self._resolve_session(ctx, isolation)
        if isolation == "forked":
            if self._session_manager is None:
                raise RuntimeError(
                    "DefaultAgentRouter._session_manager not set; 'forked' isolation "
                    "requires a session manager. Check Runtime wiring."
                )
            await self._session_manager.fork_session(ctx.session_id, target_sid)

        parent_depth = self._current_depth(ctx)
        effective_budget = budget if budget is not None else self._default_child_budget
        child_request = RunRequest(
            agent_id=agent_id,
            session_id=target_sid,
            input_text=input_text,
            parent_run_id=ctx.run_id,
            budget=effective_budget,
            deps=deps if deps is not None else ctx.deps,
            metadata={DELEGATION_DEPTH_KEY: parent_depth + 1},
        )
        return await self._run_fn(request=child_request)

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

    def _current_depth(self, ctx: "RunContext") -> int:
        metadata = getattr(getattr(ctx, "run_request", None), "metadata", None) or {}
        raw = metadata.get(DELEGATION_DEPTH_KEY, 0)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def _check_depth(self, ctx: "RunContext") -> None:
        depth = self._current_depth(ctx)
        if depth >= self._max_depth:
            raise DelegationDepthExceededError(depth=depth, limit=self._max_depth)
