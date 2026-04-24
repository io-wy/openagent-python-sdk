"""Multi-agent demo with the mock LLM provider.

Runs entirely offline (no API key needed) and demonstrates both
``agent_router.delegate`` (Orchestrator pattern) and ``agent_router.transfer``
(Handoff pattern) at the Python level. Use ``run_demo_real.py`` to see the
LLM-driven flow via tool calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure the repo root is on sys.path so examples.multi_agent.plugins resolves
# when this file is launched directly (``python examples/multi_agent/run_demo_mock.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from openagents.interfaces.agent_router import HandoffSignal  # noqa: E402
from openagents.runtime.runtime import Runtime  # noqa: E402


def _banner(title: str) -> None:
    bar = "-" * 72
    print(f"\n{bar}\n{title}\n{bar}")


def _build_ctx(runtime: Runtime, *, run_id: str, session_id: str) -> MagicMock:
    """Minimal RunContext-like stand-in for demonstrating router calls directly."""
    ctx = MagicMock()
    ctx.run_id = run_id
    ctx.session_id = session_id
    ctx.deps = None
    ctx.event_bus = runtime.event_bus
    ctx.agent_router = runtime._runtime._agent_router
    return ctx


async def demo_delegate(runtime: Runtime) -> None:
    """Orchestrator pattern — await specialist's answer, then continue."""
    _banner("Scenario 1: Delegate (await sub-agent)")

    router = runtime._runtime._agent_router
    assert router is not None, "multi_agent.enabled must be true in agent_mock.json"

    ctx = _build_ctx(runtime, run_id="demo-orchestrator-run", session_id="demo-sess-1")
    result = await router.delegate(
        "specialist",
        "What is the capital of France?",
        ctx,
        session_isolation="isolated",
    )
    print("  parent_run_id:    demo-orchestrator-run")
    print(f"  child run_id:     {result.run_id}")
    print(f"  stop_reason:      {result.stop_reason}")
    print(f"  final_output:     {result.final_output!r}")


async def demo_transfer(runtime: Runtime) -> None:
    """Handoff pattern — transfer() raises HandoffSignal with the child's result."""
    _banner("Scenario 2: Transfer (permanent handoff)")

    router = runtime._runtime._agent_router
    ctx = _build_ctx(runtime, run_id="demo-triage-run", session_id="demo-sess-2")

    try:
        await router.transfer(
            "billing_agent",
            "Please refund order #12345",
            ctx,
            session_isolation="isolated",
        )
    except HandoffSignal as sig:
        print("  HandoffSignal raised (as expected):")
        print(f"    child run_id:   {sig.result.run_id}")
        print(f"    stop_reason:    {sig.result.stop_reason}")
        print(f"    final_output:   {sig.result.final_output!r}")
        print("  Note: when transfer() is invoked inside a real run,")
        print("        DefaultRuntime catches the signal and ends the parent run")
        print("        with the child's final_output and metadata['handoff_from'].")


async def demo_via_tool_call(runtime: Runtime) -> None:
    """Tool-driven delegation through a normal ``runtime.run()`` call.

    The mock LLM turns ``/tool <tool_id> <query>`` into a tool call. The
    orchestrator's ReAct pattern executes ``delegate_to_specialist`` which calls
    the router. max_steps=2 guarantees the demo terminates cleanly even though
    the mock LLM keeps emitting tool calls.
    """
    _banner("Scenario 3: Tool-driven delegation through runtime.run()")

    try:
        out = await runtime.run(
            agent_id="orchestrator",
            session_id="demo-sess-3",
            input_text="/tool delegate_to_specialist the mass of the Earth",
        )
        print(f"  orchestrator final output: {out!r}")
    except RuntimeError as exc:
        # MaxStepsExceeded is expected because the mock always emits tool_call.
        print(f"  (expected bounded failure) {exc}")

    # Depth is now carried on child RunRequest.metadata rather than any
    # router-side dict, so there's nothing to print here — the absence of
    # per-run state is the point.


async def demo_shared_session(runtime: Runtime) -> None:
    """Shared isolation — child reuses parent session_id; no deadlock."""
    _banner("Scenario 4: Shared isolation (child reuses parent session)")

    router = runtime._runtime._agent_router
    ctx = _build_ctx(runtime, run_id="demo-shared-run", session_id="demo-shared-sess")

    captured: dict = {}
    original = router._run_fn

    async def capture(*, request):
        captured["session_id"] = request.session_id
        return await original(request=request)

    router._run_fn = capture
    try:
        await router.delegate(
            "specialist",
            "question routed via shared session",
            ctx,
            session_isolation="shared",
        )
    finally:
        router._run_fn = original

    print("  parent session_id:  demo-shared-sess")
    print(f"  child session_id:   {captured['session_id']}  (same as parent)")


async def demo_forked_session(runtime: Runtime) -> None:
    """Forked isolation — parent history is snapshot-copied; writes diverge."""
    _banner("Scenario 5: Forked isolation (history snapshot copy)")

    router = runtime._runtime._agent_router
    await runtime.session_manager.append_message(
        "demo-forked-sess", {"role": "user", "content": "earlier parent message"}
    )
    ctx = _build_ctx(runtime, run_id="demo-forked-run", session_id="demo-forked-sess")

    captured: dict = {}
    original = router._run_fn

    async def capture(*, request):
        captured["session_id"] = request.session_id
        captured["messages"] = await runtime.session_manager.load_messages(request.session_id)
        return await original(request=request)

    router._run_fn = capture
    try:
        await router.delegate(
            "specialist",
            "continue from parent context",
            ctx,
            session_isolation="forked",
        )
    finally:
        router._run_fn = original

    print("  parent session_id:  demo-forked-sess")
    print(f"  child session_id:   {captured['session_id']}")
    print(f"  child sees msgs:    {[m['content'] for m in captured['messages']]}")


async def main() -> None:
    config_path = Path(__file__).parent / "agent_mock.json"
    runtime = Runtime.from_config(config_path)

    print("multi_agent config:", runtime._config.multi_agent)
    print("agent_router wired:", runtime._runtime._agent_router is not None)

    await demo_delegate(runtime)
    await demo_transfer(runtime)
    await demo_via_tool_call(runtime)
    await demo_shared_session(runtime)
    await demo_forked_session(runtime)

    await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
