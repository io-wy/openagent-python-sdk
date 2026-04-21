from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "README_EN.md",
    REPO_ROOT / "README_CN.md",
    REPO_ROOT / "docs" / "examples.md",
    REPO_ROOT / "examples" / "README.md",
]
IGNORED_CORE_PATHS = {
    "docs/",
    "examples/",
    "README.md",
    "openagent_cli/",
    "CHANGELOG.md",
    "Mine.md",
}
EXAMPLE_REF_PATTERNS = (
    re.compile(r"examples/([a-z0-9_]+)"),
    re.compile(r"`([a-z0-9_]+)/`"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _example_dirs() -> set[str]:
    examples_dir = REPO_ROOT / "examples"
    return {item.name for item in examples_dir.iterdir() if item.is_dir() and not item.name.startswith("__")}


def _referenced_examples(path: Path) -> set[str]:
    text = _read(path)
    refs: set[str] = set()
    for pattern in EXAMPLE_REF_PATTERNS:
        refs.update(pattern.findall(text))
    return refs


def test_project_readme_declared_in_pyproject_exists():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    readme_path = REPO_ROOT / data["project"]["readme"]

    assert readme_path.exists()


def test_gitignore_does_not_hide_core_repo_structure():
    lines = {
        line.strip()
        for line in _read(REPO_ROOT / ".gitignore").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert IGNORED_CORE_PATHS.isdisjoint(lines)


def test_repo_docs_do_not_reference_removed_docs_tree():
    for path in DOC_FILES:
        assert "docs-v2" not in _read(path), path.name


def test_repo_docs_only_reference_examples_that_exist():
    existing_examples = _example_dirs()

    for path in DOC_FILES:
        refs = _referenced_examples(path)
        assert refs <= existing_examples, (path.name, sorted(refs - existing_examples))
