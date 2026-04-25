"""``openagents new plugin`` — scaffold a plugin skeleton.

Produces a class-based plugin stub plus a matching test file for any of
the recognised seams: ``tool``, ``memory``, ``pattern``,
``context_assembler``, ``tool_executor``, ``events``, ``session``,
``runtime``, ``skills``. The scaffold does NOT auto-register the plugin
— the user must add it to a config's ``impl:`` field or import the
module before config load, matching the documented contract in
:mod:`openagents.plugins.loader`.

Templates are inlined as heredoc strings so no external template files
have to be shipped. Naming conventions:

* Module filename: snake_case of the user-provided name.
* Class name: PascalCase of the name, suffixed by the seam's conventional
  suffix (``Tool``, ``Memory``, ``Pattern``, etc.).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from openagents.cli._exit import EXIT_OK, EXIT_USAGE
from openagents.plugins.registry import _BUILTIN_REGISTRY

_VALID_SEAMS = sorted(set(_BUILTIN_REGISTRY.keys()) | {"tool"})

_CLASS_SUFFIX: dict[str, str] = {
    "tool": "Tool",
    "memory": "Memory",
    "pattern": "Pattern",
    "context_assembler": "Assembler",
    "tool_executor": "ToolExecutor",
    "events": "EventBus",
    "session": "Session",
    "runtime": "Runtime",
    "skills": "Skills",
}


def _snake(name: str) -> str:
    """Return a lower_snake_case module stem for *name*."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", cleaned)
    return cleaned.lower() or "plugin"


def _pascal(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p) or "Plugin"


def _class_name(seam: str, name: str) -> str:
    stem = _pascal(name)
    suffix = _CLASS_SUFFIX.get(seam, "")
    return stem if stem.endswith(suffix) else stem + suffix


# ---------------------------------------------------------------- templates

_TEMPLATE_TOOL = '''"""{class_name} — scaffolded tool plugin.

Register via config::

    "tools": [{{"impl": "{module_dotted}.{class_name}"}}]
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openagents.interfaces.tool import ToolPlugin


class {class_name}(ToolPlugin):
    """One-line description of what this tool does."""

    name = "{tool_name}"
    description = "TODO: describe {tool_name}"

    class Config(BaseModel):
        # TODO: declare tool-specific configuration fields.
        pass

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        # TODO: implement the tool body.
        return {{"ok": True, "params": params}}
'''

_TEMPLATE_MEMORY = '''"""{class_name} — scaffolded memory plugin."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openagents.interfaces.memory import MemoryPlugin


class {class_name}(MemoryPlugin):
    """One-line description of what this memory plugin does."""

    class Config(BaseModel):
        pass

    async def inject(self, context: Any) -> None:
        # TODO: populate context.memory_view from storage.
        return None

    async def writeback(self, context: Any) -> None:
        # TODO: persist any new memory entries produced during the run.
        return None

    async def retrieve(self, query: str, context: Any) -> list[dict[str, Any]]:
        # TODO: optional — return items relevant to the query.
        return []

    async def close(self) -> None:
        return None
'''

_TEMPLATE_PATTERN = '''"""{class_name} — scaffolded pattern plugin."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openagents.interfaces.pattern import PatternPlugin


class {class_name}(PatternPlugin):
    """One-line description of what this pattern does."""

    class Config(BaseModel):
        max_steps: int = 3

    def __init__(self, config: dict[str, Any] | None = None):
        # PatternPlugin 基类自动注入 pattern.execute + pattern.react
        super().__init__(config=config or {{}})

    async def execute(self) -> Any:
        ctx = self.context
        if ctx is None:
            raise RuntimeError("{class_name}.context is not set; call setup() first")
        # TODO: drive the pattern using ctx.llm_client / ctx.call_tool.
        return ctx.input_text
'''

_TEMPLATE_CONTEXT_ASSEMBLER = '''"""{class_name} — scaffolded context assembler plugin."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openagents.interfaces.context import ContextAssemblerPlugin, ContextAssemblyResult


class {class_name}(ContextAssemblerPlugin):
    """One-line description of what this assembler does."""

    class Config(BaseModel):
        pass

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {{}})

    async def assemble(
        self,
        *,
        request: Any,
        session_state: dict[str, Any],
        session_manager: Any,
    ) -> ContextAssemblyResult:
        # TODO: derive assembled messages / metadata for the upcoming run.
        return ContextAssemblyResult(messages=[], metadata={{}})
'''

_TEMPLATE_TOOL_EXECUTOR = '''"""{class_name} — scaffolded tool-executor plugin."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openagents.interfaces.tool import (
    PolicyDecision,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutorPlugin,
)


class {class_name}(ToolExecutorPlugin):
    """One-line description of what this executor does."""

    class Config(BaseModel):
        pass

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {{}})

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        return PolicyDecision(allowed=True)

    async def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        # TODO: invoke the tool, collect timing / errors, return result.
        raise NotImplementedError
'''

