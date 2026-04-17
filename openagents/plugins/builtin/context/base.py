"""Shared base for token-budget aware context assemblers."""

from __future__ import annotations

from typing import Any

from openagents.interfaces.context import ContextAssemblerPlugin, ContextAssemblyResult


class TokenBudgetContextAssembler(ContextAssemblerPlugin):
    """Base class providing token-budget trimming helpers.

    Subclasses implement :meth:`_trim_by_budget` to decide which transcript
    entries to keep given a token budget. The base class wires
    ``count_tokens`` resolution, artifact trimming, and
    :class:`ContextAssemblyResult.metadata` population so strategies stay
    focused on ordering logic.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities=set())
        cfg = self.config
        self._max_input_tokens = int(cfg.get("max_input_tokens", 8000))
        self._max_artifacts = int(cfg.get("max_artifacts", 10))
        self._reserve_for_response = int(cfg.get("reserve_for_response", 2000))

    def _effective_budget(self) -> int:
        return max(0, self._max_input_tokens - self._reserve_for_response)

    def _measure(self, llm_client: Any, msg: dict[str, Any]) -> int:
        text = msg.get("content", "") or ""
        if llm_client is None:
            return max(1, len(text) // 4)
        try:
            return max(1, llm_client.count_tokens(text))
        except (AttributeError, TypeError):
            return max(1, len(text) // 4)

    def _trim_by_budget(
        self,
        llm_client: Any,
        msgs: list[dict[str, Any]],
        budget: int,
    ) -> tuple[list[dict[str, Any]], int]:
        raise NotImplementedError

    def _token_counter_name(self, llm_client: Any) -> str:
        provider = getattr(llm_client, "provider_name", "") if llm_client else ""
        if provider == "openai_compatible":
            try:
                import tiktoken  # type: ignore  # noqa: F401
                return "tiktoken"
            except ImportError:
                return "fallback_len//4"
        return "fallback_len//4"

    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> ContextAssemblyResult:
        llm_client = (
            session_state.get("llm_client") if isinstance(session_state, dict) else None
        )
        transcript = await session_manager.load_messages(request.session_id)
        artifacts = await session_manager.list_artifacts(request.session_id)

        budget = self._effective_budget()
        kept, omitted_messages = self._trim_by_budget(llm_client, transcript, budget)
        kept_tokens = sum(self._measure(llm_client, m) for m in kept)
        # For omitted-token count, use the messages that were not kept.
        omitted_tokens = sum(
            self._measure(llm_client, m)
            for m in transcript
            if m not in kept
        )

        if len(artifacts) > self._max_artifacts:
            omitted_artifacts = len(artifacts) - self._max_artifacts
            artifacts = artifacts[-self._max_artifacts:]
        else:
            omitted_artifacts = 0

        strategy = type(self).__name__.replace("ContextAssembler", "").lower()
        return ContextAssemblyResult(
            transcript=kept,
            session_artifacts=artifacts,
            metadata={
                "assembler": type(self).__name__,
                "strategy": strategy,
                "budget_input_tokens": self._max_input_tokens,
                "kept_tokens": kept_tokens,
                "omitted_messages": omitted_messages,
                "omitted_tokens": omitted_tokens,
                "omitted_artifacts": omitted_artifacts,
                "token_counter": self._token_counter_name(llm_client),
            },
        )

    async def finalize(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
        result: Any,
    ) -> Any:
        return result
