"""WP2: every key in EVENT_SCHEMAS must be emitted somewhere under openagents/.

This is a drift-guard - if someone removes an emit call site without
also removing the schema entry (or vice versa), the registry and the
runtime go out of sync.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openagents.interfaces.event_taxonomy import EVENT_SCHEMAS

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PACKAGE_ROOT = _REPO_ROOT / "openagents"


def _iter_python_sources():
    for p in _PACKAGE_ROOT.rglob("*.py"):
        # skip __pycache__ and the taxonomy module itself
        if "__pycache__" in p.parts:
            continue
        if p.name == "event_taxonomy.py":
            continue
        if p.name == "gen_event_doc.py":
            continue
        yield p


def _read_all_sources() -> str:
    blob = []
    for path in _iter_python_sources():
        blob.append(path.read_text(encoding="utf-8"))
    return "\n".join(blob)


@pytest.fixture(scope="module")
def all_source_text() -> str:
    return _read_all_sources()


@pytest.mark.parametrize("event_name", sorted(EVENT_SCHEMAS.keys()))
def test_event_name_is_emitted_somewhere(event_name: str, all_source_text: str) -> None:
    """Each declared event name must appear as a string literal in the package."""
    pattern = re.compile(re.escape(f'"{event_name}"'))
    pattern_single = re.compile(re.escape(f"'{event_name}'"))
    if pattern.search(all_source_text):
        return
    if pattern_single.search(all_source_text):
        return
    pytest.fail(
        f"event '{event_name}' declared in EVENT_SCHEMAS but never emitted "
        f"(searched openagents/**/*.py for a string literal)."
    )
