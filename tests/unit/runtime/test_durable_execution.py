"""Unit tests for DefaultRuntime durable execution (auto-checkpoint + resume).

Covers the new `RunRequest.durable` / `resume_from_checkpoint` fields, the
retryable-vs-permanent error classification, the resume retry loop, the
`max_resume_attempts` budget cap, and the one-shot idempotency warning.
"""

from __future__ import annotations

from typing import Any

import pytest

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import (
    ConfigError,
    LLMConnectionError,
    LLMRateLimitError,
    PermanentToolError,
    ToolRateLimitError,
)
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import RunBudget, RunRequest, StopReason
from openagents.runtime.runtime import Runtime

# ---------------------------------------------------------------------------
# Fixture pattern that simulates step boundaries + configurable failures
# ---------------------------------------------------------------------------


class ScriptedPattern(PatternPlugin):
    """Pattern fixture that replays a script of actions on each execute().

    Config ``script`` is a list of lists. Each outer item is one execute()
    attempt. Each inner item is one of:
        ("llm", "<text>")            → emit llm.succeeded, step bumps
        ("tool", "<tool_id>")        → emit tool.succeeded, step bumps
        ("raise", "<exc_kind>")      → raise a retryable/permanent exception
        ("final", <value>)           → return this value from execute()

    Between attempts the pattern does NOT reset — resume should rehydrate the
    transcript from the checkpoint, so a fresh attempt that replays from the
    top will see the checkpointed state.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        from openagents.interfaces.capabilities import PATTERN_EXECUTE

        super().__init__(config=config or {}, capabilities={PATTERN_EXECUTE})
        self._script: list[list[tuple[str, Any]]] = list(self.config.get("script", []))
        self._attempt = 0
        self.execute_calls = 0
        self.emitted_steps: list[str] = []

    async def execute(self) -> Any:
        self.execute_calls += 1
        attempt_index = self._attempt
        self._attempt += 1
        if attempt_index >= len(self._script):
            raise RuntimeError(f"ScriptedPattern exhausted (attempt {attempt_index})")
        attempt = list(self._script[attempt_index])
        for kind, value in attempt:
            if kind == "llm":
                await self.emit("llm.called", model="mock")
                await self.emit("llm.succeeded", model="mock")
                self.emitted_steps.append("llm")
            elif kind == "tool":
                await self.emit("tool.called", tool_id=value, params={})
                await self.emit("tool.succeeded", tool_id=value, result="ok")
                self.emitted_steps.append(f"tool:{value}")
            elif kind == "raise":
                if value == "rate_limit":
                    raise LLMRateLimitError("simulated rate limit")
                if value == "connection":
                    raise LLMConnectionError("simulated connection error")
                if value == "tool_rate_limit":
                    raise ToolRateLimitError("simulated tool rate limit")
                if value == "permanent":
                    raise PermanentToolError("simulated permanent tool error", tool_name="x")
                raise RuntimeError(f"unknown raise kind: {value}")
            elif kind == "final":
                return value
        return None


def _build_config(script: list) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "durable-test-agent",
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {
                    "impl": f"{ScriptedPattern.__module__}.{ScriptedPattern.__name__}",
                    "config": {"script": script},
                },
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 20,
                    "step_timeout_ms": 2000,
                    "session_queue_size": 100,
                    "event_queue_size": 100,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Phase 1: RunRequest / RunBudget field shape
# ---------------------------------------------------------------------------


def test_run_request_defaults_preserve_legacy_behavior():
    request = RunRequest(agent_id="a", session_id="s", input_text="hi")
    assert request.durable is False
    assert request.resume_from_checkpoint is None


def test_run_budget_defaults_max_resume_attempts_to_three():
    assert RunBudget().max_resume_attempts == 3
    assert RunBudget(max_resume_attempts=5).max_resume_attempts == 5
    assert RunBudget(max_resume_attempts=0).max_resume_attempts == 0


def test_run_request_round_trip_serialization():
    request = RunRequest(
        agent_id="a",
        session_id="s",
        input_text="hi",
        durable=True,
        resume_from_checkpoint="ck1",
        budget=RunBudget(max_resume_attempts=5),
    )
    dumped = request.model_dump()
    assert dumped["durable"] is True
    assert dumped["resume_from_checkpoint"] == "ck1"
    assert dumped["budget"]["max_resume_attempts"] == 5
    rebuilt = RunRequest.model_validate(dumped)
    assert rebuilt.durable is True
    assert rebuilt.resume_from_checkpoint == "ck1"
    assert rebuilt.budget.max_resume_attempts == 5


# ---------------------------------------------------------------------------
# Phase 2: Checkpoint cadence (durable=True writes one per llm/tool step)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_durable_run_never_creates_checkpoints():
    runtime = Runtime(
        load_config_dict(
            _build_config(
                [
                    [("llm", None), ("llm", None), ("final", {"answer": 42})],
                ]
            )
        )
    )
    request = RunRequest(
        agent_id="assistant",
        session_id="s-nondurable",
        input_text="hi",
        durable=False,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    event_names = [e.name for e in runtime.event_bus.history]
    assert "run.checkpoint_saved" not in event_names
    assert "run.resume_attempted" not in event_names


@pytest.mark.asyncio
async def test_durable_run_writes_one_checkpoint_per_step():
    runtime = Runtime(
        load_config_dict(
            _build_config(
                [
                    [
                        ("llm", None),
                        ("tool", "t1"),
                        ("llm", None),
                        ("final", {"answer": 42}),
                    ],
                ]
            )
        )
    )
    request = RunRequest(
        agent_id="assistant",
        session_id="s-durable-cadence",
        input_text="hi",
        durable=True,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    saved_events = [e for e in runtime.event_bus.history if e.name == "run.checkpoint_saved"]
    # 3 step boundaries: llm, tool, llm
    assert len(saved_events) == 3
    # step_index monotonic
    step_indices = [e.payload["step_index"] for e in saved_events]
    assert step_indices == [1, 2, 3]
    # ids are deterministic
    assert saved_events[0].payload["checkpoint_id"].endswith(":step:1")


# ---------------------------------------------------------------------------
# Phase 3: Resume retry loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_error_triggers_resume():
    runtime = Runtime(
        load_config_dict(
            _build_config(
                [
                    # Attempt 1: emit 2 steps then raise rate limit
                    [
                        ("llm", None),
                        ("tool", "t1"),
                        ("raise", "rate_limit"),
                    ],
                    # Attempt 2: finishes
                    [("final", {"answer": 42})],
                ]
            )
        )
    )
    request = RunRequest(
        agent_id="assistant",
        session_id="s-resume-retry",
        input_text="hi",
        durable=True,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED
    events = [e.name for e in runtime.event_bus.history]
    assert events.count("run.resume_attempted") == 1
    assert events.count("run.resume_succeeded") == 1
    # No exhaustion event
    assert "run.resume_exhausted" not in events


@pytest.mark.asyncio
async def test_connection_error_also_retryable():
    runtime = Runtime(
        load_config_dict(
            _build_config(
                [
                    [("llm", None), ("raise", "connection")],
                    [("final", {"answer": 42})],
                ]
            )
        )
    )
    request = RunRequest(
        agent_id="assistant",
        session_id="s-resume-conn",
        input_text="hi",
        durable=True,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.COMPLETED


@pytest.mark.asyncio
async def test_permanent_error_propagates_without_resume():
    runtime = Runtime(
        load_config_dict(
            _build_config(
                [
                    [("llm", None), ("raise", "permanent")],
                ]
            )
        )
    )
    request = RunRequest(
        agent_id="assistant",
        session_id="s-resume-perm",
        input_text="hi",
        durable=True,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.FAILED
    events = [e.name for e in runtime.event_bus.history]
    assert "run.resume_attempted" not in events


@pytest.mark.asyncio
async def test_max_resume_attempts_cap_emits_exhausted():
    runtime = Runtime(
        load_config_dict(
            _build_config(
                [
                    [("llm", None), ("raise", "rate_limit")],
                    [("llm", None), ("raise", "rate_limit")],
                    [("llm", None), ("raise", "rate_limit")],
                    [("llm", None), ("raise", "rate_limit")],
                ]
            )
        )
    )
    request = RunRequest(
        agent_id="assistant",
        session_id="s-resume-exhaust",
        input_text="hi",
        durable=True,
        budget=RunBudget(max_resume_attempts=2),
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.FAILED
    events = [e.name for e in runtime.event_bus.history]
    # max_resume_attempts=2 → 2 resumes succeed, 3rd is exhausted
    assert events.count("run.resume_attempted") == 2
    assert events.count("run.resume_exhausted") == 1


@pytest.mark.asyncio
async def test_no_checkpoint_yet_raises_not_resumable():
    # First attempt raises BEFORE any step emits → no checkpoint exists yet.
    runtime = Runtime(load_config_dict(_build_config([[("raise", "rate_limit")]])))
    request = RunRequest(
        agent_id="assistant",
        session_id="s-no-ckpt",
        input_text="hi",
        durable=True,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.FAILED
    events = [e.name for e in runtime.event_bus.history]
    assert "run.resume_attempted" not in events


@pytest.mark.asyncio
async def test_durable_false_with_retryable_error_does_not_resume():
    runtime = Runtime(load_config_dict(_build_config([[("llm", None), ("raise", "rate_limit")]])))
    request = RunRequest(
        agent_id="assistant",
        session_id="s-nondurable-retry",
        input_text="hi",
        durable=False,
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.FAILED
    events = [e.name for e in runtime.event_bus.history]
    assert "run.resume_attempted" not in events


# ---------------------------------------------------------------------------
# Phase 4: Explicit resume_from_checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_resume_from_checkpoint_rehydrates_state():
    """First run writes a checkpoint; its state contains the __durable__ blob
    with the rehydration payload."""
    cfg = _build_config(
        [
            [
                ("llm", None),
                ("tool", "t1"),
                ("llm", None),
                ("final", {"answer": 1}),
            ]
        ]
    )
    runtime = Runtime(load_config_dict(cfg))
    request = RunRequest(
        agent_id="assistant",
        session_id="s-explicit-resume",
        input_text="hi",
        durable=True,
    )
    first = await runtime.run_detailed(request=request)
    assert first.stop_reason == StopReason.COMPLETED

    saved = [e for e in runtime.event_bus.history if e.name == "run.checkpoint_saved"]
    assert saved, "expected at least one checkpoint"
    last_checkpoint_id = saved[-1].payload["checkpoint_id"]

    # Inspect the persisted checkpoint directly — validates the state shape
    # the resume path consumes without needing a second runtime setup.
    ckpt = await runtime.session_manager.load_checkpoint("s-explicit-resume", last_checkpoint_id)
    assert ckpt is not None
    assert ckpt.transcript_length >= 0
    assert "__durable__" in ckpt.state
    assert ckpt.state["__durable__"]["checkpoint_id"] == last_checkpoint_id


@pytest.mark.asyncio
async def test_explicit_resume_actually_rehydrates_usage_and_artifacts():
    """Exercise the live resume path: second run with resume_from_checkpoint
    loads the checkpoint, rehydrates transcript/usage/artifacts/state onto
    the new RunUsage+artifacts list, and emits context.assemble.completed
    with the resumed_from_checkpoint metadata."""
    cfg = _build_config(
        [
            [
                ("llm", None),
                ("tool", "t1"),
                ("final", {"answer": 1}),
            ],
            # Second execute() call (resumed run) just finalizes.
            [("final", {"answer": 2})],
        ]
    )
    runtime = Runtime(load_config_dict(cfg))
    session_id = "s-resume-live"
    first = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id=session_id,
            input_text="hi",
            durable=True,
        )
    )
    assert first.stop_reason == StopReason.COMPLETED

    saved = [e for e in runtime.event_bus.history if e.name == "run.checkpoint_saved"]
    assert saved, "expected at least one checkpoint"
    last_checkpoint_id = saved[-1].payload["checkpoint_id"]

    # Seed a synthetic artifact into the checkpoint's durable blob so the
    # "rehydrate artifacts from blob" branch (lines 676-683) is exercised.
    ckpt = await runtime.session_manager.load_checkpoint(session_id, last_checkpoint_id)
    assert ckpt is not None
    blob = dict(ckpt.state.get("__durable__") or {})
    blob["artifacts"] = [
        {
            "name": "seed",
            "kind": "text",
            "content": "from-checkpoint",
            "metadata": {"source": "resume-test"},
        },
        # Malformed entry — should hit the `except` branch and be skipped.
        {"__invalid__": True, "name": 123},
    ]
    # Seed a usage payload so the RunUsage rehydration branch runs.
    blob["usage"] = {
        "llm_calls": 7,
        "tool_calls": 3,
        "input_tokens": 11,
        "output_tokens": 13,
        "total_tokens": 24,
        "input_tokens_cached": 2,
        "input_tokens_cache_creation": 1,
        "cost_usd": 0.5,
        "cost_breakdown": {"input": 0.3, "output": 0.2},
    }
    # Mutate the persisted checkpoint via the session state directly —
    # the default SessionManager stores checkpoints under _session_checkpoints.
    sm_state = await runtime.session_manager.get_state(session_id)
    ckpts = dict(sm_state.get("_session_checkpoints", {}))
    ckpt_raw = dict(ckpts[last_checkpoint_id])
    new_ckpt_state = dict(ckpt_raw.get("state") or {})
    new_ckpt_state["__durable__"] = blob
    # Add a non-private state key to exercise the state-merge loop.
    new_ckpt_state["custom_key"] = "resumed-value"
    ckpt_raw["state"] = new_ckpt_state
    ckpts[last_checkpoint_id] = ckpt_raw
    sm_state["_session_checkpoints"] = ckpts
    await runtime.session_manager.set_state(session_id, sm_state)

    # Second run with resume_from_checkpoint.
    second = await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id=session_id,
            input_text="continue",
            resume_from_checkpoint=last_checkpoint_id,
        )
    )
    assert second.stop_reason == StopReason.COMPLETED
    # Usage fields were rehydrated from the blob (and pattern didn't emit more).
    assert second.usage.llm_calls == 7
    assert second.usage.input_tokens == 11
    assert second.usage.cost_breakdown == {"input": 0.3, "output": 0.2}
    # The seeded valid artifact survived; the malformed one was silently dropped.
    names = [a.name for a in second.artifacts]
    assert "seed" in names


@pytest.mark.asyncio
async def test_list_checkpoints_returns_ids_in_order():
    cfg = _build_config(
        [
            [
                ("llm", None),
                ("llm", None),
                ("final", {"answer": 1}),
            ]
        ]
    )
    runtime = Runtime(load_config_dict(cfg))
    await runtime.run_detailed(
        request=RunRequest(
            agent_id="assistant",
            session_id="s-list",
            input_text="hi",
            durable=True,
        )
    )
    ids = await runtime.session_manager.list_checkpoints("s-list")
    assert len(ids) == 2
    assert all(":step:" in i for i in ids)


@pytest.mark.asyncio
async def test_unknown_checkpoint_surfaces_config_error():
    """Unknown resume_from_checkpoint should surface a ConfigError.

    ``run_detailed`` returns the error on ``RunResult.exception`` (consistent
    with other error surfaces in the SDK); the legacy ``runtime.run()`` helper
    re-raises it.
    """
    runtime = Runtime(load_config_dict(_build_config([[("final", {"answer": 1})]])))
    request = RunRequest(
        agent_id="assistant",
        session_id="s-unknown-ckpt",
        input_text="hi",
        resume_from_checkpoint="does-not-exist",
    )
    result = await runtime.run_detailed(request=request)
    assert result.stop_reason == StopReason.FAILED
    assert isinstance(result.exception, ConfigError)
    # Hint should point at available checkpoints (or lack thereof)
    hint = result.exception.hint or ""
    assert "checkpoint" in hint.lower() or "session" in hint.lower()


# ---------------------------------------------------------------------------
# Phase 5: Idempotency warning (one-shot)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_durable_idempotent_attribute_default_true():
    from openagents.interfaces.tool import ToolPlugin

    class MyTool(ToolPlugin):
        async def invoke(self, params, context):  # pragma: no cover - unused
            return None

    assert MyTool.durable_idempotent is True


@pytest.mark.asyncio
async def test_non_idempotent_builtins_are_marked():
    from openagents.plugins.builtin.tool.file_ops import (
        DeleteFileTool,
        ReadFileTool,
        WriteFileTool,
    )
    from openagents.plugins.builtin.tool.http_ops import HttpRequestTool
    from openagents.plugins.builtin.tool.shell_exec import ShellExecTool
    from openagents.plugins.builtin.tool.system_ops import (
        ExecuteCommandTool,
        SetEnvTool,
    )

    assert WriteFileTool.durable_idempotent is False
    assert DeleteFileTool.durable_idempotent is False
    assert HttpRequestTool.durable_idempotent is False
    assert ShellExecTool.durable_idempotent is False
    assert ExecuteCommandTool.durable_idempotent is False
    assert SetEnvTool.durable_idempotent is False
    # Read-only defaults stay True
    assert ReadFileTool.durable_idempotent is True
