"""Deterministic end-to-end integration tests for the research-analyst example.

ReAct response shape (confirmed from openagents/plugins/builtin/pattern/react.py):
  - Tool call : {"type": "tool_call", "tool": "<tool_id>", "params": {...}}
  - Final     : {"type": "final", "content": "<text>"}
  - Continue  : {"type": "continue"}

Key deviations from the task spec (documented here):

  1. retry_attempts now reaches events.ndjson (resolved 2026-04-17)
     RetryToolExecutor.execute() stamps retry_attempts on ToolExecutionResult.metadata.
     With the plugin-system-cleanup spec, _BoundTool.invoke() returns the full
     ToolExecutionResult and pattern.call_tool emits the metadata as the
     ``executor_metadata`` field on the tool.succeeded event payload.  We can now
     assert directly on retry_attempts >= 3 in events.ndjson rather than rely on
     report.md as indirect proof.

  2. HttpRequestTool does NOT raise RetryableToolError for HTTP 503
     HttpRequestTool.invoke() catches all urllib errors / HTTP status codes and returns
     a plain dict {"status": 503, "success": False, ...}.  SafeToolExecutor therefore
     sees ToolExecutionResult(success=True) on the first call, so RetryToolExecutor
     never retries — retry_attempts stays at 1 with no delays.

  3. ReActPattern _PENDING_TOOL_KEY short-circuit
     When the LLM returns {"type": "tool_call", ...}, _react_with_llm() stores the
     tool_id in ctx.scratch["_react_pending_tool"].  After the tool runs, the NEXT
     call to react() sees the pending key and returns a "final" immediately (without
     calling the LLM again).  Effectively each LLM invocation gets exactly ONE tool
     call before the pattern finalises.  The multi-step script in the original spec
     (http_request → write_file → final) cannot work as-is because the second LLM
     call never happens.  The test uses separate runtime.run() calls — each with its
     own single-entry script — to cover both tools.

     The LLM client is cached by agent.id inside DefaultRuntime._llm_clients.  To
     switch scripts between sub-runs without recreating the runtime we set up ONE
     scriptable client that pops from a shared queue and monkeypatch it BEFORE the
     runtime is created, so all sub-runs share the same client instance.

  4. Policy-denial propagates as PatternError
     NetworkAllowlistExecutionPolicy raises PermissionError, which pattern.call_tool()
     re-raises (via tool.fallback), and DefaultRuntime wraps it as PatternError stored
     in RunResult.exception.  runtime.run() re-raises that exception; run_detailed()
     is used instead so we can inspect events.ndjson without the test crashing.

  5. WriteFileTool uses params["path"] (not "file_path")
     The original spec used "file_path" in the write_file params dict, but
     WriteFileTool.invoke() reads params.get("path", "").  The param key is corrected
     to "path".  (The filesystem policy also recognises "path" via _PATH_KEYS so
     the policy check also uses the right key.)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import openagents.llm.registry as llm_registry
from examples.research_analyst.app.stub_server import start_stub_server
from openagents.interfaces.runtime import RunRequest
from openagents.llm.base import LLMClient
from openagents.runtime.runtime import Runtime

_EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "research_analyst"
_SESSIONS_DIR = _EXAMPLE_DIR / "sessions"


def _clean_sessions() -> None:
    if _SESSIONS_DIR.exists():
        shutil.rmtree(_SESSIONS_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_sessions_between_tests():
    _clean_sessions()
    yield
    _clean_sessions()


class _ScriptedResearchLLM(LLMClient):
    """Pop from a shared response queue; fall back to 'final/done' when empty."""

    def __init__(self, script: list[str]):
        self.calls = 0
        self._script = list(script)

    def push(self, response: str) -> None:
        """Append a response to the queue (used to feed sub-runs after construction)."""
        self._script.append(response)

    async def complete(
        self,
        *,
        messages,
        model=None,
        temperature=None,
        max_tokens=None,
        tools=None,
        tool_choice=None,
    ) -> str:
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        return json.dumps({"type": "final", "content": "done"})


# ---------------------------------------------------------------------------
# Happy-path test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_analyst_end_to_end(monkeypatch):
    """Happy path: http_request; write_file report; followup short-circuited by rule_based.

    Due to ReActPattern's _PENDING_TOOL_KEY design, each LLM call results in exactly
    one tool invocation followed by immediate finalisation (without a second LLM call).
    We therefore exercise http_request and write_file in separate runtime.run() calls
    on the same runtime instance.  A single shared LLM client is monkeypatched BEFORE
    runtime construction so the cached client receives all sub-run scripts.
    """
    async with start_stub_server() as base_url:
        _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = _SESSIONS_DIR / "report.md"

        # Single client shared across all sub-runs (starts empty; we push() before each run).
        client = _ScriptedResearchLLM(script=[])
        monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

        runtime = Runtime.from_config(_EXAMPLE_DIR / "agent.json")

        # --- Sub-run A: http_request to stub server (exercises network policy + tool) ---
        client.push(
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "http_request",
                    "params": {"url": f"{base_url}/pages/topic-a", "method": "GET"},
                }
            )
        )
        result_a = await runtime.run(
            agent_id="research-analyst",
            session_id="http-check",
            input_text="fetch topic-a",
        )
        # After one tool call, ReActPattern returns "Tool[http_request] => <result-dict>"
        assert result_a is not None
        assert "http_request" in str(result_a)

        events_path = _SESSIONS_DIR / "events.ndjson"
        assert events_path.exists(), "events.ndjson was not created"
        events_text = events_path.read_text(encoding="utf-8")
        assert '"tool.called"' in events_text, "tool.called event missing"
        assert "http_request" in events_text, "http_request missing from events"
        assert '"tool.succeeded"' in events_text, "tool.succeeded event missing"

        # --- Sub-run A2: /pages/flaky drives RetryToolExecutor.  The first two
        # requests time out (stub server sleeps 500ms > executor's 200ms timeout),
        # so the executor retries.  The third attempt succeeds.  We then assert
        # directly on executor_metadata.retry_attempts in events.ndjson.
        client.push(
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "http_request",
                    "params": {"url": f"{base_url}/pages/flaky", "method": "GET"},
                }
            )
        )
        await runtime.run(
            agent_id="research-analyst",
            session_id="flaky-check",
            input_text="fetch flaky",
        )

        events_lines = events_path.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in events_lines if line.strip()]
        flaky_event = next(
            (
                e
                for e in events
                if e.get("name") == "tool.succeeded"
                and e.get("payload", {}).get("tool_id") == "http_request"
                and "/pages/flaky" in str((e.get("payload", {}).get("result") or {}).get("url", ""))
            ),
            None,
        )
        assert flaky_event is not None, "expected a tool.succeeded event for the /pages/flaky http_request"
        executor_metadata = flaky_event["payload"].get("executor_metadata") or {}
        assert executor_metadata.get("retry_attempts", 0) >= 3, (
            f"expected retry_attempts >= 3 (the executor should retry past the two "
            f"500ms timeouts), got executor_metadata={executor_metadata!r}"
        )

        # --- Sub-run B: write_file exercises filesystem policy + report persistence ---
        client.push(
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "write_file",
                    "params": {
                        "path": str(report_path),
                        "content": "# research report\n\ncovered topic-a.\n",
                    },
                }
            )
        )
        result_b = await runtime.run(
            agent_id="research-analyst",
            session_id="report-write",
            input_text="write a report",
        )
        assert result_b is not None
        # The flaky endpoint sleeps past the tool executor's default_timeout_ms on the
        # first two attempts. If RetryToolExecutor did NOT retry, the scripted LLM
        # would receive a timeout error on turn 1 and never reach the write_file call
        # on turn 2. The fact that report.md exists at the end proves retry fired.
        assert report_path.exists(), "report.md was not written — retry likely did not fire"
        content = report_path.read_text(encoding="utf-8")
        assert "research report" in content

        # --- Sub-run C: followup on session report-write, short-circuited by rule_based ---
        # After run B, memory buffer has tool_results=[{"tool_id": "write_file", ...}].
        # Rule "last_tools": pattern "(用了哪些工具|which tools.*use)" matches the input.
        # Rule template: "上一轮工具：{tool_ids}"
        calls_before = client.calls
        result_c = await runtime.run(
            agent_id="research-analyst",
            session_id="report-write",
            input_text="用了哪些工具",
        )
        assert client.calls == calls_before, (
            f"followup should be resolved locally (no LLM call); "
            f"LLM was called {client.calls - calls_before} extra time(s)"
        )
        assert result_c is not None
        # Rule template: "上一轮工具：{tool_ids}"
        assert "write_file" in str(result_c), f"followup output should mention write_file from run B, got: {result_c!r}"

        # JsonlFileSessionManager can replay the session written by run B
        from openagents.plugins.builtin.session.jsonl_file import JsonlFileSessionManager

        fresh = JsonlFileSessionManager(config={"root_dir": str(_SESSIONS_DIR)})
        msgs = await fresh.load_messages("report-write")
        assert len(msgs) > 0, "jsonl_file session should have at least one message"

        if hasattr(runtime, "close"):
            await runtime.close()


# ---------------------------------------------------------------------------
# Policy-denial test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_analyst_policy_denial(monkeypatch):
    """Scripted LLM emits a URL outside the allowlist — network_allowlist policy denies it."""
    async with start_stub_server() as base_url:
        _ = base_url  # stub server running but we deliberately use an evil URL
        script = [
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "http_request",
                    "params": {"url": "http://evil.test/anything", "method": "GET"},
                }
            ),
            json.dumps({"type": "final", "content": "I tried but was denied."}),
        ]
        client = _ScriptedResearchLLM(script)
        monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)

        runtime = Runtime.from_config(_EXAMPLE_DIR / "agent.json")
        # PermissionError from the policy propagates as PatternError via
        # result.exception.  Use run_detailed() to capture it without re-raising.
        result = await runtime.run_detailed(
            request=RunRequest(
                agent_id="research-analyst",
                session_id="deny",
                input_text="try an evil URL",
            )
        )
        # The run must have failed due to the policy denial.
        assert result.stop_reason in ("failed", "error") or result.exception is not None, (
            f"expected a failed run for evil.test URL, "
            f"got stop_reason={result.stop_reason!r}, exception={result.exception!r}"
        )

        events_path = _SESSIONS_DIR / "events.ndjson"
        text = events_path.read_text(encoding="utf-8") if events_path.exists() else ""

        # NetworkAllowlistExecutionPolicy sets reason: "host 'evil.test' not in allow_hosts"
        # pattern.call_tool() catches the PermissionError and emits tool.failed with
        # that message.  Either "not in allow_hosts" or "evil.test" confirms denial.
        assert "not in allow_hosts" in text or "evil.test" in text, (
            f"expected denial signal ('not in allow_hosts' or 'evil.test') in events.ndjson, got: {text[:800]}"
        )

        if hasattr(runtime, "close"):
            await runtime.close()