_TEMPLATE_GENERIC = '''"""{class_name} — scaffolded {seam} plugin.

NOTE: the {seam} seam's interface isn't covered by a dedicated template.
Start from :class:`openagents.interfaces.plugin.BasePlugin` and add the
methods your runtime requires.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openagents.interfaces.plugin import BasePlugin


class {class_name}(BasePlugin):
    """One-line description of what this {seam} plugin does."""

    class Config(BaseModel):
        pass

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {{}})
'''

_TEMPLATES: dict[str, str] = {
    "tool": _TEMPLATE_TOOL,
    "memory": _TEMPLATE_MEMORY,
    "pattern": _TEMPLATE_PATTERN,
    "context_assembler": _TEMPLATE_CONTEXT_ASSEMBLER,
    "tool_executor": _TEMPLATE_TOOL_EXECUTOR,
}

_TEST_TEMPLATE = '''"""Auto-generated test stub for {class_name}.

Asserts that the scaffolded module imports cleanly and that its
``Config`` model accepts an empty payload. Replace with real coverage
as you flesh the plugin out.
"""

from __future__ import annotations

import importlib


def test_{module_stem}_module_imports():
    mod = importlib.import_module("{module_dotted}")
    assert hasattr(mod, "{class_name}")


def test_{module_stem}_config_accepts_empty_payload():
    mod = importlib.import_module("{module_dotted}")
    cls = getattr(mod, "{class_name}")
    cfg = cls.Config.model_validate({{}})
    assert cfg is not None
'''


def _render_plugin(seam: str, class_name: str, tool_name: str, module_dotted: str) -> str:
    template = _TEMPLATES.get(seam, _TEMPLATE_GENERIC)
    return template.format(
        class_name=class_name,
        seam=seam,
        tool_name=tool_name,
        module_dotted=module_dotted,
    )


def _render_test(module_stem: str, class_name: str, module_dotted: str) -> str:
    return _TEST_TEMPLATE.format(
        module_stem=module_stem,
        class_name=class_name,
        module_dotted=module_dotted,
    )


def _derive_module_dotted(path: Path) -> str:
    """Turn a filesystem path into a best-effort dotted import path.

    Strips a leading ``./`` and the ``.py`` suffix, replaces separators
    with ``.``. This is a heuristic — the user may need to adjust their
    PYTHONPATH; the test stub re-derives it from the import attempt.
    """
    rel = path.with_suffix("")
    parts = [p for p in rel.parts if p not in ("", ".")]
    return ".".join(parts)


def _plugin_dispatch(args: argparse.Namespace) -> int:
    seam = args.seam
    name = args.name
    if seam not in _VALID_SEAMS:
        print(
            f"unknown seam: {seam}. Valid seams: {', '.join(_VALID_SEAMS)}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    module_stem = _snake(name)
    class_name = _class_name(seam, name)
    target = Path(args.path) if args.path else Path("plugins") / f"{module_stem}.py"
    if target.exists() and not args.force:
        print(
            f"refusing to overwrite existing file: {target} (pass --force)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    target.parent.mkdir(parents=True, exist_ok=True)
    module_dotted = _derive_module_dotted(target)
    target.write_text(
        _render_plugin(seam, class_name, module_stem, module_dotted),
        encoding="utf-8",
    )
    print(f"wrote plugin: {target}")

    if not args.no_test:
        test_path = Path("tests") / "unit" / f"test_{module_stem}.py"
        test_path.parent.mkdir(parents=True, exist_ok=True)
        if test_path.exists() and not args.force:
            print(
                f"skipping test stub (already exists): {test_path} (pass --force)",
                file=sys.stderr,
            )
        else:
            test_path.write_text(
                _render_test(module_stem, class_name, module_dotted),
                encoding="utf-8",
            )
            print(f"wrote test stub: {test_path}")
    return EXIT_OK


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "new",
        help="scaffold a new plugin or project asset",
        description="Scaffold a new plugin skeleton.",
    )
    nested = p.add_subparsers(dest="new_target")
    plugin = nested.add_parser("plugin", help="scaffold a plugin skeleton")
    plugin.add_argument(
        "seam",
        help=f"plugin seam (one of: {', '.join(_VALID_SEAMS)})",
    )
    plugin.add_argument("name", help="plugin name (e.g. my_calculator)")
    plugin.add_argument("--path", help="override output path for the plugin module")
    plugin.add_argument(
        "--no-test",
        action="store_true",
        help="skip writing the tests/unit/test_<name>.py stub",
    )
    plugin.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing files if they conflict",
    )
    plugin.set_defaults(func=run)
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    target = getattr(args, "new_target", None)
    if target is None:
        print("usage: openagents new plugin <seam> <name> [--path PATH]", file=sys.stderr)
        return EXIT_USAGE
    if target != "plugin":  # pragma: no cover - guarded by argparse choices
        print(f"unknown target: {target}", file=sys.stderr)
        return EXIT_USAGE
    return _plugin_dispatch(args)
