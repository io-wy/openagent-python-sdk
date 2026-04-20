"""WP1: openagents.errors.suggestions.near_match."""

from __future__ import annotations

from openagents.errors.suggestions import near_match


def test_exact_match_returned():
    assert near_match("buffer", ["buffer", "window_buffer", "chain"]) == "buffer"


def test_close_typo_returned():
    assert near_match("bufer", ["buffer", "window_buffer", "chain"]) == "buffer"


def test_no_close_match_returns_none():
    assert near_match("xyzzy", ["buffer", "window_buffer", "chain"]) is None


def test_empty_candidates_returns_none():
    assert near_match("anything", []) is None


def test_cutoff_is_respected():
    # "ab" / "wz" are very dissimilar; default 0.6 cutoff filters them out
    assert near_match("ab", ["wz"]) is None


def test_lower_cutoff_lets_weak_matches_through():
    # "ab" / "ax" share one char; cutoff=0.4 should match
    result = near_match("ab", ["ax"], cutoff=0.4)
    assert result == "ax"


def test_returns_first_close_match_only():
    # near_match is documented as n=1; should pick exactly one
    result = near_match("buf", ["buffer", "buf_thing", "wholly_different"])
    assert result in {"buffer", "buf_thing"}


def test_works_with_iterables_not_just_lists():
    result = near_match("buffer", iter(["buffer", "chain"]))
    assert result == "buffer"
