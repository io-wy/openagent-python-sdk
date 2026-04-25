"""Tests for ``openagents doctor``.

The critical invariants:

* Exit ``0`` when Python + required checks pass; ``1`` otherwise.
* API-key values are NEVER printed, only presence.
* ``--config`` path is wired through ``load_config`` and surfaced.
* ``--format json`` is a stable machine-parseable shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openagents.cli.commands import doctor
from openagents.cli.main import main as cli_main


def _write_valid_agent(tmp_path: Path) -> Path:
    cfg = tmp_path / "agent.json"
    cfg.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": [
                    {
                        "id": "a",
                        "name": "x",
                        "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                        "pattern": {"type": "react"},
                        "llm": {"provider": "mock", "model": "m"},
                        "tools": [],
                    }
                ],
            }
        )
    )
    return cfg


def test_doctor_healthy_env_exits_zero(capsys):
    code = cli_main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Overall: OK" in out
    assert "Builtin plugins:" in out


def test_doctor_json_shape(capsys):
    code = cli_main(["doctor", "--format", "json"])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert set(data) >= {"python", "extras", "env_vars", "builtin_plugin_counts", "ok"}
    assert isinstance(data["extras"], list)
    assert all("name" in row and "installed" in row for row in data["extras"])
    assert all("name" in row and "set" in row for row in data["env_vars"])


def test_doctor_never_prints_api_key_values(monkeypatch, capsys):
    sentinel = "sk-secret-NEVER-PRINT-ME-123"
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    monkeypatch.setenv("MINIMAX_API_KEY", sentinel)
    code = cli_main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0
    assert sentinel not in out


def test_doctor_json_redaction(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    cli_main(["doctor", "--format", "json"])
    out = capsys.readouterr().out
    assert "sk-should-not-leak" not in out


def test_doctor_with_valid_config_path(tmp_path, capsys):
    cfg = _write_valid_agent(tmp_path)
    code = cli_main(["doctor", "--config", str(cfg)])
    assert code == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    assert str(cfg) in out


def test_doctor_with_invalid_config_path_fails(tmp_path, capsys):
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json")
    code = cli_main(["doctor", "--config", str(bad)])
    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "broken.json" in out


def test_doctor_fails_when_python_below_minimum(monkeypatch, capsys):
    # Force _python_meets_minimum to return False by patching sys.version_info
    # via the module-level helper.
    monkeypatch.setattr(doctor, "_python_meets_minimum", lambda: (False, "2.7.0", "3.11"))
    code = cli_main(["doctor"])
    assert code == 1
    out = capsys.readouterr().out
    assert "Overall: FAIL" in out


def test_python_meets_minimum_parses_metadata_string():
    ok, detected, required = doctor._python_meets_minimum()
    # We're running under a supported Python, so ok must be True here.
    assert ok
    assert detected.count(".") == 2
    assert required  # non-empty string regardless of whether dist is installed


def test_env_var_status_reports_presence_not_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "some-real-value")
    rows = doctor._env_var_status()
    anth = next(r for r in rows if r["name"] == "ANTHROPIC_API_KEY")
    assert anth["set"] is True
    assert "some-real-value" not in json.dumps(rows)


def test_extras_status_returns_expected_shape():
    rows = doctor._extras_status()
    assert len(rows) == len(doctor._OPTIONAL_EXTRAS)
    assert all(isinstance(r["installed"], bool) for r in rows)


def test_doctor_rejects_invalid_format_choice(capsys):
    # ``--format`` has choices=[text, json]; any other value is rejected
    # by argparse with SystemExit(2). (``--config`` accepts any string,
    # so wrong-looking paths surface as graceful FAIL reports instead.)
    with pytest.raises(SystemExit):
        cli_main(["doctor", "--format", "not-a-format"])
