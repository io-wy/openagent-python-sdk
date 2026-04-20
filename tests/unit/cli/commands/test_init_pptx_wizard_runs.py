"""End-to-end scaffold test: ``init --template pptx-wizard`` output is runnable.

Per `builtin-cli` spec delta — "Scaffold runs against mock provider".
Scaffold a pptx-wizard project, then dispatch ``openagents run`` against
the mock LLM to confirm the scaffold is not just syntactically valid but
actually executes an agent end-to-end without any code edits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openagents.cli.main import main as cli_main


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_pptx_wizard_scaffold_runs_against_mock(workdir, capsys, monkeypatch):
    # Provide the api-key env var the scaffold declares, even though mock
    # provider does not actually require a real key.
    monkeypatch.setenv("MOCK_API_KEY", "unused-by-mock")

    proj = workdir / "pptx_runnable"
    rc_init = cli_main(
        [
            "init",
            str(proj),
            "--template",
            "pptx-wizard",
            "--provider",
            "mock",
            "--yes",
        ]
    )
    assert rc_init == 0

    agent_path = proj / "agent.json"
    rc_run = cli_main(
        [
            "run",
            str(agent_path),
            "--agent",
            "intent-analyst",
            "--input",
            "hello",
            "--format",
            "events",
        ]
    )
    assert rc_run == 0

    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one JSONL event line"
    event_names = set()
    for ln in lines:
        try:
            blob = json.loads(ln)
        except json.JSONDecodeError:
            continue
        name = blob.get("name")
        if isinstance(name, str):
            event_names.add(name)
    assert "run.finished" in event_names, f"scaffold run must reach run.finished; got: {sorted(event_names)}"
