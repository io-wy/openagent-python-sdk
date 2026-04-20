"""pptx-agent CLI entry point.

This file exposes ``main`` (async) and ``main_sync`` (entry point for
``project.scripts``). The ``run_wizard`` function drives the full 7-step
wizard pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None

from openagents.cli.wizard import Wizard
from openagents.plugins.builtin.tool.shell_exec import ShellExecTool
from openagents.runtime.runtime import Runtime as _Runtime
from openagents.utils.env_doctor import (
    CliBinaryCheck,
    EnvironmentDoctor,
    EnvVarCheck,
    NodeVersionCheck,
    NpmCheck,
    PythonVersionCheck,
)

from .persistence import (
    ProjectCorruptedError,
    backup_path,
    load_project,
    restore_from_backup,
    save_project,
)
from .state import DeckProject
from .wizard.compile_qa import CompileQAWizardStep
from .wizard.env import EnvDoctorWizardStep
from .wizard.intent import IntentWizardStep
from .wizard.outline import OutlineWizardStep
from .wizard.research import ResearchWizardStep
from .wizard.slides import SlideGeneratorWizardStep
from .wizard.theme import ThemeWizardStep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pptx-agent",
        description="Interactive PPT generator built on openagents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="start a new deck")
    p_new.add_argument("--topic", help="initial topic prompt (optional)")
    p_new.add_argument("--slug", help="override project slug")

    p_resume = sub.add_parser("resume", help="resume an existing deck by slug")
    p_resume.add_argument("slug")

    p_memory = sub.add_parser("memory", help="inspect or manage persisted memory entries")
    mem_sub = p_memory.add_subparsers(dest="memory_cmd")
    p_mem_list = mem_sub.add_parser("list", help="list persisted memory entries (default)")
    p_mem_list.add_argument(
        "--section",
        default=None,
        help="limit to a specific section (user_goals|user_feedback|decisions|references)",
    )
    p_mem_forget = mem_sub.add_parser("forget", help="remove a memory entry by id")
    p_mem_forget.add_argument("entry_id")
    # Back-compat: `pptx-agent memory --section X` without the subcommand still lists
    p_memory.add_argument(
        "--section",
        default=None,
        help="limit list to a specific section (deprecated: use `memory list --section`)",
    )
    return parser


_SLUG_CHAR_RE = re.compile(r"[^a-z0-9]+")
_MAX_BASE_LEN = 48  # leaves room for "-YYYYMMDD-HHMMSS" (16 chars) within 64-char limit


def _slugify(topic: str | None) -> str:
    base = _SLUG_CHAR_RE.sub("-", (topic or "deck").lower()).strip("-") or "deck"
    base = base[:_MAX_BASE_LEN].rstrip("-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{base}-{stamp}"


def _load_env_files() -> None:
    """Load user-level .env file if python-dotenv is installed."""
    if _load_dotenv is None:
        return
    user_env = Path("~/.config/pptx-agent/.env").expanduser()
    if user_env.exists():
        _load_dotenv(user_env, override=False)


def outputs_root() -> Path:
    return Path(os.environ.get("PPTX_AGENT_OUTPUTS", "examples/pptx_generator/outputs"))


async def run_wizard(
    project: DeckProject,
    *,
    resume: bool = False,
    topic: str | None = None,
    runtime=None,
    shell_tool=None,
) -> int:
    """Drive the full 7-step wizard pipeline.

    Parameters
    ----------
    project:
        The :class:`DeckProject` to work on (mutated in-place as stages advance).
    resume:
        When *True*, start from ``project.stage`` rather than the first step.
    runtime:
        Injectable runtime (for testing). If *None*, constructed from
        ``examples/pptx_generator/agent.json``.
    shell_tool:
        Injectable shell tool (for testing). If *None*, a :class:`ShellExecTool`
        with a safe command allowlist is constructed.
    """
    outputs = outputs_root()
    project_dir = outputs / project.slug
    project_dir.mkdir(parents=True, exist_ok=True)
    save_project(project, root=outputs)

    if resume and project.stage == "done":
        print(f"Project {project.slug!r} is already complete.")
        return 0

    prior_log = os.environ.get("PPTX_EVENTS_LOG")
    if prior_log is None:
        os.environ["PPTX_EVENTS_LOG"] = str(project_dir / "events.jsonl")

    if runtime is None:
        runtime = _Runtime.from_config(Path("examples/pptx_generator/agent.json"))
    if shell_tool is None:
        shell_tool = ShellExecTool(
            config={
                "command_allowlist": ["node", "npx", "npm", "markitdown"],
                "env_passthrough": ["PATH", "HOME", "APPDATA", "USERPROFILE"],
                "default_timeout_ms": 300_000,
            }
        )

    doctor = EnvironmentDoctor(
        checks=[
            PythonVersionCheck(min_version="3.10"),
            NodeVersionCheck(min_version="18"),
            NpmCheck(),
            CliBinaryCheck(
                name="markitdown",
                install_hint="pip install 'markitdown[all]'",
                get_url="https://pypi.org/project/markitdown/",
                required=False,
            ),
            EnvVarCheck(
                name="LLM_API_KEY",
                required=True,
                description="LLM API key",
                get_url="https://docs.anthropic.com/",
            ),
            EnvVarCheck(
                name="LLM_API_BASE",
                required=True,
                description="LLM base URL",
                get_url=None,
            ),
            EnvVarCheck(
                name="LLM_MODEL",
                required=True,
                description="LLM model name",
                get_url=None,
            ),
            EnvVarCheck(
                name="TAVILY_API_KEY",
                required=False,
                description="Tavily API key",
                get_url="https://tavily.com/",
            ),
        ],
        dotenv_paths=[
            Path(outputs) / project.slug / ".env",
            Path("~/.config/pptx-agent/.env").expanduser(),
        ],
    )

    steps = [
        IntentWizardStep(runtime=runtime, topic_hint=topic),
        EnvDoctorWizardStep(doctor=doctor),
        ResearchWizardStep(runtime=runtime),
        OutlineWizardStep(runtime=runtime),
        ThemeWizardStep(runtime=runtime),
        SlideGeneratorWizardStep(runtime=runtime, concurrency=3),
        CompileQAWizardStep(
            shell_tool=shell_tool,
            output_root=outputs,
            templates_dir=Path("examples/pptx_generator/templates"),
        ),
    ]

    wizard = Wizard(steps=steps, project=project)
    try:
        if resume:
            outcome = await wizard.resume(from_step=project.stage)
        else:
            outcome = await wizard.run()
    except KeyboardInterrupt:
        save_project(project, root=outputs)
        print(
            f"\ninterrupted; state saved. resume with: pptx-agent resume {project.slug}",
        )
        return 130
    finally:
        if prior_log is None:
            os.environ.pop("PPTX_EVENTS_LOG", None)
    save_project(project, root=outputs)
    return 0 if outcome == "completed" else 1


async def main(argv: Sequence[str] | None = None) -> int:
    _load_env_files()
    args = build_parser().parse_args(argv)
    if args.command == "new":
        slug = args.slug or _slugify(args.topic)
        project = DeckProject(
            slug=slug,
            created_at=datetime.now(timezone.utc),
            stage="intent",
        )
        save_project(project, root=outputs_root())
        return await run_wizard(project, topic=args.topic)
    if args.command == "resume":
        project = _load_or_restore(args.slug)
        if project is None:
            return 1
        return await run_wizard(project, resume=True)
    if args.command == "memory":
        return _dispatch_memory(args)
    return 1


def _load_or_restore(slug: str) -> DeckProject | None:
    try:
        return load_project(slug, root=outputs_root())
    except FileNotFoundError:
        print(f"no project found for slug: {slug}", file=sys.stderr)
        return None
    except ProjectCorruptedError as exc:
        print(f"project.json is corrupt: {exc.detail}", file=sys.stderr)
        return _interactive_restore(slug)


def _interactive_restore(slug: str) -> DeckProject | None:
    root = outputs_root()
    backup = backup_path(slug, root=root)
    if not backup.exists():
        print(
            "no backup available; delete the project directory and run `pptx-agent new`.",
            file=sys.stderr,
        )
        return None
    print(
        f"\nbackup available at {backup}. choose:\n"
        f"  1) restore from backup\n"
        f"  2) start fresh (delete project.json)\n"
        f"  3) abort\n",
        file=sys.stderr,
    )
    try:
        choice = input("selection [1/2/3]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if choice == "1":
        return restore_from_backup(slug, root=root)
    if choice == "2":
        from pathlib import Path as _P

        _P(root / slug / "project.json").unlink(missing_ok=True)
        print("project.json deleted; rerun `pptx-agent new` to start fresh.", file=sys.stderr)
        return None
    return None


def _dispatch_memory(args: Any) -> int:
    from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory

    mem = MarkdownMemory(config={"memory_dir": "~/.config/pptx-agent/memory"})
    sub_cmd = getattr(args, "memory_cmd", None)
    if sub_cmd == "forget":
        ok = mem.forget(args.entry_id)
        if ok:
            print(f"forgot {args.entry_id}")
            return 0
        print(f"entry not found: {args.entry_id}", file=sys.stderr)
        return 1
    # default: list (explicit `list` or back-compat `memory --section`)
    section = getattr(args, "section", None)
    sections = [section] if section else mem.cfg.sections
    for s in sections:
        print(f"## {s}")
        for e in mem.list_entries(s):
            print(f"- [{e['id']}] {e['rule']}  — {e['reason']}")
    return 0


def main_sync() -> int:
    return asyncio.run(main(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main_sync())
