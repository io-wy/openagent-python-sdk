"""Failing-by-design tests for ``stats.py``.

The CoreCoder demo agent should run::

    python -m unittest examples/corecoder_agent/workspace/test_stats.py

observe the failures, locate the bugs in ``stats.py``, fix them, and re-run
until everything passes.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path
import sys

# Make the workspace folder importable when invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats  # type: ignore  # noqa: E402


class TestMean(unittest.TestCase):
    def test_single_value(self) -> None:
        self.assertEqual(stats.mean([4.0]), 4.0)

    def test_uniform(self) -> None:
        self.assertEqual(stats.mean([2.0, 2.0, 2.0, 2.0]), 2.0)

    def test_mixed(self) -> None:
        self.assertAlmostEqual(stats.mean([1.0, 2.0, 3.0, 4.0]), 2.5)

    def test_raises_on_empty(self) -> None:
        with self.assertRaises(ValueError):
            stats.mean([])


class TestMedian(unittest.TestCase):
    def test_odd_length(self) -> None:
        self.assertEqual(stats.median([3.0, 1.0, 2.0]), 2.0)

    def test_even_length_returns_float_average(self) -> None:
        self.assertEqual(stats.median([1.0, 2.0, 3.0, 4.0]), 2.5)

    def test_even_length_with_negatives(self) -> None:
        self.assertEqual(stats.median([-3.0, -1.0, 1.0, 3.0]), 0.0)

    def test_raises_on_empty(self) -> None:
        with self.assertRaises(ValueError):
            stats.median([])


class TestVariance(unittest.TestCase):
    def test_uniform_is_zero(self) -> None:
        self.assertEqual(stats.variance([5.0, 5.0, 5.0]), 0.0)

    def test_known_value(self) -> None:
        # Population variance of {1,2,3,4,5}: mean=3, sum_sq=10, /5 = 2.0
        self.assertTrue(math.isclose(stats.variance([1.0, 2.0, 3.0, 4.0, 5.0]), 2.0))


if __name__ == "__main__":
    unittest.main()
