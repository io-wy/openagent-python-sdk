"""Local filesystem-backed skills manager."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openagents.interfaces.skills import SessionSkillSummary, SkillsPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _parse_skill_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Skill package '{path.parent}' must start with YAML frontmatter")

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            metadata[key] = _strip_quotes(value)
    return metadata


def _parse_flat_yaml(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            data[key] = _strip_quotes(value)
    return data


class LocalSkillsManager(TypedConfigPluginMixin, SkillsPlugin):
    """Discover and execute repo-local skill packages.

    What:
        Walks ``search_paths`` for directories whose ``SKILL.md``
        opens with YAML frontmatter, treats each as a skill
        package, and exposes them through ``prepare_session`` /
        ``load_references`` / ``run_skill``. Skill payloads run by
        importing ``<package>.entrypoint:run_openagent_skill``.

    Usage:
        ``{"skills": {"type": "local", "config": {"search_paths":
        ["skills"], "enabled": ["my_skill"]}}}``

    Depends on:
        - the local filesystem under ``search_paths``
        - the host runtime's session manager (injected as
          ``self._session_manager``)
    """

    _STATE_KEY = "_session_skills"

    class Config(BaseModel):
        search_paths: list[str] = Field(default_factory=lambda: ["skills"])
        enabled: list[str] = Field(default_factory=list)

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities=set())
        self._init_typed_config()
        self._search_paths = [
            Path(path).resolve(strict=False) for path in self.cfg.search_paths if isinstance(path, str) and path.strip()
        ]
        self._enabled = {str(name).strip() for name in self.cfg.enabled if isinstance(name, str) and name.strip()}
        self._packages: dict[str, dict[str, Any]] = {}

    def _discover(self) -> dict[str, dict[str, Any]]:
        if self._packages:
            return self._packages

        packages: dict[str, dict[str, Any]] = {}
        for root in self._search_paths:
            if not root.exists():
                continue
            for item in sorted(root.iterdir()):
                if not item.is_dir():
                    continue
                skill_md = item / "SKILL.md"
                if not skill_md.exists():
                    continue
                frontmatter = _parse_skill_frontmatter(skill_md)
                name = frontmatter.get("name")
                description = frontmatter.get("description", "")
                if not name:
                    continue
                if self._enabled and name not in self._enabled:
                    continue
                openai_yaml = item / "agents" / "openai.yaml"
                interface = _parse_flat_yaml(openai_yaml if openai_yaml.exists() else None)
                entrypoints = list((item / "src").glob("*/entrypoint.py"))
                if len(entrypoints) != 1:
                    raise ValueError(f"Skill package '{item}' must contain exactly one src/<package>/entrypoint.py")
                references_root = item / "references"
                references = (
                    sorted(path for path in references_root.iterdir() if path.is_file())
                    if references_root.exists()
                    else []
                )
                packages[name] = {
                    "name": name,
                    "description": description,
                    "root": item,
                    "entrypoint_file": entrypoints[0],
                    "package_name": entrypoints[0].parent.name,
                    "display_name": interface.get("display_name", name),
                    "default_prompt": interface.get("default_prompt", description),
                    "references": references,
                }
        self._packages = packages
        return packages

    async def prepare_session(
        self,
        *,
        session_id: str,
        session_manager: Any,
    ) -> dict[str, SessionSkillSummary]:
        state = await session_manager.get_state(session_id)
        current = dict(state.get(self._STATE_KEY, {}))

        for name, package in self._discover().items():
            current.setdefault(
                name,
                asdict(
                    SessionSkillSummary(
                        name=name,
                        description=package["description"],
                        display_name=package["display_name"],
                        default_prompt=package["default_prompt"],
                    )
                ),
            )

        state[self._STATE_KEY] = current
        await session_manager.set_state(session_id, state)

        return {name: SessionSkillSummary(**payload) for name, payload in current.items() if isinstance(payload, dict)}

    async def load_references(
        self,
        *,
        session_id: str,
        skill_name: str,
        session_manager: Any,
    ) -> list[dict[str, str]]:
        packages = self._discover()
        package = packages.get(skill_name)
        if package is None:
            from openagents.errors.suggestions import near_match

            available = sorted(packages.keys())
            guess = near_match(skill_name, available)
            extra = f" Did you mean '{guess}'?" if guess else ""
            raise KeyError(f"Unknown skill package: '{skill_name}'.{extra} Available: {available}")

        state = await session_manager.get_state(session_id)
        current = dict(state.get(self._STATE_KEY, {}))
        skill_state = dict(current.get(skill_name, {}))
        loaded = [
            {
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            }
            for path in package["references"]
        ]
        skill_state["references_loaded"] = [item["path"] for item in loaded]
        current[skill_name] = skill_state
        state[self._STATE_KEY] = current
        await session_manager.set_state(session_id, state)
        return loaded

    async def run_skill(
        self,
        *,
        session_id: str,
        skill_name: str,
        payload: dict[str, Any],
        session_manager: Any,
    ) -> dict[str, Any]:
        import importlib
        import inspect
        import sys

        packages = self._discover()
        package = packages.get(skill_name)
        if package is None:
            from openagents.errors.suggestions import near_match

            available = sorted(packages.keys())
            guess = near_match(skill_name, available)
            extra = f" Did you mean '{guess}'?" if guess else ""
            raise KeyError(f"Unknown skill package: '{skill_name}'.{extra} Available: {available}")

        src_root = package["root"] / "src"
        entrypoint_module = f"{package['package_name']}.entrypoint"
        added = False
        if str(src_root) not in sys.path:
            sys.path.insert(0, str(src_root))
            added = True
        try:
            module = importlib.import_module(entrypoint_module)
            fn = getattr(module, "run_openagent_skill", None)
            if not callable(fn):
                raise ValueError(
                    f"Skill package '{skill_name}' entrypoint module '{entrypoint_module}' "
                    "must define callable 'run_openagent_skill'"
                )
            result = fn(payload)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise TypeError(
                    f"Skill package '{skill_name}' entrypoint returned {type(result).__name__}, expected dict"
                )
        finally:
            if added:
                try:
                    sys.path.remove(str(src_root))
                except ValueError:
                    pass

        state = await session_manager.get_state(session_id)
        current = dict(state.get(self._STATE_KEY, {}))
        skill_state = dict(current.get(skill_name, {}))
        skill_state["last_result_summary"] = str(result.get("design_rationale", "")).strip()[:300] or None
        current[skill_name] = skill_state
        state[self._STATE_KEY] = current
        await session_manager.set_state(session_id, state)
        return result
