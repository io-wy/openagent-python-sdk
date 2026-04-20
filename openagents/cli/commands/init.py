"""``openagents init`` — scaffold a new project from a bundled template.

Templates are inlined as Python string literals so no package-data glue
is required. Each bundled template must produce an ``agent.json`` that
passes ``openagents validate`` immediately (enforced by tests).

Placeholders: ``{{ project_name }}``, ``{{ provider }}``, and
``{{ api_key_env }}`` are replaced verbatim by the user's answers to
the prompts (or the ``--provider`` / ``--api-key-env`` flags when
``--yes`` was passed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openagents.cli._exit import EXIT_OK, EXIT_USAGE
from openagents.cli._fallback import require_or_hint

_SUPPORTED_TEMPLATES = ("minimal", "coding-agent", "pptx-wizard")
_SUPPORTED_PROVIDERS = ("anthropic", "openai-compatible", "mock")

# ---------------------------------------------------------------- templates

_README_TEMPLATE = """# {{ project_name }}

Scaffolded with `openagents init`. Run with:

```bash
openagents validate ./agent.json
openagents run ./agent.json --input "hello"
```

Provider: `{{ provider }}`
API-key env var: `{{ api_key_env }}`
"""


_AGENT_MINIMAL = """{
  "version": "1.0",
  "agents": [
    {
      "id": "assistant",
      "name": "{{ project_name }}",
      "memory": {"type": "window_buffer", "config": {"window_size": 10}},
      "pattern": {"type": "react", "config": {"max_steps": 4}},
      "llm": {
        "provider": "{{ provider }}",
        "model": "PLACEHOLDER_MODEL_NAME",
        "api_key_env": "{{ api_key_env }}",
        "temperature": 0
      },
      "tools": [],
      "runtime": {
        "max_steps": 8,
        "step_timeout_ms": 15000,
        "session_queue_size": 100,
        "event_queue_size": 200
      }
    }
  ]
}
"""


_AGENT_CODING = """{
  "version": "1.0",
  "agents": [
    {
      "id": "coder",
      "name": "{{ project_name }}",
      "memory": {"type": "window_buffer", "config": {"window_size": 30}},
      "pattern": {"type": "react", "config": {"max_steps": 16}},
      "llm": {
        "provider": "{{ provider }}",
        "model": "PLACEHOLDER_MODEL_NAME",
        "api_key_env": "{{ api_key_env }}",
        "temperature": 0
      },
      "tools": [
        {"id": "search", "type": "builtin_search", "config": {}},
        {"id": "shell", "type": "shell_exec", "config": {"timeout_ms": 30000}}
      ],
      "runtime": {
        "max_steps": 32,
        "step_timeout_ms": 60000,
        "session_queue_size": 1000,
        "event_queue_size": 2000
      }
    }
  ]
}
"""


_AGENT_PPTX = """{
  "version": "1.0",
  "events": {"type": "async"},
  "agents": [
    {
      "id": "intent-analyst",
      "name": "{{ project_name }} · intent",
      "memory": {
        "type": "chain",
        "on_error": "continue",
        "config": {
          "memories": [
            {"type": "window_buffer", "config": {"window_size": 12}},
            {"type": "markdown_memory", "config": {"memory_dir": "./memory"}}
          ]
        }
      },
      "pattern": {"type": "react", "config": {"max_steps": 3}},
      "context_assembler": {"type": "truncating", "config": {"max_messages": 8}},
      "llm": {
        "provider": "{{ provider }}",
        "model": "PLACEHOLDER_MODEL_NAME",
        "api_key_env": "{{ api_key_env }}",
        "temperature": 0.3
      },
      "tools": [],
      "runtime": {
        "max_steps": 8,
        "step_timeout_ms": 30000,
        "session_queue_size": 500,
        "event_queue_size": 1000
      }
    },
    {
      "id": "slide-generator",
      "name": "{{ project_name }} · slides",
      "memory": {
        "type": "chain",
        "on_error": "continue",
        "config": {
          "memories": [
            {"type": "window_buffer", "config": {"window_size": 12}},
            {"type": "markdown_memory", "config": {"memory_dir": "./memory"}}
          ]
        }
      },
      "pattern": {"type": "react", "config": {"max_steps": 2}},
      "context_assembler": {"type": "truncating", "config": {"max_messages": 6}},
      "llm": {
        "provider": "{{ provider }}",
        "model": "PLACEHOLDER_MODEL_NAME",
        "api_key_env": "{{ api_key_env }}",
        "temperature": 0.3
      },
      "tools": [],
      "runtime": {
        "max_steps": 8,
        "step_timeout_ms": 30000,
        "session_queue_size": 500,
        "event_queue_size": 1000
      }
    }
  ]
}
"""


_PPTX_README = """# {{ project_name }}

