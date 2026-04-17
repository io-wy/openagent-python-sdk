from __future__ import annotations

import json
import subprocess
import sys

import pytest


EXPECTED: list[tuple[str, str]] = [
    ("tool_executor", "retry"),
    ("execution_policy", "composite"),
    ("execution_policy", "network_allowlist"),
    ("followup_resolver", "rule_based"),
    ("session", "jsonl_file"),
    ("session", "sqlite"),
    ("events", "file_logging"),
    ("events", "otel_bridge"),
    ("response_repair_policy", "strict_json"),
]


def _run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "openagents", *args],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    return result.stdout


@pytest.mark.parametrize("seam,name", EXPECTED)
def test_list_plugins_json_includes_new_builtin(seam: str, name: str):
    stdout = _run("list-plugins", "--seam", seam, "--source", "builtin", "--format", "json")
    rows = json.loads(stdout)
    names = {row["name"] for row in rows}
    assert name in names, f"{seam}/{name} missing; got {sorted(names)}"
    # Should also be present in table form.
    table = _run("list-plugins", "--seam", seam, "--source", "builtin")
    assert name in table


@pytest.mark.parametrize("seam,name", EXPECTED)
def test_schema_exposes_config_for_new_builtin(seam: str, name: str):
    stdout = _run("schema", "--seam", seam, "--plugin", name)
    data = json.loads(stdout)
    assert isinstance(data, dict), f"{seam}/{name} schema is not a dict"
    # pydantic v2 model_json_schema output has a "properties" key at top level.
    assert "properties" in data, f"{seam}/{name} schema missing 'properties'; keys={sorted(data)}"
