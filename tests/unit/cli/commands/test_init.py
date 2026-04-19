"""Tests for ``openagents init``.

Key properties verified:

* Each bundled template produces a directory containing ``agent.json`` +
  ``README.md``.
* The generated ``agent.json`` passes ``openagents validate`` when the
  ``impl`` of each seam is reachable (so minimal template produces a
  config that at least *parses* — we don't require a working LLM
  backend for the scaffold).
* Refusal on collision without ``--force``; success with ``--force``.
* Placeholder substitution happens (``{{ project_name }}`` replaced).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openagents.cli.commands import init as init_cmd
from openagents.cli.main import main as cli_main


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("template", ["minimal", "coding-agent", "pptx-wizard"])
def test_init_each_template_produces_parseable_agent_json(template, workdir):
    proj = workdir / f"proj_{template.replace('-', '_')}"
    code = cli_main(
        [
            "init",
            str(proj),
            "--template",
            template,
            "--provider",
            "mock",
            "--yes",
        ]
    )
    assert code == 0
    agent = proj / "agent.json"
    assert agent.exists()
    readme = proj / "README.md"
    assert readme.exists()
    # agent.json must at least be valid JSON.
    data = json.loads(agent.read_text(encoding="utf-8"))
    assert data["version"] == "1.0"
    assert data["agents"][0]["llm"]["provider"] == "mock"


def test_init_placeholder_substitution_happens(workdir):
    proj = workdir / "custom_project_name"
    cli_main(
        [
            "init",
            str(proj),
            "--template",
            "minimal",
            "--provider",
            "anthropic",
            "--api-key-env",
            "FANCY_KEY",
            "--yes",
        ]
    )
    data = json.loads((proj / "agent.json").read_text(encoding="utf-8"))
    assert data["agents"][0]["name"] == "custom_project_name"
    assert data["agents"][0]["llm"]["api_key_env"] == "FANCY_KEY"
    readme = (proj / "README.md").read_text(encoding="utf-8")
    assert "custom_project_name" in readme
    assert "anthropic" in readme


def test_init_refuses_non_empty_directory_without_force(workdir, capsys):
    proj = workdir / "existing"
    proj.mkdir()
    (proj / "file.txt").write_text("existing content")
    code = cli_main(
        ["init", str(proj), "--template", "minimal", "--provider", "mock", "--yes"]
    )
    assert code == 1
    assert "not empty" in capsys.readouterr().err


def test_init_force_overwrites_existing_directory(workdir):
    proj = workdir / "existing"
    proj.mkdir()
    (proj / "file.txt").write_text("existing content")
    code = cli_main(
        [
            "init",
            str(proj),
            "--template",
            "minimal",
            "--provider",
            "mock",
            "--yes",
            "--force",
        ]
    )
    assert code == 0
    assert (proj / "agent.json").exists()
    # Existing unrelated file survives — only scaffold files are written.
    assert (proj / "file.txt").exists()


def test_init_refuses_when_path_is_a_file(workdir, capsys):
    conflict = workdir / "conflict.txt"
    conflict.write_text("not a dir", encoding="utf-8")
    code = cli_main(
        ["init", str(conflict), "--template", "minimal", "--provider", "mock", "--yes"]
    )
    assert code == 1
    assert "not a directory" in capsys.readouterr().err


def test_init_empty_existing_directory_is_allowed(workdir):
    proj = workdir / "empty_existing"
    proj.mkdir()
    code = cli_main(
        ["init", str(proj), "--template", "minimal", "--provider", "mock", "--yes"]
    )
    assert code == 0


def test_init_default_api_key_env_by_provider(workdir):
    proj = workdir / "defaulted"
    cli_main(["init", str(proj), "--template", "minimal", "--provider", "anthropic", "--yes"])
    data = json.loads((proj / "agent.json").read_text(encoding="utf-8"))
    assert data["agents"][0]["llm"]["api_key_env"] == "ANTHROPIC_API_KEY"


def test_default_api_key_env_helper():
    assert init_cmd._default_api_key_env("anthropic") == "ANTHROPIC_API_KEY"
    assert init_cmd._default_api_key_env("openai-compatible") == "OPENAI_API_KEY"
    assert init_cmd._default_api_key_env("mock") == "MOCK_API_KEY"
    # Unknown providers still get a sensible fallback rather than crashing.
    assert init_cmd._default_api_key_env("something-else") == "API_KEY"


def test_render_substitutes_multiple_occurrences():
    text = "Project {{ project_name }} uses {{ provider }} via {{ project_name }}"
    out = init_cmd._render(
        text, {"project_name": "demo", "provider": "mock"}
    )
    assert out == "Project demo uses mock via demo"


def test_init_interactive_fallback_when_questionary_missing(workdir, monkeypatch):
    # When --yes is not passed and questionary is absent, command should
    # still succeed using declared flags and defaults.
    monkeypatch.setattr(init_cmd, "require_or_hint", lambda name: None)
    proj = workdir / "interactive_fallback"
    # Invoke directly without --yes.
    code = cli_main(
        ["init", str(proj), "--template", "minimal", "--provider", "mock"]
    )
    assert code == 0
    assert (proj / "agent.json").exists()