Scaffolded with `openagents init --template pptx-wizard`. Two-agent slice
(intent analyst + slide generator) wired with `chain` memory
(`window_buffer` + `markdown_memory`) and `truncating` context assembler.

Run either agent directly:

```bash
openagents validate ./agent.json
openagents run ./agent.json --input "hello" --agent intent-analyst
```

Provider: `{{ provider }}`
API-key env var: `{{ api_key_env }}`

## See the full pipeline

This scaffold is a minimal slice of the flagship PPT example in the
OpenAgents SDK repo. For the complete 7-stage wizard (env doctor,
research via Tavily MCP, outline, theme gallery, slide retry/fallback,
compile via PptxGenJS, QA via MarkItDown), clone the SDK and see
`examples/pptx_generator/README.md`.
"""


_TEMPLATE_FILES: dict[str, dict[str, str]] = {
    "minimal": {
        "agent.json": _AGENT_MINIMAL,
        "README.md": _README_TEMPLATE,
    },
    "coding-agent": {
        "agent.json": _AGENT_CODING,
        "README.md": _README_TEMPLATE,
    },
    "pptx-wizard": {
        "agent.json": _AGENT_PPTX,
        "README.md": _PPTX_README,
    },
}


def _render(text: str, substitutions: dict[str, str]) -> str:
    rendered = text
    for key, value in substitutions.items():
        rendered = rendered.replace("{{ " + key + " }}", value)
    return rendered


def _target_dir_is_safe(target: Path, *, force: bool) -> tuple[bool, str | None]:
    if not target.exists():
        return True, None
    if not target.is_dir():
        return False, f"path exists and is not a directory: {target}"
    non_hidden = [child for child in target.iterdir() if not child.name.startswith(".")]
    if non_hidden and not force:
        return False, f"directory exists and is not empty: {target} (pass --force to overwrite)"
    return True, None


def _collect_interactive(args: argparse.Namespace) -> argparse.Namespace:
    questionary = require_or_hint("questionary")
    if questionary is None:
        # Non-interactive fallback — user did not pass --yes but questionary
        # isn't installed, so we take the declared defaults.
        return args
    if args.template is None:
        args.template = str(
            questionary.select(
                "Template:",
                choices=list(_SUPPORTED_TEMPLATES),
                default="minimal",
            ).ask()
        )
    if args.provider is None:
        args.provider = str(
            questionary.select(
                "Provider:",
                choices=list(_SUPPORTED_PROVIDERS),
                default="mock",
            ).ask()
        )
    if args.api_key_env is None:
        args.api_key_env = str(
            questionary.text(
                "API-key env var name:",
                default=_default_api_key_env(args.provider),
            ).ask()
        )
    return args


def _default_api_key_env(provider: str) -> str:
    return {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai-compatible": "OPENAI_API_KEY",
        "mock": "MOCK_API_KEY",
    }.get(provider, "API_KEY")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "init",
        help="scaffold a new OpenAgents project",
        description="Scaffold a new project from a bundled template.",
    )
    p.add_argument("name", help="directory name for the new project")
    p.add_argument(
        "--template",
        choices=_SUPPORTED_TEMPLATES,
        default=None,
        help="bundled template (default: minimal)",
    )
    p.add_argument(
        "--provider",
        choices=_SUPPORTED_PROVIDERS,
        default=None,
        help="LLM provider to target (default: mock for --yes, prompted otherwise)",
    )
    p.add_argument(
        "--api-key-env",
        dest="api_key_env",
        default=None,
        help="environment variable name holding the API key",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="skip interactive prompts and use declared / default values",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite files in an existing non-empty directory",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    if not args.yes:
        args = _collect_interactive(args)
    if args.template is None:
        args.template = "minimal"
    if args.provider is None:
        args.provider = "mock"
    if args.api_key_env is None:
        args.api_key_env = _default_api_key_env(args.provider)

    files = _TEMPLATE_FILES.get(args.template)
    if files is None:  # pragma: no cover - guarded by argparse choices
        print(f"unknown template: {args.template}", file=sys.stderr)
        return EXIT_USAGE

    target = Path(args.name).resolve()
    ok, reason = _target_dir_is_safe(target, force=args.force)
    if not ok:
        print(reason, file=sys.stderr)
        return EXIT_USAGE
    target.mkdir(parents=True, exist_ok=True)

    substitutions = {
        "project_name": target.name,
        "provider": args.provider,
        "api_key_env": args.api_key_env,
    }
    for filename, contents in files.items():
        out_path = target / filename
        out_path.write_text(
            _render(contents, substitutions),
            encoding="utf-8",
        )

    # Sanity-check the generated agent.json against AppConfig.
    agent_path = target / "agent.json"
    try:
        json.loads(agent_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"generated agent.json is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_USAGE

    print(f"scaffolded {args.template} project at {target}")
    print("next: openagents validate " + str(agent_path))
    return EXIT_OK
