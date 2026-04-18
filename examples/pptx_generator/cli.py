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
from typing import Sequence

from openagents.cli.wizard import Wizard
from openagents.plugins.builtin.tool.shell_exec import ShellExecTool
from openagents.runtime.runtime import Runtime as _Runtime
from openagents.utils.env_doctor import (
    CliBinaryCheck,
    EnvVarCheck,
    EnvironmentDoctor,
    NodeVersionCheck,
    NpmCheck,
    PythonVersionCheck,
)

from .persistence import load_project, save_project
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

    p_memory = sub.add_parser("memory", help="list persisted memory entries")
    p_memory.add_argument(
        "--section",
        default=None,
        help="limit to a specific section (user_goals|user_feedback|decisions|references)",
    )
    return parser


_SLUG_CHAR_RE = re.compile(r"[^a-z0-9]+")
_MAX_BASE_LEN = 48  # leaves room for "-YYYYMMDD-HHMMSS" (16 chars) within 64-char limit


def _slugify(topic: str | None) -> str:
    base = _SLUG_CHAR_RE.sub("-", (topic or "deck").lower()).strip("-") or "deck"
    base = base[:_MAX_BASE_LEN].rstrip("-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{base}-{stamp}"


def outputs_root() -> Path:
    return Path(os.environ.get("PPTX_AGENT_OUTPUTS", "examples/pptx_generator/outputs"))


async def run_wizard(
    project: DeckProject,
    *,
    resume: bool = False,
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
    save_project(project, root=outputs)

    if runtime is None:
        runtime = _Runtime.from_config(
            Path("examples/pptx_generator/agent.json")
        )
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
        IntentWizardStep(runtime=runtime, topic_hint=None),
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
    if resume:
        outcome = await wizard.resume(from_step=project.stage)
    else:
        outcome = await wizard.run()
    save_project(project, root=outputs)
    return 0 if outcome == "completed" else 1


async def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "new":
        slug = args.slug or _slugify(args.topic)
        project = DeckProject(
            slug=slug,
            created_at=datetime.now(timezone.utc),
            stage="intent",
        )
        save_project(project, root=outputs_root())
        return await run_wizard(project)
    if args.command == "resume":
        project = load_project(args.slug, root=outputs_root())
        return await run_wizard(project, resume=True)
    if args.command == "memory":
        from openagents.plugins.builtin.memory.markdown_memory import MarkdownMemory

        mem = MarkdownMemory(config={"memory_dir": "~/.config/pptx-agent/memory"})
        sections = [args.section] if args.section else mem.cfg.sections
        for s in sections:
            print(f"## {s}")
            for e in mem.list_entries(s):
                print(f"- [{e['id']}] {e['rule']}  — {e['reason']}")
        return 0
    return 1


def main_sync() -> int:
    return asyncio.run(main(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main_sync())
