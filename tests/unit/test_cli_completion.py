"""Tests for ``openagents completion``.

We verify:

* Every supported shell produces non-empty output containing each
  subcommand name (so newly-registered commands in
  ``commands.__init__.COMMANDS`` will transparently show up).
* The bash script is syntactically parseable via ``bash -n`` **when
  bash is available** on the host. Skip gracefully on hosts (e.g.
  bare Windows) without bash.
* Unsupported shell names are rejected with exit ``1`` (argparse raises
  SystemExit before our handler runs, so we expect that).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from openagents.cli.commands import completion as completion_cmd
from openagents.cli.main import main as cli_main


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "powershell"])
def test_completion_emits_output_for_every_supported_shell(shell, capsys):
    code = cli_main(["completion", shell])
    assert code == 0
    out = capsys.readouterr().out
    assert out, f"{shell} produced no output"
    # Every known subcommand appears somewhere in the script.
    assert "schema" in out
    assert "validate" in out
    assert "list-plugins" in out
    assert "version" in out


def test_completion_bash_script_is_syntactically_valid(capsys):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not installed on this host")
    cli_main(["completion", "bash"])
    script = capsys.readouterr().out
    result = subprocess.run(
        [bash, "-n"],
        input=script,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_completion_unknown_shell_rejected(capsys):
    with pytest.raises(SystemExit) as ei:
        cli_main(["completion", "tcsh"])
    # argparse returns 2 for choice violations.
    assert ei.value.code == 2
    err = capsys.readouterr().err
    # Standard argparse error mentions the valid choices.
    for shell in ("bash", "zsh", "fish", "powershell"):
        assert shell in err


def test_walk_tree_finds_all_registered_subcommands():
    root_flags, subs, sub_flags = completion_cmd._walk_tree()
    assert "--help" in root_flags
    # All COMMANDS members from the registry are present.
    from openagents.cli.commands import COMMANDS

    for name in COMMANDS:
        assert name in subs, f"{name} missing from completion tree"
        assert name in sub_flags


def test_fish_script_includes_subcommand_completion_line(capsys):
    cli_main(["completion", "fish"])
    out = capsys.readouterr().out
    assert "__fish_use_subcommand" in out
    assert "__fish_seen_subcommand_from schema" in out


def test_powershell_script_registers_argument_completer(capsys):
    cli_main(["completion", "powershell"])
    out = capsys.readouterr().out
    assert "Register-ArgumentCompleter" in out
    assert "openagents" in out


def test_zsh_script_declares_compdef(capsys):
    cli_main(["completion", "zsh"])
    out = capsys.readouterr().out
    assert "#compdef openagents" in out


def test_renderers_dispatch_covers_every_supported_shell():
    assert set(completion_cmd._RENDERERS) == set(completion_cmd._SUPPORTED_SHELLS)
