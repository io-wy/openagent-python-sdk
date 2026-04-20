"""Tests for ``openagents version`` and the root ``-V`` / ``--version`` flag."""

from __future__ import annotations

import importlib.metadata
import json
import re

import pytest

from openagents.cli.commands import version as version_cmd
from openagents.cli.main import main as cli_main


def test_version_default_text_is_single_line(capsys):
    code = cli_main(["version"])
    assert code == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    # Shape: "openagents <sdk> python <py> extras [<list>]"
    assert re.match(r"^openagents \S+ python \S+ extras \[.*\]$", lines[0])


def test_version_verbose_expands_into_plugin_counts(capsys):
    code = cli_main(["version", "--verbose"])
    assert code == 0
    out = capsys.readouterr().out
    # Either rich table or plain-text fallback; both mention "plugins" per seam.
    assert "openagents" in out
    assert "plugins/" in out or "plugin counts" in out


def test_version_json_is_parseable_and_has_expected_keys(capsys):
    code = cli_main(["version", "--format", "json"])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert set(data) == {"sdk", "python", "extras", "builtin_plugin_counts"}
    assert isinstance(data["extras"], list)
    assert isinstance(data["builtin_plugin_counts"], dict)
    assert data["sdk"]
    assert data["python"]


def test_root_dash_V_equivalent_to_version_subcommand(capsys):
    code = cli_main(["-V"])
    assert code == 0
    out = capsys.readouterr().out
    assert re.match(r"^openagents ", out)


def test_root_long_version_flag_equivalent(capsys):
    code = cli_main(["--version"])
    assert code == 0
    out = capsys.readouterr().out
    assert re.match(r"^openagents ", out)


def test_sdk_version_fallback_when_distribution_missing(monkeypatch, capsys):
    def _raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version_cmd.importlib.metadata, "version", _raise_not_found)
    code = cli_main(["version"])
    assert code == 0
    out = capsys.readouterr().out
    assert "openagents unknown" in out


def test_summary_dict_shape():
    data = version_cmd._summary_dict()
    assert set(data) == {"sdk", "python", "extras", "builtin_plugin_counts"}
    assert all(isinstance(seam, str) for seam in data["builtin_plugin_counts"])
    assert all(isinstance(count, int) for count in data["builtin_plugin_counts"].values())


def test_render_rich_returns_none_when_rich_absent(monkeypatch):
    # _render_rich should gracefully return None so plain-text path fires.
    monkeypatch.setattr(version_cmd.importlib.util, "find_spec", lambda name: None)
    assert version_cmd._render_rich(version_cmd._summary_dict()) is None


def test_render_plain_non_verbose_lists_extras():
    text = version_cmd._render_plain(
        {
            "sdk": "0.0.0",
            "python": "3.12.0",
            "extras": [],
            "builtin_plugin_counts": {},
        },
        verbose=False,
    )
    assert "(none)" in text


@pytest.mark.parametrize("argv", [["version", "--verbose"], ["version", "--format", "json"]])
def test_version_returns_zero_exit_code(argv, capsys):
    assert cli_main(argv) == 0
