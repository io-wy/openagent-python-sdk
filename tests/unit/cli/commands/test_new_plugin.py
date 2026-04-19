"""Tests for ``openagents new plugin``.

We scaffold a plugin per seam and verify:

* The generated module imports cleanly (``importlib`` + generated test).
* The generated test stub passes when pytest is pointed at it.
* Unknown seam names exit ``1`` with the valid set listed.
* ``--force`` / refusal on existing files behaves as specified.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from openagents.cli.commands import new as new_cmd
from openagents.cli.main import main as cli_main


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "seam",
    [
        "tool",
        "memory",
        "pattern",
        "context_assembler",
        "tool_executor",
        "events",  # falls through to the generic template
    ],
)
def test_new_plugin_generates_importable_module(seam, workdir):
    code = cli_main(["new", "plugin", seam, "My_Widget"])
    assert code == 0
    module_path = workdir / "plugins" / "my_widget.py"
    assert module_path.exists()
    # Run the generated test stub via a subprocess so the import happens
    # with the tmp path on PYTHONPATH (matching how a user would run it).
    test_path = workdir / "tests" / "unit" / "test_my_widget.py"
    assert test_path.exists()
    env = {"PYTHONPATH": str(workdir)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_path)],
        capture_output=True,
        text=True,
        cwd=str(workdir),
        env={**env, **{k: v for k, v in __import__("os").environ.items()}},
        timeout=60,
    )
    assert result.returncode == 0, f"pytest failed:\n{result.stdout}\n{result.stderr}"


def test_new_plugin_unknown_seam_returns_1(workdir, capsys):
    code = cli_main(["new", "plugin", "not-a-seam", "foo"])
    assert code == 1
    err = capsys.readouterr().err
    # Every known seam must appear so the user knows what's valid.
    for seam in ("tool", "memory", "pattern", "context_assembler"):
        assert seam in err


def test_new_plugin_refuses_overwrite_without_force(workdir, capsys):
    # First scaffold — should succeed.
    cli_main(["new", "plugin", "tool", "dup_tool"])
    # Second scaffold — must refuse.
    code = cli_main(["new", "plugin", "tool", "dup_tool"])
    assert code == 1
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err


def test_new_plugin_force_overwrites(workdir):
    cli_main(["new", "plugin", "tool", "forced_tool"])
    # Mutate the module so we can detect the overwrite.
    module_path = workdir / "plugins" / "forced_tool.py"
    module_path.write_text("# stale\n", encoding="utf-8")
    code = cli_main(["new", "plugin", "tool", "forced_tool", "--force"])
    assert code == 0
    # The file is rewritten with the template, so "class ForcedTool" appears.
    assert "class ForcedTool" in module_path.read_text(encoding="utf-8")


def test_new_plugin_no_test_skips_test_stub(workdir):
    cli_main(["new", "plugin", "tool", "quiet_tool", "--no-test"])
    assert (workdir / "plugins" / "quiet_tool.py").exists()
    assert not (workdir / "tests" / "unit" / "test_quiet_tool.py").exists()


def test_new_plugin_custom_path(workdir):
    target = workdir / "src" / "custom" / "my_tool.py"
    code = cli_main(
        ["new", "plugin", "tool", "MyTool", "--path", str(target), "--no-test"]
    )
    assert code == 0
    assert target.exists()


def test_new_top_level_without_subcommand_prints_usage(workdir, capsys):
    code = cli_main(["new"])
    assert code == 1
    assert "usage: openagents new plugin" in capsys.readouterr().err


def test_snake_and_pascal_helpers():
    assert new_cmd._snake("MyWidgetTool") == "my_widget_tool"
    assert new_cmd._snake("foo-bar baz") == "foo_bar_baz"
    assert new_cmd._pascal("my_tool_name") == "MyToolName"
    assert new_cmd._class_name("tool", "calc") == "CalcTool"
    # Avoid double-suffixing.
    assert new_cmd._class_name("tool", "MyTool") == "MyTool"
