"""WP2: docs/event-taxonomy.md must list exactly the keys in EVENT_SCHEMAS."""

from __future__ import annotations

import re
from pathlib import Path

from openagents.interfaces.event_taxonomy import EVENT_SCHEMAS
from openagents.tools.gen_event_doc import render_doc

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOC_PATH = _REPO_ROOT / "docs" / "event-taxonomy.md"


_BACKTICK_NAME_RE = re.compile(r"\| `([^`]+)` \|")


def _names_in_doc(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        # rows look like "| `event.name` | required | optional | description |"
        match = _BACKTICK_NAME_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


def test_doc_file_exists():
    assert _DOC_PATH.exists(), (
        "docs/event-taxonomy.md is missing; regenerate via 'uv run python -m openagents.tools.gen_event_doc'"
    )


def test_doc_event_names_match_registry():
    text = _DOC_PATH.read_text(encoding="utf-8")
    doc_names = _names_in_doc(text)
    schema_names = set(EVENT_SCHEMAS.keys())
    assert doc_names == schema_names, (
        f"doc/registry mismatch: "
        f"in doc only={sorted(doc_names - schema_names)}; "
        f"in registry only={sorted(schema_names - doc_names)}; "
        f"regenerate via 'uv run python -m openagents.tools.gen_event_doc'"
    )


def test_render_doc_matches_file_byte_for_byte():
    """The committed file is exactly what the generator would produce."""
    expected = render_doc()
    actual = _DOC_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/event-taxonomy.md is out of date; regenerate via 'uv run python -m openagents.tools.gen_event_doc'"
    )
