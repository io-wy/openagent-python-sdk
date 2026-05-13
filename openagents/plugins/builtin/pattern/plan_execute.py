"""Plan-Execute pattern: first plan, then execute step by step.

Uses native LLM function-calling (``tools`` / ``tool_choice``) when the
provider supports it, falling back to prompt-based JSON control when
``native_tool_calls: false`` is set in config.
"""

from __future__ import annotations

import asyncio
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

        When ``native_tool_calls`` is ``true`` (default), the execution
        phase uses native LLM ``tool_calls`` instead of prompt-based
        JSON parsing. The LLM receives structured tool schemas, may
        emit ``tool_calls``, and the pattern feeds results back as
        standardized ``tool_result`` messages until a final answer
        is produced.

    Usage:
        ``{"type": "plan_execute", "config": {"max_steps": 16,
        "step_timeout_ms": 30000, "native_tool_calls": true}}``

    Depends on:
        - ``RunContext.llm_client`` for plan + step generation
        - ``RunContext.tools`` for tool dispatch
        - ``RunContext.event_bus`` for plan/phase/step events
    """

    class Config(BaseModel):
        max_steps: int = 16
        step_timeout_ms: int = 30000
        native_tool_calls: bool = True

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _max_steps(self) -> int:
        max_steps = self.config.get("max_steps", self.cfg.max_steps)
        if isinstance(max_steps, int) and max_steps > 0:
            return max_steps
        return 16

    def _step_timeout_ms(self) -> int:
        timeout = self.config.get("step_timeout_ms", self.cfg.step_timeout_ms)
        if isinstance(timeout, int) and timeout > 0:
            return timeout
        return 30000

    def _native_tool_calls(self) -> bool:
        return bool(self.config.get("native_tool_calls", self.cfg.native_tool_calls))

    def _llm_enabled(self) -> bool:
        ctx = self.context
        return ctx.llm_client is not None

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Phase 1: Planning (shared between native and legacy)
    # ------------------------------------------------------------------

    async def _plan(self) -> list[dict[str, Any]]:
        """Phase 1: Create a plan."""
        ctx = self.context
        messages = [
            {"role": "system", "content": self._planning_prompt()},
            {"role": "user", "content": ctx.input_text},
        ]
        raw = await self.call_llm(messages=messages)
        # Empty-response repair
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

    # ------------------------------------------------------------------
    # Phase 2: Legacy execution (prompt-based JSON)
    # ------------------------------------------------------------------

    async def _execute_plan_legacy(self, plan: list[dict[str, Any]]) -> str:
        """Phase 2: Execute the plan step by step using prompt-based JSON."""
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

    # ------------------------------------------------------------------
    # Phase 2: Native tool-calling execution
    # ------------------------------------------------------------------

    async def _execute_plan_native(self, plan: list[dict[str, Any]]) -> str:
        """Phase 2: Execute the plan using native LLM tool_calls.

        The LLM receives the plan as context plus available tool schemas.
        It may emit tool_calls to execute steps; results are fed back
        into the conversation until a final answer is produced.
        """
        ctx = self.context
        llm_options = ctx.llm_options
        model = getattr(llm_options, "model", None) if llm_options else None
        temperature = getattr(llm_options, "temperature", None) if llm_options else None
        max_tokens = getattr(llm_options, "max_tokens", None) if llm_options else None
        max_steps = self._max_steps()
        timeout_s = self._step_timeout_ms() / 1000

        # Build tool schemas from registered tools.
        tools = self._build_tool_schemas()

        # Serialize the plan for the LLM context.
        plan_text = json.dumps(plan, ensure_ascii=False, indent=2)

        # Initialise conversation.
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self.compose_system_prompt(
                    "You are an executor for an agent runtime.\n"
                    "You have been given a plan. Execute it step by step.\n"
                    "Use the available tools when a step requires a tool call.\n"
                    "When all steps are complete, provide a final answer.\n"
                    "If a step fails, note the failure and continue with the remaining steps."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User input: {ctx.input_text}\n\n"
                    f"Plan to execute:\n{plan_text}\n\n"
                    "Please execute this plan using the available tools."
                ),
            },
        ]

        results: list[str] = []

        for step in range(max_steps):
            await self.emit("pattern.step_started", step=step)

            try:
                response = await asyncio.wait_for(
                    ctx.llm_client.generate(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools or None,
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Pattern step timed out after {self._step_timeout_ms()}ms at step {step}"
                ) from exc

            await self.emit("pattern.step_finished", step=step)

            # Empty-response repair.
            if not response.output_text and not response.tool_calls:
                repair = await self.repair_empty_response(
                    context=ctx,
                    messages=messages,
                    assistant_content=[],
                    stop_reason=response.stop_reason,
                    retries=0,
                )
                if repair is not None and repair.status == "repaired":
                    return repair.output

            # If the LLM emitted tool_calls, execute them and feed results back.
            if response.tool_calls:
                for tc in response.tool_calls:
                    tool_name = tc.name
                    # Map tool name back to tool_id (name usually == tool_id).
                    tool_id = tool_name
                    if tool_id not in ctx.tools:
                        for tid, t in ctx.tools.items():
                            desc = t.describe() if hasattr(t, "describe") else {}
                            if desc.get("name") == tool_name:
                                tool_id = tid
                                break

                    try:
                        result = await self.call_tool(tool_id, tc.arguments or {})
                        results.append(f"Step {step + 1}: {tool_id} completed")
                    except Exception as e:
                        result = f"Error: {e}"
                        results.append(f"Step {step + 1}: {tool_id} failed - {e}")

                    # Append assistant tool_call + tool_result to conversation.
                    messages.append(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": tc.id or "",
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(tc.arguments or {}),
                                    },
                                }
                            ],
                        }
                    )
                    messages.append(
                        self._make_tool_result_message(
                            tc.id or "",
                            tool_name,
                            result,
                        )
                    )
                continue  # Loop again for next step.

            # No tool calls — final answer.
            final = response.output_text or "\n".join(results) if results else "Plan executed"
            return final

        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        # Phase 1: Create plan (shared)
        plan = await self._plan()
        ctx.scratch["_plan"] = plan

        await self.emit("pattern.phase", phase="executing")
        await self.emit("pattern.plan_created", plan=plan)

        # Phase 2: Execute plan (native or legacy)
        if self._native_tool_calls():
            result = await self._execute_plan_native(plan)
        else:
            result = await self._execute_plan_legacy(plan)

        ctx.state["_runtime_last_output"] = result
        return result
