"""Reflexion pattern: execute with self-reflection on failures.

Uses native LLM function-calling (``tools`` / ``tool_choice``) when the
provider supports it, falling back to prompt-based JSON control when
``native_tool_calls: false`` is set in config.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel

from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin

logger = logging.getLogger(__name__)


class ReflexionPattern(TypedConfigPluginMixin, PatternPlugin):
    """Reflexion pattern: execute, reflect on results, retry if needed.

    What:
        After each tool result the LLM reflects on whether the task is
        complete; if not, it adjusts approach and retries up to
        ``max_retries`` times. Useful for tasks where the first
        attempt is often wrong but recoverable.

        Native tool-calling mode lets the LLM use provider-native
        ``tool_calls`` instead of prompt-based JSON.  The conversation
        loop naturally handles reflection because the LLM sees tool
        results and decides whether to continue or finish.

    Usage:
        ``{"type": "reflexion", "config": {"max_steps": 16,
        "max_retries": 2, "step_timeout_ms": 30000,
        "native_tool_calls": true}}``

    Depends on:
        - ``RunContext.llm_client`` for execution + reflection
        - ``RunContext.tools`` for tool dispatch
        - ``RunContext.event_bus`` for tool/llm/usage events
    """

    class Config(BaseModel):
        max_steps: int = 16
        step_timeout_ms: int = 30000
        max_retries: int = 2
        native_tool_calls: bool = True

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()
        self._max_retries = self.cfg.max_retries
        self._messages: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Helpers
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

    # ------------------------------------------------------------------
    # Legacy fallback (prompt-based JSON)
    # ------------------------------------------------------------------

    def _reflection_prompt(self) -> str:
        ctx = self.context
        history = ctx.memory_view.get("history", [])
        tool_results = ctx.tool_results

        history_text = self._format_history(history)

        results_text = ""
        if tool_results:
            results = []
            for tr in tool_results[-2:]:
                tool_id = tr.get("tool_id", "unknown")
                result = tr.get("result", tr.get("error", "error"))
                results.append(f"{tool_id}: {result}")
            results_text = f"Recent tool results: {', '.join(results)}\n"

        return self.compose_system_prompt(
            f"You are reflecting on the agent's recent actions.\n"
            f"CONVERSATION_HISTORY:\n{history_text}\n"
            f"{results_text}"
            f"Current input: {ctx.input_text}\n"
            "Determine if the task is complete or needs retry.\n"
            "Return JSON:\n"
            '{"type":"final","content":"result"} if complete\n'
            '{"type":"retry","reason":"why","adjusted_params":{...}} to retry\n'
            '{"type":"continue"} to do more steps\n'
            "No markdown."
        )

    def _action_prompt(self) -> str:
        ctx = self.context
        tool_ids = sorted(ctx.tools.keys())
        history = ctx.memory_view.get("history", [])
        history_text = self._format_history(history)

        return self.compose_system_prompt(
            f"Input: {ctx.input_text}\n"
            f"CONVERSATION_HISTORY:\n{history_text}\n"
            f"Available tools: {', '.join(tool_ids)}\n"
            "Return JSON:\n"
            '{"type":"tool_call","tool":"id","params":{...}}\n'
            '{"type":"final","content":"..."}\n'
            '{"type":"continue"}\n'
            "No markdown."
        )

    def _parse_llm_response(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"type": "final", "content": raw}

    async def _legacy_react(self) -> dict[str, Any]:
        """Single step with reflection (legacy prompt-based)."""
        ctx = self.context
        # Check if we should reflect on recent results
        if ctx.tool_results:
            # Reflect on last tool result
            messages = [
                {"role": "system", "content": self._reflection_prompt()},
                {"role": "user", "content": "Reflect on the previous action and determine next step."},
            ]
            try:
                raw = await self.call_llm(messages=messages)
                reflection = self._parse_llm_response(raw)

                action_type = reflection.get("type")
                if action_type == "final":
                    return {"type": "final", "content": reflection.get("content", "")}
                if action_type == "retry":
                    # Retry with adjusted approach
                    adjusted = reflection.get("adjusted_params", {})
                    tool_id = adjusted.get("tool")
                    params = adjusted.get("params", {})
                    if tool_id:
                        return {"type": "tool_call", "tool": tool_id, "params": params}
            except Exception:
                logger.debug("Failed to parse reflection response, falling through to normal action", exc_info=True)

        # Normal action selection
        if self._llm_enabled():
            messages = [
                {"role": "system", "content": self._action_prompt()},
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
            return self._parse_llm_response(raw)

        # No LLM, just continue
        return {"type": "continue"}

    # ------------------------------------------------------------------
    # Native tool-calling loop
    # ------------------------------------------------------------------

    async def _native_react_step(self) -> dict[str, Any]:
        """One Reflexion step using native function calling.

        Returns a dict so ``execute()`` can treat native and legacy steps
        uniformly.
        """
        ctx = self.context
        llm_options = ctx.llm_options
        model = getattr(llm_options, "model", None) if llm_options else None
        temperature = getattr(llm_options, "temperature", None) if llm_options else None
        max_tokens = getattr(llm_options, "max_tokens", None) if llm_options else None

        tools = self._build_tool_schemas()

        response = await ctx.llm_client.generate(
            messages=self._messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools or None,
        )

        # Empty-response repair
        if not response.output_text and not response.tool_calls:
            repair = await self.repair_empty_response(
                context=ctx,
                messages=self._messages,
                assistant_content=[],
                stop_reason=response.stop_reason,
                retries=0,
            )
            if repair is not None and repair.status == "repaired":
                return {"type": "final", "content": repair.output}

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

                result = await self.call_tool(tool_id, tc.arguments or {})

                # Append assistant tool_call + user tool_result to conversation.
                self._messages.append(
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
                self._messages.append(
                    self._make_tool_result_message(
                        tc.id or "",
                        tool_name,
                        result,
                    )
                )
            return {"type": "continue"}

        # No tool calls -- final answer.
        return {"type": "final", "content": response.output_text}

    async def react(self) -> dict[str, Any]:
        """Run one pattern step."""
        if self._native_tool_calls():
            return await self._native_react_step()
        return await self._legacy_react()

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self) -> Any:
        """Execute with reflection after each step."""
        self._inject_validation_correction()
        ctx = self.context

        # Followup short-circuit: allow a resolver to answer locally and skip
        # the reflect/act loop entirely.
        resolution = await self.resolve_followup(context=ctx)
        if resolution is not None and resolution.status == "resolved":
            if ctx.state is not None:
                ctx.state["_runtime_last_output"] = resolution.output
                ctx.state["resolved_by"] = "resolve_followup"
            return resolution.output

        # Initialise conversation for native mode.
        if self._native_tool_calls():
            self._messages = [
                {
                    "role": "system",
                    "content": self.compose_system_prompt(
                        "You are a helpful assistant with access to tools. "
                        "Use the available tools when needed. "
                        "After receiving tool results, reflect on whether the task "
                        "is complete. If complete, provide a final answer. "
                        "If not, continue using tools."
                    ),
                },
                {"role": "user", "content": ctx.input_text},
            ]
        else:
            self._messages = []

        max_steps = self._max_steps()
        retries = 0
        timeout_s = self._step_timeout_ms() / 1000

        for step in range(max_steps):
            await self.emit("pattern.step_started", step=step)

            try:
                action = await asyncio.wait_for(self.react(), timeout=timeout_s)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Pattern step timed out after {self._step_timeout_ms()}ms at step {step}"
                ) from exc

            await self.emit("pattern.step_finished", step=step, action=action)

            if not isinstance(action, dict):
                raise TypeError("Pattern action must be dict")

            action_type = action.get("type")

            if action_type == "tool_call":
                tool_id = action.get("tool") or action.get("tool_id")
                params = action.get("params", {})
                if not tool_id:
                    raise ValueError("tool_call must include 'tool'")
                await self.call_tool(tool_id, params)
                continue

            if action_type == "final":
                content = action.get("content", "")
                ctx.state["_runtime_last_output"] = content
                return content

            # continue or retry - loop continues
            if action_type == "retry":
                retries += 1
                if retries >= self._max_retries:
                    return f"Max retries ({self._max_retries}) reached"

        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")
