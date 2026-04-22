"""Drift gate: every OpenAgentsError subclass must appear (by code) in both
docs/errors.md and docs/errors.en.md."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import openagents.errors.exceptions as errors_mod
from openagents.errors.exceptions import OpenAgentsError

ROOT = Path(__file__).resolve().parents[3]


def _all_codes() -> list[str]:
    codes: list[str] = []
    for _, cls in inspect.getmembers(errors_mod, inspect.isclass):
        if issubclass(cls, OpenAgentsError) and cls.__module__ == errors_mod.__name__:
            codes.append(cls.code)
    # Dedup in case an alias surfaces the same class twice
    return sorted(set(codes))


@pytest.mark.parametrize("doc_path", ["docs/errors.md", "docs/errors.en.md"])
def test_errors_doc_covers_every_code(doc_path):
    text = (ROOT / doc_path).read_text(encoding="utf-8")
    missing = [c for c in _all_codes() if c not in text]
    assert not missing, f"{doc_path} missing codes: {missing}"
