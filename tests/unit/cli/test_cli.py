"""CLI unit tests (schema / validate / list-plugins)."""

from __future__ import annotations

import json

from openagents.cli.list_plugins_cmd import run as list_plugins_run
from openagents.cli.main import main as cli_main
from openagents.cli.schema_cmd import run as schema_run
from openagents.cli.validate_cmd import run as validate_run


def test_cli_no_subcommand_exits_1(capsys):
    code = cli_main([])
    captured = capsys.readouterr()
    assert code == 1
    assert "usage" in (captured.err + captured.out).lower()


def test_schema_dumps_appconfig_json(capsys):
    code = schema_run([])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "properties" in data or "$defs" in data


def test_schema_unknown_plugin_returns_2(capsys):
    code = schema_run(["--plugin", "does-not-exist"])
    assert code == 2


def test_schema_seam_filter_produces_mapping(capsys):
    code = schema_run(["--seam", "context_assembler"])
    # The seam exists, so this returns 0. The output is a JSON mapping
    # (possibly empty if no plugin declares a Config yet).
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, dict)


def test_list_plugins_table_format_lists_context_truncating(capsys):
    code = list_plugins_run([])
    assert code == 0
    captured = capsys.readouterr()
    # Table format should mention our renamed context_assembler.
    assert "context_assembler" in captured.out
    assert "truncating" in captured.out


def test_list_plugins_json_roundtrip(capsys):
    code = list_plugins_run(["--format", "json"])
    assert code == 0
    captured = capsys.readouterr()
    rows = json.loads(captured.out)
    assert any(
        r["seam"] == "context_assembler" and r["name"] == "truncating" for r in rows
    )


def test_validate_accepts_minimal_config(tmp_path, capsys):
    cfg = tmp_path / "agent.json"
    cfg.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": [
                    {
                        "id": "a",
                        "name": "x",
                        "memory": {
                            "impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"
                        },
                        "pattern": {"type": "react"},
                        "llm": {"provider": "mock", "model": "m"},
                        "tools": [],
                        "runtime": {
                            "max_steps": 8,
                            "step_timeout_ms": 1000,
                            "session_queue_size": 10,
                            "event_queue_size": 10,
                        },
                    }
                ],
            }
        )
    )
    code = validate_run([str(cfg)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "OK:" in captured.out


def test_validate_rejects_malformed_json(tmp_path, capsys):
    cfg = tmp_path / "agent.json"
    cfg.write_text("{not valid json")
    code = validate_run([str(cfg)])
    assert code == 2
    captured = capsys.readouterr()
    assert "ConfigLoadError" in captured.err or "ConfigValidationError" in captured.err


def test_validate_strict_flags_unresolved_plugin(tmp_path, capsys):
    cfg = tmp_path / "agent.json"
    cfg.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": [
                    {
                        "id": "a",
                        "name": "x",
                        "memory": {
                            "impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"
                        },
                        "pattern": {"type": "react"},
                        "llm": {"provider": "mock", "model": "m"},
                        "tools": [],
                        "context_assembler": {"type": "not-a-real-type"},
                        "runtime": {
                            "max_steps": 8,
                            "step_timeout_ms": 1000,
                            "session_queue_size": 10,
                            "event_queue_size": 10,
                        },
                    }
                ],
            }
        )
    )
    code = validate_run([str(cfg), "--strict"])
    captured = capsys.readouterr()
    assert code == 2
    assert "unresolved" in captured.err.lower()
