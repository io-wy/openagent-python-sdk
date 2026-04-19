"""Tests for ``openagents run``.

All paths exercise the mock LLM provider — no network, no external
services. The fixtures build minimal valid ``agent.json`` files under
``tmp_path`` and drive the CLI via ``cli_main``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from openagents.cli.main import main as cli_main


def _valid_agent(tmp_path: Path, *, agent_id: str = "a", extra_agents: list | None = None) -> Path:
    cfg_path = tmp_path / "agent.json"
    agents = [
        {
            "id": agent_id,
            "name": "x",
            "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
            "pattern": {"type": "react", "config": {"max_steps": 1}},
            "llm": {"provider": "mock", "model": "m"},
            "tools": [],
            "runtime": {
                "max_steps": 1,
                "step_timeout_ms": 5000,
                "session_queue_size": 10,
                "event_queue_size": 10,
            },
        }
    ]
    if extra_agents:
        agents.extend(extra_agents)
    cfg_path.write_text(json.dumps({"version": "1.0", "agents": agents}))
    return cfg_path


def test_run_single_agent_with_input_flag(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    code = cli_main(["run", str(cfg), "--input", "hello", "--format", "text", "--no-stream"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Echo: hello" in out


def test_run_json_format_returns_full_result(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    code = cli_main(["run", str(cfg), "--input", "hi", "--format", "json", "--no-stream"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "run_id" in payload
    assert "stop_reason" in payload
    assert "Echo: hi" in str(payload["final_output"])


def test_run_events_format_emits_jsonl(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    cli_main(["run", str(cfg), "--input", "hi", "--format", "events"])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one JSONL event line"
    for ln in lines:
        blob = json.loads(ln)
        assert "name" in blob
        assert "payload" in blob
    # The terminal run.finished line must be present.
    assert any(json.loads(ln)["name"] == "run.finished" for ln in lines)


def test_run_missing_input_returns_1(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    # Force stdin to look TTY-ish so no pipe data is consumed.
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    code = cli_main(["run", str(cfg)])
    assert code == 1
    assert "no input" in capsys.readouterr().err


def test_run_multi_agent_without_agent_flag_returns_1(tmp_path, capsys):
    extra = {
        "id": "b",
        "name": "y",
        "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
        "pattern": {"type": "react", "config": {"max_steps": 1}},
        "llm": {"provider": "mock", "model": "m"},
        "tools": [],
        "runtime": {
            "max_steps": 1,
            "step_timeout_ms": 5000,
            "session_queue_size": 10,
            "event_queue_size": 10,
        },
    }
    cfg = _valid_agent(tmp_path, extra_agents=[extra])
    code = cli_main(["run", str(cfg), "--input", "hi"])
    assert code == 1
    err = capsys.readouterr().err
    assert "config declares 2 agents" in err


def test_run_unknown_agent_flag_returns_1(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    code = cli_main(["run", str(cfg), "--input", "hi", "--agent", "does_not_exist"])
    assert code == 1
    assert "agent not found" in capsys.readouterr().err


def test_run_bad_config_returns_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    code = cli_main(["run", str(bad), "--input", "hi"])
    assert code == 2


def test_run_missing_config_returns_2(tmp_path, capsys):
    code = cli_main(["run", str(tmp_path / "nope.json"), "--input", "hi"])
    assert code == 2


def test_run_input_file_reads_prompt(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("from-a-file")
    code = cli_main(
        ["run", str(cfg), "--input-file", str(prompt_file), "--format", "text", "--no-stream"]
    )
    assert code == 0
    assert "Echo: from-a-file" in capsys.readouterr().out


def test_run_stdin_input_fallback(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    # Simulate piped stdin ("not a TTY") carrying the prompt.
    monkeypatch.setattr("sys.stdin", io.StringIO("piped-prompt\n"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    code = cli_main(["run", str(cfg), "--format", "text", "--no-stream"])
    assert code == 0
    # The mock provider echoes the prompt.
    assert "Echo: piped-prompt" in capsys.readouterr().out


def test_run_explicit_session_id_is_used(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    code = cli_main(
        [
            "run",
            str(cfg),
            "--input",
            "hi",
            "--format",
            "json",
            "--no-stream",
            "--session-id",
            "fixed-session-123",
        ]
    )
    assert code == 0
    # Session id is not part of RunResult, but the command still succeeds.
    payload = json.loads(capsys.readouterr().out)
    assert "run_id" in payload


def test_run_selects_agent_by_flag(tmp_path, capsys):
    extra = {
        "id": "second",
        "name": "y",
        "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
        "pattern": {"type": "react", "config": {"max_steps": 1}},
        "llm": {"provider": "mock", "model": "m"},
        "tools": [],
        "runtime": {
            "max_steps": 1,
            "step_timeout_ms": 5000,
            "session_queue_size": 10,
            "event_queue_size": 10,
        },
    }
    cfg = _valid_agent(tmp_path, extra_agents=[extra])
    code = cli_main(
        [
            "run",
            str(cfg),
            "--agent",
            "second",
            "--input",
            "hi",
            "--format",
            "text",
            "--no-stream",
        ]
    )
    assert code == 0


def test_run_with_format_events_streams_events(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    cli_main(["run", str(cfg), "--input", "hi", "--format", "events"])
    out = capsys.readouterr().out
    # At minimum the terminal run.finished event should be there; we also
    # expect the event-bus subscriber to have emitted at least one upstream
    # event (tool/llm) — assert loosely.
    assert "run.finished" in out
    assert out.count("\n") >= 1


def test_run_input_file_missing_returns_1(tmp_path, capsys):
    cfg = _valid_agent(tmp_path)
    code = cli_main(["run", str(cfg), "--input-file", str(tmp_path / "missing.txt")])
    assert code == 1
    assert "failed to read --input-file" in capsys.readouterr().err


def test_run_default_format_prefers_events_when_stdout_not_a_tty(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    cli_main(["run", str(cfg), "--input", "hi"])
    out = capsys.readouterr().out
    # Default for piped stdout is JSONL events.
    assert "run.finished" in out


def test_run_default_format_prefers_text_when_stdout_is_a_tty(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    cli_main(["run", str(cfg), "--input", "hi", "--no-stream"])
    out = capsys.readouterr().out
    assert "Echo: hi" in out


def test_run_runtime_exception_returns_3(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    from openagents.runtime.runtime import Runtime

    async def _blow_up(self, *, request):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated LLM failure")

    monkeypatch.setattr(Runtime, "run_detailed", _blow_up)
    code = cli_main(["run", str(cfg), "--input", "hi", "--no-stream"])
    assert code == 3
    err = capsys.readouterr().err
    assert "simulated LLM failure" in err


def test_run_close_failure_is_swallowed(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    from openagents.runtime.runtime import Runtime

    async def _bad_close(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("close-failed")

    monkeypatch.setattr(Runtime, "close", _bad_close)
    code = cli_main(["run", str(cfg), "--input", "hi", "--no-stream"])
    # Best-effort: close errors don't affect exit code.
    assert code == 0


def test_run_config_error_during_runtime_construction_returns_2(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    from openagents.errors.exceptions import ConfigLoadError
    from openagents.runtime.runtime import Runtime

    def _raise(path):
        raise ConfigLoadError("fabricated")

    monkeypatch.setattr(Runtime, "from_config", staticmethod(_raise))
    code = cli_main(["run", str(cfg), "--input", "hi"])
    assert code == 2
