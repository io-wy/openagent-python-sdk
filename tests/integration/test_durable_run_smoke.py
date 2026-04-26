"""Integration smoke for durable execution.

Exercises the full durable-run path against a real jsonl session backend,
then cross-process resumes the run in a freshly-constructed ``Runtime`` that
loads the checkpoint from disk — proving the checkpoint blob is complete
enough to recover state without in-memory coupling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import LLMRateLimitError
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.runtime import RunBudget, RunRequest, StopReason
from openagents.runtime.runtime import Runtime


class _DurableScriptedPattern(PatternPlugin):
    """Integration-test fixture pattern.

    First execute() attempt emits 2 llm step events then raises
    LLMRateLimitError. Second attempt returns a final value. Intended to be
    resumed inside the same runtime under durable=True.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.calls = 0

    async def execute(self) -> Any:
        self.calls += 1
        if self.calls == 1:
            await self.emit("llm.called", model="mock")
            await self.emit("llm.succeeded", model="mock")
            await self.emit("llm.called", model="mock")
            await self.emit("llm.succeeded", model="mock")
            raise LLMRateLimitError("simulated upstream 429")
        # Second attempt: final output
        return {"answer": "restored"}


class _AlwaysFailingPattern(PatternPlugin):
    """Every execute() raises LLMRateLimitError after emitting one step."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.calls = 0

    async def execute(self) -> Any:
        self.calls += 1
        await self.emit("llm.called", model="mock")
        await self.emit("llm.succeeded", model="mock")
        raise LLMRateLimitError(f"always failing attempt {self.calls}")


def _make_jsonl_config(root_dir: Path) -> dict:
    return {
        "version": "1.0",
        "session": {
            "type": "jsonl_file",
            "config": {"root_dir": str(root_dir)},
        },
        "agents": [
            {
                "id": "resumable",
                "name": "durable-integration-agent",
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {
                    "impl": f"{_DurableScriptedPattern.__module__}.{_DurableScriptedPattern.__name__}",
                },
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {
                    "max_steps": 8,
                    "step_timeout_ms": 2000,
                    "session_queue_size": 16,
                    "event_queue_size": 64,
                },
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_durable_run_auto_recovers_end_to_end(tmp_path: Path):
    """Single-process: durable run survives mid-run LLMRateLimitError via
    in-band resume (no external intervention)."""
    root = tmp_path / "sessions"
    runtime = Runtime(load_config_dict(_make_jsonl_config(root)))

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="resumable",
            session_id="sess-auto",
            input_text="go",
            durable=True,
            budget=RunBudget(max_resume_attempts=2),
        )
    )
    assert result.stop_reason == StopReason.COMPLETED
    assert result.final_output == {"answer": "restored"}

    events = [e.name for e in runtime.event_bus.history]
    # At least 2 step checkpoints before the failure, then 1 resume cycle.
    assert events.count("run.checkpoint_saved") >= 2
    assert events.count("run.resume_attempted") == 1
    assert events.count("run.resume_succeeded") == 1
    assert "run.resume_exhausted" not in events

    # The jsonl session file contains the checkpoints (durable on disk).
    session_file = root / "sess-auto.jsonl"
    assert session_file.exists()
    lines = [line for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    entry_types = {json.loads(line)["type"] for line in lines}
    assert "checkpoint" in entry_types


@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_durable_checkpoint_survives_fresh_runtime(tmp_path: Path):
    """Cross-instance: a freshly-constructed Runtime can load a checkpoint
    persisted to jsonl by a prior Runtime — no in-memory coupling."""
    root = tmp_path / "sessions"
    cfg = _make_jsonl_config(root)

    # Run 1 — durable; pattern raises on first attempt then succeeds on
    # second. Records at least 2 checkpoints on disk.
    runtime1 = Runtime(load_config_dict(cfg))
    result1 = await runtime1.run_detailed(
        request=RunRequest(
            agent_id="resumable",
            session_id="sess-cross",
            input_text="go",
            durable=True,
        )
    )
    assert result1.stop_reason == StopReason.COMPLETED

    saved = [e for e in runtime1.event_bus.history if e.name == "run.checkpoint_saved"]
    assert saved, "expected checkpoint persistence"
    target_ckpt = saved[-1].payload["checkpoint_id"]

    # Run 2 — fresh Runtime, same jsonl file. Query list_checkpoints via the
    # fresh session manager to prove the state survived the instance boundary.
    runtime2 = Runtime(load_config_dict(cfg))
    ids = await runtime2.session_manager.list_checkpoints("sess-cross")
    assert target_ckpt in ids

    checkpoint = await runtime2.session_manager.load_checkpoint("sess-cross", target_ckpt)
    assert checkpoint is not None
    assert "__durable__" in checkpoint.state
    assert checkpoint.state["__durable__"]["checkpoint_id"] == target_ckpt


@pytest.mark.asyncio
@pytest.mark.timeout(20)
async def test_durable_max_resume_attempts_exhausts(tmp_path: Path):
    """Resume budget caps repeated transient failures and emits
    run.resume_exhausted then propagates FAILED."""
    root = tmp_path / "sessions"
    cfg = _make_jsonl_config(root)
    # Swap pattern impl to the always-failing class.
    cfg["agents"][0]["pattern"]["impl"] = f"{_AlwaysFailingPattern.__module__}.{_AlwaysFailingPattern.__name__}"

    runtime = Runtime(load_config_dict(cfg))
    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="resumable",
            session_id="sess-exhaust",
            input_text="go",
            durable=True,
            budget=RunBudget(max_resume_attempts=2),
        )
    )
    assert result.stop_reason == StopReason.FAILED
    events = [e.name for e in runtime.event_bus.history]
    assert events.count("run.resume_attempted") == 2
    assert events.count("run.resume_exhausted") == 1
