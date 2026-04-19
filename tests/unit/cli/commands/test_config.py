"""Tests for ``openagents config show``.

Covers:

* Valid config → JSON dump with ``impl`` paths annotated.
* Invalid JSON / missing file → exit code 2.
* ``--redact`` replaces ``api_key`` / ``token`` / ``password`` /
  ``secret`` leaves with ``***``.
* YAML output when PyYAML is present; plain JSON fallback when it's not.
* Env var substitution happens via ``load_config``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openagents.cli.commands import config as config_cmd
from openagents.cli.main import main as cli_main


def _valid_agent_json(tmp_path: Path, *, with_secret: bool = False) -> Path:
    cfg = tmp_path / "agent.json"
    agent: dict = {
        "id": "a",
        "name": "x",
        "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
        "pattern": {"type": "react"},
        "llm": {"provider": "mock", "model": "m"},
        "tools": [],
    }
    if with_secret:
        agent["llm"]["api_key"] = "sk-super-secret-leak"
        agent["llm"]["extra_token"] = "tok-abc"
    cfg.write_text(json.dumps({"version": "1.0", "agents": [agent]}))
    return cfg


def test_config_show_valid_prints_json(tmp_path, capsys):
    cfg = _valid_agent_json(tmp_path)
    code = cli_main(["config", "show", str(cfg)])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["agents"][0]["id"] == "a"


def test_config_show_annotates_builtin_impl_paths(tmp_path, capsys):
    cfg = _valid_agent_json(tmp_path)
    cli_main(["config", "show", str(cfg)])
    data = json.loads(capsys.readouterr().out)
    pattern = data["agents"][0]["pattern"]
    assert pattern["type"] == "react"
    # ``react`` is a builtin pattern → impl should be annotated.
    assert "impl" in pattern
    assert pattern["impl"].startswith("openagents.plugins.builtin.pattern.react")


def test_config_show_redact_replaces_secret_fields(tmp_path, capsys):
    cfg = _valid_agent_json(tmp_path, with_secret=True)
    cli_main(["config", "show", str(cfg), "--redact"])
    out = capsys.readouterr().out
    assert "sk-super-secret-leak" not in out
    assert "tok-abc" not in out
    assert "***" in out


def test_config_show_no_redact_leaves_secrets_visible(tmp_path, capsys):
    cfg = _valid_agent_json(tmp_path, with_secret=True)
    cli_main(["config", "show", str(cfg)])
    out = capsys.readouterr().out
    assert "sk-super-secret-leak" in out


def test_config_show_missing_file_returns_2(tmp_path, capsys):
    code = cli_main(["config", "show", str(tmp_path / "nonexistent.json")])
    assert code == 2
    assert "ConfigLoadError" in capsys.readouterr().err


def test_config_show_invalid_json_returns_2(tmp_path, capsys):
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json")
    code = cli_main(["config", "show", str(bad)])
    assert code == 2


def test_config_show_env_var_substitution(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TEST_MODEL_NAME", "substituted-model")
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
                        "llm": {"provider": "mock", "model": "${TEST_MODEL_NAME}"},
                        "tools": [],
                    }
                ],
            }
        )
    )
    code = cli_main(["config", "show", str(cfg)])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["agents"][0]["llm"]["model"] == "substituted-model"


def test_config_show_yaml_format_when_available(tmp_path, capsys):
    pytest.importorskip("yaml")
    cfg = _valid_agent_json(tmp_path)
    code = cli_main(["config", "show", str(cfg), "--format", "yaml"])
    assert code == 0
    out = capsys.readouterr().out
    # Valid YAML starts with a mapping (no leading "{") and contains known keys.
    assert not out.lstrip().startswith("{")
    assert "version:" in out
    assert "agents:" in out


def test_config_show_yaml_falls_back_to_json_without_pyyaml(tmp_path, capsys, monkeypatch):
    # Force the fallback by making require_or_hint return None.
    monkeypatch.setattr(config_cmd, "require_or_hint", lambda name: None)
    cfg = _valid_agent_json(tmp_path)
    code = cli_main(["config", "show", str(cfg), "--format", "yaml"])
    assert code == 0
    out = capsys.readouterr().out
    # Fallback emits JSON.
    json.loads(out)


def test_config_top_level_without_subaction_prints_usage(capsys):
    code = cli_main(["config"])
    assert code == 1
    assert "usage: openagents config show" in capsys.readouterr().err


def test_redact_helper_handles_nested_structures():
    blob = {
        "agents": [
            {"llm": {"api_key": "s1", "model": "m"}},
            {"headers": {"Authorization": "Bearer t"}},
            {"misc": {"PasswordField": "pw"}},
        ]
    }
    red = config_cmd._redact(blob)
    assert red["agents"][0]["llm"]["api_key"] == "***"
    assert red["agents"][0]["llm"]["model"] == "m"
    assert red["agents"][2]["misc"]["PasswordField"] == "***"


def test_annotate_refs_ignores_when_impl_already_set():
    blob = {
        "agents": [
            {
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {"type": "react"},
            }
        ]
    }
    annotated = config_cmd._annotate_refs(blob)
    mem = annotated["agents"][0]["memory"]
    # impl was pre-set, not overwritten.
    assert mem["impl"] == "tests.fixtures.runtime_plugins.InjectWritebackMemory"
    # type was absent; _annotate_refs must not invent one.
    assert "type" not in mem


def test_annotate_refs_skips_unknown_types():
    blob = {"agents": [{"pattern": {"type": "does-not-exist-anywhere"}}]}
    annotated = config_cmd._annotate_refs(blob)
    # Unknown type → no impl annotation; we don't fabricate paths.
    assert "impl" not in annotated["agents"][0]["pattern"]
