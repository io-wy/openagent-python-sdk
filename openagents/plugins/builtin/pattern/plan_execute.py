"""Plan-Execute pattern: first plan, then execute step by step."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class PlanExecutePattern(TypedConfigPluginMixin, PatternPlugin):
    """Two-phase pattern: planning first, then execution.

    What:
        Phase 1 asks the LLM to produce a numbered plan and emits
        ``pattern.plan_created``. Phase 2 walks each plan step,
        dispatching tools as needed and emitting ``pattern.phase`` /
        ``pattern.step_started`` / ``pattern.step_finished``. Useful
        when work decomposes naturally before any tool is called.

    Usage:
        ``{"type": "plan_execute", "config": {"max_steps": 16,
        "step_timeout_ms": 30000}}``

    Depends on:
        - ``RunContext.llm_client`` for plan + step generation
        - ``RunContext.tools`` for tool dispatch
        - ``RunContext.event_bus`` for plan/phase/step events
    """

    class Config(BaseModel):
        max_steps: int = 16
        step_timeout_ms: int = 30000

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()

    # Default implementations

    async def emit(self, event_name: str, **payload: Any) -> None:
        """Emit event using context's event_bus."""
        ctx = self.context
        await ctx.event_bus.emit(
            event_name,
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            **payload,
        )

    async def call_tool(self, tool_id: str, params: dict[str, Any] | None = None) -> Any:
        """Call a tool and record result."""
        return await super().call_tool(tool_id, params)

    async def call_llm(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Call the LLM."""
        return await super().call_llm(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # Pattern-specific methods

    def _max_steps(self) -> int:
        # Read from self.config (raw dict) to honor post-init runtime
        # budget overrides applied via DefaultRuntime._apply_runtime_budget.
        max_steps = self.config.get("max_steps", self.cfg.max_steps)
        if isinstance(max_steps, int) and max_steps > 0:
            return max_steps
        return 16

    def _step_timeout_ms(self) -> int:
        timeout = self.config.get("step_timeout_ms", self.cfg.step_timeout_ms)
        if isinstance(timeout, int) and timeout > 0:
            return timeout
        return 30000

    def _llm_enabled(self) -> bool:
        ctx = self.context
        return ctx.llm_client is not None

    def _format_history(self, history: list) -> str:
        """Format history for LLM prompt."""
        if not history:
            return "(no conversation history)"

        lines = []
        for item in history[-5:]:  # Last 5 entries
            if isinstance(item, dict):
                user_msg = item.get("input", "")
                assistant_msg = item.get("output", "")
                if user_msg:
                    lines.append(f"User: {user_msg}")
                if assistant_msg:
                    lines.append(f"Assistant: {assistant_msg}")
        return "\n".join(lines) if lines else "(no conversation history)"

    def _planning_prompt(self) -> str:
        ctx = self.context
        history = ctx.memory_view.get("history", [])
        history_text = self._format_history(history)

        return self.compose_system_prompt(
            "You are a planner for an agent runtime.\n"
            "Given the user input and conversation history, create a detailed step-by-step plan.\n"
            f"CONVERSATION_HISTORY:\n{history_text}\n"
            "Return only JSON with this structure:\n"
            '{"plan": [{"step": 1, "action": "tool_call", "tool": "tool_id", "params": {...}},'
            ' {"step": 2, "action": "final", "content": "..."}]}\n'
            "No markdown, no extra text."
        )

    def _execution_prompt(self, step_num: int, plan: list) -> str:
        ctx = self.context
        tool_ids = sorted(ctx.tools.keys())
        history = ctx.memory_view.get("history", [])
        history_text = self._format_history(history)

        return self.compose_system_prompt(
            f"Execute step {step_num} of the plan.\n"
            f"Current input: {ctx.input_text}\n"
            f"CONVERSATION_HISTORY:\n{history_text}\n"
            f"Available tools: {', '.join(tool_ids)}\n"
            f"Return JSON:\n"
            '{"type":"tool_call","tool":"id","params":{...}} or {"type":"final","content":"..."}\n'
            "No markdown."
        )

    def _parse_llm_response(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        # Try to find JSON in text
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"type": "final", "content": raw}

    async def _plan(self) -> list[dict[str, Any]]:
        """Phase 1: Create a plan."""
        ctx = self.context
        messages = [
            {"role": "system", "content": self._planning_prompt()},
            {"role": "user", "content": ctx.input_text},
        ]
        raw = await self.call_llm(messages=messages)
        # Empty-response repair: the planning call is the only LLM boundary
        # in this pattern, so recover here before downstream parsing turns
        # empty text into an empty plan.
        if not (raw or "").strip():
            repair = await self.repair_empty_response(
                context=ctx,
                messages=messages,
                assistant_content=[],
                stop_reason=None,
                retries=0,
            )
            if repair is not None and repair.status == "repaired":
                raw = repair.output
        result = self._parse_llm_response(raw)

        plan = result.get("plan", [])
        if not isinstance(plan, list):
            plan = [{"type": "final", "content": str(plan)}]
        return plan

    async def _execute_plan(self, plan: list[dict[str, Any]]) -> str:
        """Phase 2: Execute the plan step by step."""
        max_steps = self._max_steps()
        results = []

        for i, step in enumerate(plan[:max_steps]):
            step_num = i + 1
            await self.emit("pattern.step_started", step=step_num, plan_step=step)

            action_type = step.get("action") or step.get("type")

            if action_type == "tool_call":
                tool_id = step.get("tool")
                params = step.get("params", {})
                try:
                    await self.call_tool(tool_id, params)
                    results.append(f"Step {step_num}: {tool_id} completed")
                except Exception as e:
                    results.append(f"Step {step_num}: {tool_id} failed - {e}")
                continue

            # Assume final/continue
            content = step.get("content", step.get("result", ""))
            results.append(f"Step {step_num}: {content}")
            if action_type == "final":
                return content

        return "\n".join(results) if results else "Plan executed"

    async def react(self) -> dict[str, Any]:
        """Single step - not used in PlanExecute, use execute instead."""
        return {"type": "final", "content": "Use execute() for PlanExecute pattern"}

    async def execute(self) -> Any:
        """Execute the complete Plan-Execute workflow."""
        self._inject_validation_correction()
        ctx = self.context

        # Followup short-circuit: allow a resolver to answer locally and skip
        # both planning and execution phases entirely.
        resolution = await self.resolve_followup(context=ctx)
        if resolution is not None and resolution.status == "resolved":
            if ctx.state is not None:
                ctx.state["_runtime_last_output"] = resolution.output
                ctx.state["resolved_by"] = "resolve_followup"
            return resolution.output

        if not self._llm_enabled():
            return {"type": "final", "content": "PlanExecute requires LLM"}

        await self.emit("pattern.phase", phase="planning")

        # Phase 1: Create plan
        plan = await self._plan()
        ctx.scratch["_plan"] = plan

        await self.emit("pattern.phase", phase="executing")
        await self.emit("pattern.plan_created", plan=plan)

        # Phase 2: Execute plan
        result = await self._execute_plan(plan)

        ctx.state["_runtime_last_output"] = result
        return result
