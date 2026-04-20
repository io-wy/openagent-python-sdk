"""WP4: every builtin plugin class has 'What:' / 'Usage:' / 'Depends on:' headers."""

from __future__ import annotations

from typing import Iterable

import pytest

from openagents.plugins.registry import _BUILTIN_REGISTRY


def _collect_builtin_plugin_classes() -> Iterable[type]:
    seen: set[type] = set()
    for mapping in _BUILTIN_REGISTRY.values():
        for cls in mapping.values():
            if cls in seen:
                continue
            seen.add(cls)
            yield cls


REQUIRED_SECTIONS = ("What:", "Usage:", "Depends on:")


def test_every_builtin_class_has_three_section_docstring():
    """Every class registered in ``_BUILTIN_REGISTRY`` must have all three section headers in its docstring."""
    missing: list[str] = []
    for cls in _collect_builtin_plugin_classes():
        doc = (cls.__doc__ or "").strip()
        if not doc:
            missing.append(f"{cls.__module__}.{cls.__name__}: missing docstring")
            continue
        for header in REQUIRED_SECTIONS:
            if header not in doc:
                missing.append(f"{cls.__module__}.{cls.__name__}: missing '{header}' section")
    assert not missing, "\n  " + "\n  ".join(missing)


@pytest.mark.parametrize("cls", list(_collect_builtin_plugin_classes()), ids=lambda c: c.__name__)
def test_docstring_first_line_ends_with_period(cls):
    """First line should end with a period (Google-style summary)."""
    doc = (cls.__doc__ or "").strip()
    if not doc:
        pytest.fail(f"{cls.__name__}: missing docstring")
    first = doc.splitlines()[0].rstrip()
    assert first.endswith("."), f"{cls.__name__}: first docstring line should end with a period, got: {first!r}"
