"""Integration smoke tests exercising `python -m openagents` end-to-end."""

from __future__ import annotations

import json
import subprocess
import sys


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "openagents", *args],
        capture_output=True,
        text=True,
        **kwargs,
    )


def test_schema_dump_is_valid_json():
    result = _run(["schema"])
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data, dict)


def test_list_plugins_json_includes_context_assembler():
    result = _run(["list-plugins", "--format", "json"])
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert any(r.get("seam") == "context_assembler" and r.get("name") == "truncating" for r in rows)


def test_validate_minimal_config(tmp_path):
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
    result = _run(["validate", str(cfg)])
    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout
