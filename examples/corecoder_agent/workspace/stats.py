"""Tiny statistics helpers used by the demo.

Two of these functions have bugs that the CoreCoder demo agent is expected
to find and fix. Run::

    python -m unittest examples.corecoder_agent.workspace.test_stats

before and after the run to see the failures resolve.
"""

from __future__ import annotations


def mean(values: list[float]) -> float:
    """Arithmetic mean of a non-empty sequence."""
    if not values:
        raise ValueError("mean() requires at least one value")
    # BUG: dividing by len(values) - 1 makes this the wrong formula for any
    # input longer than 1 element.
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    """Median of a sequence; assumes ``values`` is non-empty."""
    if not values:
        raise ValueError("median() requires at least one value")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    # BUG: integer-divides instead of true-divides — returns floored midpoint.
    return (ordered[mid - 1] + ordered[mid]) / 2


def variance(values: list[float]) -> float:
    """Population variance."""
    if not values:
        raise ValueError("variance() requires at least one value")
    m = mean(values)
    return sum((x - m) ** 2 for x in values) / len(values)
