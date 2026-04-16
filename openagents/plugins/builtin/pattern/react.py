"""Builtin ReAct pattern plugin."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openagents.interfaces.capabilities import PATTERN_EXECUTE, PATTERN_REACT
from openagents.interfaces.pattern import PatternPlugin


class ReActPattern(PatternPlugin):
    """ReAct pattern implementation."""

    _PENDING_TOOL_KEY = "_react_pending_tool"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={PATTERN_EXECUTE, PATTERN_REACT})

    # Default implementations - can be overridden

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

    def _tool_prefix(self) -> str:
        return str(self.config.get("tool_prefix", "/tool")).strip() or "/tool"

    def _echo_prefix(self) -> str:
        return str(self.config.get("echo_prefix", "Echo")).strip() or "Echo"

    def _max_steps(self) -> int:
        max_steps = self.config.get("max_steps", 16)
        if isinstance(max_steps, int) and max_steps > 0:
            return max_steps
        return 16

    def _step_timeout_ms(self) -> int:
        timeout = self.config.get("step_timeout_ms", 30000)
        if isinstance(timeout, int) and timeout > 0:
            return timeout
        return 30000

    def _format_tool_result(self, tool_id: str, result: Any) -> str:
        return f"Tool[{tool_id}] => {result}"

    def _llm_enabled(self) -> bool:
        ctx = self.context
        return ctx.llm_client is not None

    def _llm_system_prompt(self) -> str:
        return self.compose_system_prompt(
            "You are a strict planner for an agent runtime.\n"
            "Return only JSON with one of these shapes:\n"
            '{"type":"final","content":"..."}\n'
            '{"type":"continue"}\n'
            '{"type":"tool_call","tool":"<tool_id>","params":{...}}\n'
            "No markdown, no extra text."
        )

    def _format_history(self, history: list) -> str:
        """Format history for LLM prompt."""
        if not history:
            return "(no conversation history)"

        lines = []
        for item in history:
            if isinstance(item, dict):
                user_msg = item.get("input", "")
                assistant_msg = item.get("output", "")
                if user_msg:
                    lines.append(f"User: {user_msg}")
                if assistant_msg:
                    lines.append(f"Assistant: {assistant_msg}")
            elif isinstance(item, str):
                lines.append(item)

        return "\n".join(lines) if lines else "(no conversation history)"

    def _format_tools_description(self) -> str:
        """Format tool descriptions for LLM prompt."""
        ctx = self.context
        lines = []
        for tool_id in sorted(ctx.tools.keys()):
            tool = ctx.tools[tool_id]
            desc = tool.describe() if hasattr(tool, "describe") else {}
            description = desc.get("description", "")
            params_schema = desc.get("parameters", {})
            props = params_schema.get("properties", {})
            required = params_schema.get("required", [])

            param_parts = []
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                req_marker = " (required)" if pname in required else ""
                param_parts.append(f"    {pname}: {ptype}{req_marker} — {pdesc}")

            tool_line = f"  - {tool_id}: {description}" if description else f"  - {tool_id}"
            lines.append(tool_line)
            if param_parts:
                lines.extend(param_parts)

        return "\n".join(lines) if lines else "  (none)"

    def _llm_user_prompt(self) -> str:
        ctx = self.context
        history = ctx.memory_view.get("history")
        if not isinstance(history, list):
            history = []

        history_text = self._format_history(history)
        tools_text = self._format_tools_description()
        return (
            f"INPUT:{ctx.input_text}\n"
            f"CONVERSATION_HISTORY:\n{history_text}\n"
            f"AVAILABLE_TOOLS:\n{tools_text}\n"
            "Prefer tool_call when user explicitly asks for tool usage. "
            "params must match the tool's parameter schema.\n"
            "If no tool is needed, return final."
        )

    def _parse_llm_action(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # Soft fallback: try to parse first JSON object block.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            snippet = raw[start : end + 1]
            try:
                data = json.loads(snippet)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        return {"type": "final", "content": raw}

    async def _react_with_llm(self) -> dict[str, Any]:
        ctx = self.context
        messages = [
            {"role": "system", "content": self._llm_system_prompt()},
            {"role": "user", "content": self._llm_user_prompt()},
        ]
        llm_options = ctx.llm_options
        model = getattr(llm_options, "model", None) if llm_options else None
        temperature = getattr(llm_options, "temperature", None) if llm_options else None
        max_tokens = getattr(llm_options, "max_tokens", None) if llm_options else None
        raw = await self.call_llm(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        action = self._parse_llm_action(raw)
        if action.get("type") == "tool_call":
            tool_id = action.get("tool") or action.get("tool_id")
            if isinstance(tool_id, str) and tool_id.strip():
                ctx.scratch[self._PENDING_TOOL_KEY] = tool_id.strip()
        return action

    async def react(self) -> dict[str, Any]:
        """Run one pattern step."""
        ctx = self.context
        pending_tool = ctx.scratch.get(self._PENDING_TOOL_KEY)
        if isinstance(pending_tool, str):
            ctx.scratch.pop(self._PENDING_TOOL_KEY, None)
            latest = ctx.tool_results[-1]["result"] if ctx.tool_results else None
            return {"type": "final", "content": self._format_tool_result(pending_tool, latest)}

        if self._llm_enabled():
            return await self._react_with_llm()

        raw_input = (ctx.input_text or "").strip()
        prefix = self._tool_prefix()
        if raw_input.startswith(prefix):
            rest = raw_input[len(prefix) :].strip()
            if not rest:
                return {
                    "type": "final",
                    "content": f"Usage: {prefix} <tool_id> <query>",
                }
            parts = rest.split(maxsplit=1)
            tool_id = parts[0].strip()
            query = parts[1].strip() if len(parts) == 2 else ""
            ctx.scratch[self._PENDING_TOOL_KEY] = tool_id
            return {
                "type": "tool_call",
                "tool": tool_id,
                "params": {"query": query},
            }

        history = ctx.memory_view.get("history")
        if not isinstance(history, list):
            history = []

        history_count = len(history)
        history_lines = []
        for item in history:
            if isinstance(item, dict):
                user_msg = item.get("input", "")
                assistant_msg = item.get("output", "")
                if user_msg:
                    history_lines.append(f"User: {user_msg}")
                if assistant_msg:
                    history_lines.append(f"Assistant: {assistant_msg}")
        history_text = "\n".join(history_lines) if history_lines else "(no conversation history)"

        return {
            "type": "final",
            "content": f"{self._echo_prefix()}: {raw_input}\n\n[Conversation History ({history_count} items)]:\n{history_text}",
        }

    async def execute(self) -> Any:
        """Execute the complete ReAct loop."""
        self._inject_validation_correction()
        ctx = self.context
        allowed_action_types = {"tool_call", "final", "continue"}
        max_steps = self._max_steps()
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
                raise TypeError(f"Pattern action must be dict, got {type(action).__name__}")

            action_type = action.get("type")
            if not isinstance(action_type, str) or not action_type.strip():
                raise ValueError("Pattern action must include a non-empty string 'type'")
            if action_type not in allowed_action_types:
                raise ValueError(
                    f"Unsupported pattern action type: '{action_type}'. "
                    f"Allowed: {sorted(allowed_action_types)}"
                )

            if action_type == "tool_call":
                tool_id = action.get("tool") or action.get("tool_id")
                if not isinstance(tool_id, str) or not tool_id:
                    raise ValueError("tool_call action must include non-empty 'tool' or 'tool_id'")
                params = action.get("params", {})
                if params is None:
                    params = {}
                if not isinstance(params, dict):
                    raise ValueError("tool_call action 'params' must be an object")
                await self.call_tool(tool_id, params)
                continue

            if action_type == "final":
                content = action.get("content")
                ctx.state["_runtime_last_output"] = content
                return content

            # action_type == "continue"
            continue

        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")
