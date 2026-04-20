"""Tests for per-project ``events.jsonl`` persistence in the pptx-agent wizard."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from examples.pptx_generator import cli
from examples.pptx_generator.state import DeckProject


class _StubWizard:
    """Wizard replacement that terminates before any stage runs.

    Used by the ``run_wizard`` env-var management tests — we only care about
    the pre-run / post-run env mutation, not the stage loop.
    """

    def __init__(self, *a: Any, **kw: Any) -> None:
        self._console = kw.get("console")

    async def run(self) -> str:
        return "completed"

    async def resume(self, from_step: str) -> str:
        return "completed"


@pytest.mark.asyncio
async def test_run_wizard_sets_events_log_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPTX_AGENT_OUTPUTS", str(tmp_path))
    monkeypatch.delenv("PPTX_EVENTS_LOG", raising=False)

    captured: dict[str, str | None] = {}

    def fake_wizard_ctor(*a: Any, **kw: Any) -> _StubWizard:
        captured["log_env"] = os.environ.get("PPTX_EVENTS_LOG")
        return _StubWizard(*a, **kw)

    monkeypatch.setattr("examples.pptx_generator.cli.Wizard", fake_wizard_ctor)

    project = DeckProject(
        slug="env-default",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        stage="intent",
    )
    runtime = SimpleNamespace(run=AsyncMock())
    shell_tool = SimpleNamespace(invoke=AsyncMock())

    rc = await cli.run_wizard(project, runtime=runtime, shell_tool=shell_tool)

    assert rc == 0
    expected = tmp_path / "env-default" / "events.jsonl"
    assert captured["log_env"] == str(expected)


@pytest.mark.asyncio
async def test_run_wizard_restores_prior_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPTX_AGENT_OUTPUTS", str(tmp_path))
    monkeypatch.setenv("PPTX_EVENTS_LOG", "/external/override.jsonl")

    captured: dict[str, str | None] = {}

    def fake_wizard_ctor(*a: Any, **kw: Any) -> _StubWizard:
        captured["during"] = os.environ.get("PPTX_EVENTS_LOG")
        return _StubWizard(*a, **kw)

    monkeypatch.setattr("examples.pptx_generator.cli.Wizard", fake_wizard_ctor)

    project = DeckProject(
        slug="env-override",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        stage="intent",
    )
    runtime = SimpleNamespace(run=AsyncMock())
    shell_tool = SimpleNamespace(invoke=AsyncMock())

    await cli.run_wizard(project, runtime=runtime, shell_tool=shell_tool)

    # Caller override is honored during the run...
    assert captured["during"] == "/external/override.jsonl"
    # ...and preserved after the run (not clobbered by the wizard).
    assert os.environ.get("PPTX_EVENTS_LOG") == "/external/override.jsonl"


@pytest.mark.asyncio
async def test_run_wizard_unsets_env_var_when_absent_at_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPTX_AGENT_OUTPUTS", str(tmp_path))
    monkeypatch.delenv("PPTX_EVENTS_LOG", raising=False)
    monkeypatch.setattr("examples.pptx_generator.cli.Wizard", _StubWizard)

    project = DeckProject(
        slug="env-unset-after",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        stage="intent",
    )
    runtime = SimpleNamespace(run=AsyncMock())
    shell_tool = SimpleNamespace(invoke=AsyncMock())

    await cli.run_wizard(project, runtime=runtime, shell_tool=shell_tool)

    assert os.environ.get("PPTX_EVENTS_LOG") is None


@pytest.mark.asyncio
async def test_run_wizard_creates_project_dir_before_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPTX_AGENT_OUTPUTS", str(tmp_path))
    monkeypatch.delenv("PPTX_EVENTS_LOG", raising=False)
    monkeypatch.setattr("examples.pptx_generator.cli.Wizard", _StubWizard)

    project = DeckProject(
        slug="dir-created",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        stage="intent",
    )
    runtime = SimpleNamespace(run=AsyncMock())
    shell_tool = SimpleNamespace(invoke=AsyncMock())

    await cli.run_wizard(project, runtime=runtime, shell_tool=shell_tool)

    assert (tmp_path / "dir-created").is_dir()


@pytest.mark.asyncio
async def test_file_logging_event_bus_writes_jsonl_with_redaction(
    tmp_path: Path,
) -> None:
    """End-to-end: the builtin FileLoggingEventBus appends redacted JSONL."""
    from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus

    log_path = tmp_path / "events.jsonl"
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log_path),
            "inner": {"type": "async"},
            "redact_keys": ["api_key", "authorization"],
        }
    )

    await bus.emit("run.started", agent_id="intent-analyst", api_key="SECRET-XYZ")
    await bus.emit("run.finished", agent_id="intent-analyst", outcome="ok")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["name"] == "run.started"
    assert first["payload"]["agent_id"] == "intent-analyst"
    # api_key SHALL be redacted (either replaced or elided by the builtin).
    serialized = json.dumps(first["payload"])
    assert "SECRET-XYZ" not in serialized
    second = json.loads(lines[1])
    assert second["name"] == "run.finished"
    assert second["payload"]["outcome"] == "ok"


@pytest.mark.asyncio
async def test_file_logging_event_bus_appends_on_resume(tmp_path: Path) -> None:
    """Second FileLoggingEventBus instance pointing at an existing file appends."""
    from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus

    log_path = tmp_path / "events.jsonl"
    log_path.write_text(
        json.dumps({"name": "prior", "payload": {}, "ts": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )

    bus = FileLoggingEventBus(config={"log_path": str(log_path), "inner": {"type": "async"}})
    await bus.emit("run.finished", outcome="ok")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["name"] == "prior"
    assert json.loads(lines[1])["name"] == "run.finished"


def test_agent_json_events_block_is_file_logging() -> None:
    """The pptx example's agent.json wraps PrettyEventBus in file_logging."""
    import json as _json

    raw = _json.loads(Path("examples/pptx_generator/agent.json").read_text(encoding="utf-8"))
    events = raw.get("events", {})
    assert events.get("type") == "file_logging"
    inner = events.get("config", {}).get("inner", {})
    assert inner.get("impl", "").endswith("PrettyEventBus")
    assert "${PPTX_EVENTS_LOG" in events.get("config", {}).get("log_path", "")
