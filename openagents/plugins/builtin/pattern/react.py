"""Builtin ReAct pattern plugin.

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


class ReActPattern(TypedConfigPluginMixin, PatternPlugin):
    """ReAct pattern implementation with native tool-calling support.

    What:
        A Reason-Act loop that uses the LLM's native ``tools`` parameter.
        The LLM receives structured tool schemas and may emit one or more
        ``tool_calls`` in its response.  The pattern executes them, feeds
        the results back as standardized ``tool_result`` messages, and
        loops until a final answer is produced or the budget is hit.

        Backward-compat: set ``native_tool_calls: false`` to fall back
        to the legacy prompt-based JSON protocol.

    Usage:
        ``{"type": "react", "config": {"max_steps": 16,
        "step_timeout_ms": 30000, "native_tool_calls": true}}``

    Depends on:
        - ``RunContext.llm_client`` for step generation
        - ``RunContext.tools`` for tool dispatch
        - ``RunContext.event_bus`` for tool/llm/usage events
    """

    class Config(BaseModel):
        max_steps: int = 16
        step_timeout_ms: int = 30000
        native_tool_calls: bool = True

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()
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

    def _build_tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool schemas from registered tools."""
        ctx = self.context
        schemas: list[dict[str, Any]] = []
        for tool_id in sorted(ctx.tools.keys()):
            tool = ctx.tools[tool_id]
            desc = tool.describe() if hasattr(tool, "describe") else {}
            name = desc.get("name") or tool_id
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc.get("description", ""),
                        "parameters": desc.get("parameters", {"type": "object"}),
                    },
                }
            )
        return schemas

    def _make_tool_result_message(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> dict[str, Any]:
        """Build a standardized tool-result message for the LLM conversation.

        Returns the provider-native shape so the LLM can correlate the
        result with its earlier ``tool_calls``.
        """
        ctx = self.context
        provider = ""
        if ctx.llm_client is not None:
            provider = getattr(ctx.llm_client, "provider_name", "") or ""

        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

        # Anthropic uses a block-style user message with tool_result blocks.
        if provider == "anthropic":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content,
                    }
                ],
            }

        # Default / OpenAI-compatible: role="tool" with tool_call_id.
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": content,
        }

    # ------------------------------------------------------------------
    # Legacy fallback (prompt-based JSON)
    # ------------------------------------------------------------------

    def _legacy_system_prompt(self) -> str:
        return self.compose_system_prompt(
            "You are a strict planner for an agent runtime.\n"
            "Return only JSON with one of these shapes:\n"
            '{"type":"final","content":"..."}\n'
            '{"type":"continue"}\n'
            '{"type":"tool_call","tool":"<tool_id>","params":{...}}\n'
            "No markdown, no extra text."
        )

    def _legacy_user_prompt(self) -> str:
        ctx = self.context
        lines = [f"INPUT: {ctx.input_text}"]
        lines.append("AVAILABLE_TOOLS:")
        for tool_id in sorted(ctx.tools.keys()):
            tool = ctx.tools[tool_id]
            desc = tool.describe() if hasattr(tool, "describe") else {}
            lines.append(f"  - {tool_id}: {desc.get('description', '')}")
            params = desc.get("parameters", {})
            for pname, pinfo in (params.get("properties") or {}).items():
                req = " (required)" if pname in (params.get("required") or []) else ""
                lines.append(f"      {pname}: {pinfo.get('type', 'any')}{req}")
        lines.append("If no tool is needed, return final.")
        return "\n".join(lines)

    def _legacy_parse_action(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return {"type": "final", "content": raw}

    async def _legacy_react_step(self) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": self._legacy_system_prompt()},
            {"role": "user", "content": self._legacy_user_prompt()},
        ]
        ctx = self.context
        llm_options = ctx.llm_options
        raw = await self.call_llm(
            messages=messages,
            model=getattr(llm_options, "model", None) if llm_options else None,
            temperature=getattr(llm_options, "temperature", None) if llm_options else None,
            max_tokens=getattr(llm_options, "max_tokens", None) if llm_options else None,
        )
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
        return self._legacy_parse_action(raw)

    # ------------------------------------------------------------------
    # Native tool-calling loop
    # ------------------------------------------------------------------

    async def _native_react_step(self) -> dict[str, Any]:
        """One ReAct step using native function calling.

        Returns a dict so ``execute()`` can treat native and legacy steps
        uniformly.
        """
        ctx = self.context
        llm_options = ctx.llm_options
        model = getattr(llm_options, "model", None) if llm_options else None
        temperature = getattr(llm_options, "temperature", None) if llm_options else None
        max_tokens = getattr(llm_options, "max_tokens", None) if llm_options else None

        tools = self._build_tool_schemas()

        # First call: ask the LLM with tools available.
        response = await ctx.llm_client.generate(
            messages=self._messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools or None,
        )

        # Empty-response repair.
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

        # If the LLM emitted tool_calls, execute them and return a pseudo-action
        # so the outer loop handles the continue.
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_name = tc.name
                # Map tool name back to tool_id (name usually == tool_id).
                tool_id = tool_name
                if tool_id not in ctx.tools:
                    # Try to find by the tool's describe().name as well.
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

        # No tool calls — final answer.
        return {"type": "final", "content": response.output_text}

    async def react(self) -> dict[str, Any]:
        """Run one pattern step."""
        if self._native_tool_calls():
            return await self._native_react_step()
        return await self._legacy_react_step()

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self) -> Any:
        """Execute the complete ReAct loop."""
        self._inject_validation_correction()
        ctx = self.context

        # Followup short-circuit.
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
                        "When you have enough information, provide a final answer."
                    ),
                },
                {"role": "user", "content": ctx.input_text},
            ]
        else:
            self._messages = []

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
                    f"Unsupported pattern action type: '{action_type}'. Allowed: {sorted(allowed_action_types)}"
                )

            if action_type == "tool_call":
                # Legacy path only — native path handles tool calls inside _native_react_step.
                tool_id = action.get("tool") or action.get("tool_id")
                if not isinstance(tool_id, str) or not tool_id:
                    raise ValueError("tool_call action must include non-empty 'tool' or 'tool_id'")
                params = action.get("params", {}) or {}
                if not isinstance(params, dict):
                    raise ValueError("tool_call action 'params' must be an object")
                await self.call_tool(tool_id, params)
                continue

            if action_type == "final":
                content = action.get("content")
                ctx.state["_runtime_last_output"] = content
                return content

            # action_type == "continue" — loop again.
            continue

        raise RuntimeError(f"Pattern exceeded max_steps ({max_steps})")
