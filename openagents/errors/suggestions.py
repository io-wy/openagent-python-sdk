"""Lightweight 'did you mean?' helpers."""

from __future__ import annotations

import difflib
from typing import Iterable


def near_match(
    needle: str,
    candidates: Iterable[str],
    *,
    cutoff: float = 0.6,
) -> str | None:
    """Return the closest candidate to ``needle`` (or ``None``).

    Uses :func:`difflib.get_close_matches` under the hood. ``cutoff`` is
    the minimum similarity ratio (0.0 - 1.0) to consider; the default of
    ``0.6`` matches Python's stdlib default and works well for typical
    typo / case-skew distances.

    Returns ``None`` when no candidate clears the cutoff or when
    ``candidates`` is empty.
    """

    matches = difflib.get_close_matches(needle, list(candidates), n=1, cutoff=cutoff)
    return matches[0] if matches else None
