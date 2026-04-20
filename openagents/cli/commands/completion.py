"""``openagents completion`` — emit shell-completion scripts.

Supported shells: ``bash``, ``zsh``, ``fish``, ``powershell``. The
completion tree is derived at invocation time from the root argparse
parser, so newly-registered subcommands show up automatically without
the scripts needing to be regenerated.

Install by piping into the appropriate location:

    # Bash (global)
    openagents completion bash | sudo tee /etc/bash_completion.d/openagents
    # Zsh
    openagents completion zsh  > ~/.zsh/completions/_openagents
    # Fish
    openagents completion fish > ~/.config/fish/completions/openagents.fish
    # PowerShell
    openagents completion powershell >> $PROFILE
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from openagents.cli._exit import EXIT_OK, EXIT_USAGE

_SUPPORTED_SHELLS = ("bash", "zsh", "fish", "powershell")


def _walk_tree() -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Return ``(root_flags, subcommands, subcommand_flags)`` for the live parser.

    Imported lazily inside the function to avoid a circular import — the
    root parser's construction includes this module via ``COMMANDS``.
    """
    from openagents.cli.main import build_parser  # local import avoids cycle

    parser = build_parser()
    root_flags = _collect_flags(parser)
    subcommands: list[str] = []
    sub_flags: dict[str, list[str]] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                subcommands.append(name)
                sub_flags[name] = _collect_flags(sub)
    return root_flags, sorted(set(subcommands)), sub_flags


def _collect_flags(parser: argparse.ArgumentParser) -> list[str]:
    flags: set[str] = set()
    for action in parser._actions:
        for option in action.option_strings:
            flags.add(option)
    return sorted(flags)


def _bash_script(root_flags: list[str], subcommands: list[str], sub_flags: dict[str, list[str]]) -> str:
    per_sub = "\n".join(
        f'        {name}) COMPREPLY=( $(compgen -W "{" ".join(flags)}" -- "$cur") ); return 0 ;;'
        for name, flags in sub_flags.items()
    )
    subcommand_list = " ".join(subcommands)
    root_flag_list = " ".join(root_flags)
    return f"""# bash completion for openagents
_openagents_completion() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "{subcommand_list} {root_flag_list}" -- "$cur") )
        return 0
    fi
    case "${{COMP_WORDS[1]}}" in
{per_sub}
    esac
    return 0
}}
complete -F _openagents_completion openagents
"""


def _zsh_script(root_flags: list[str], subcommands: list[str], sub_flags: dict[str, list[str]]) -> str:
    cmd_lines = "\n".join(
        f"      {name}) _arguments {' '.join(repr(f) for f in flags)} ;;" for name, flags in sub_flags.items()
    )
    sub_list = " ".join(subcommands)
    root_list = " ".join(root_flags)
    return f"""#compdef openagents
# zsh completion for openagents
_openagents() {{
  local state
  _arguments -C \\
    '1:command:(({sub_list}))' \\
    '*::arg:->args'
  case $state in
    args)
      case $words[1] in
{cmd_lines}
      esac
      ;;
  esac
  _values 'root' {root_list}
}}
compdef _openagents openagents
"""


def _fish_script(root_flags: list[str], subcommands: list[str], sub_flags: dict[str, list[str]]) -> str:
    lines: list[str] = ["# fish completion for openagents"]
    lines.append(f"complete -c openagents -n '__fish_use_subcommand' -a '{' '.join(subcommands)}'")
    for flag in root_flags:
        lines.append(f"complete -c openagents -n '__fish_use_subcommand' -l {flag.lstrip('-')}")
    for name, flags in sub_flags.items():
        for flag in flags:
            name_clean = flag.lstrip("-")
            if not name_clean:
                continue
            flag_kind = "l" if flag.startswith("--") else "s"
            lines.append(f"complete -c openagents -n '__fish_seen_subcommand_from {name}' -{flag_kind} {name_clean}")
    return "\n".join(lines) + "\n"


def _powershell_script(root_flags: list[str], subcommands: list[str], sub_flags: dict[str, list[str]]) -> str:
    sub_list = ", ".join(f"'{s}'" for s in subcommands)
    root_list = ", ".join(f"'{f}'" for f in root_flags)
    per_sub = "\n".join(
        f"        '{name}' {{ {', '.join(repr(f) for f in flags)} }}" for name, flags in sub_flags.items()
    )
    return f"""# PowerShell completion for openagents
Register-ArgumentCompleter -Native -CommandName openagents -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $commands = @({sub_list})
    $rootFlags = @({root_list})
    $tokens = $commandAst.CommandElements
    if ($tokens.Count -le 2) {{
        ($commands + $rootFlags) | Where-Object {{ $_ -like "$wordToComplete*" }}
        return
    }}
    $sub = $tokens[1].Value
    $flags = switch ($sub) {{
{per_sub}
        default {{ @() }}
    }}
    $flags | Where-Object {{ $_ -like "$wordToComplete*" }}
}}
"""


_RENDERERS: dict[str, Any] = {
    "bash": _bash_script,
    "zsh": _zsh_script,
    "fish": _fish_script,
    "powershell": _powershell_script,
}


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "completion",
        help="emit a shell-completion script",
        description=f"Emit a completion script for one of: {', '.join(_SUPPORTED_SHELLS)}.",
    )
    p.add_argument("shell", choices=_SUPPORTED_SHELLS, help="target shell")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    renderer = _RENDERERS.get(args.shell)
    if renderer is None:  # pragma: no cover - guarded by argparse choices
        print(
            f"unsupported shell: {args.shell}. Supported: {', '.join(_SUPPORTED_SHELLS)}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    root_flags, subcommands, sub_flags = _walk_tree()
    sys.stdout.write(renderer(root_flags, subcommands, sub_flags))
    return EXIT_OK
