"""Tests for observability.redact."""

from __future__ import annotations

import pytest

from openagents.observability.redact import redact


class TestRedactKeys:
    def test_masks_matching_key_case_insensitive(self) -> None:
        out = redact({"API_KEY": "sk-123"}, keys=["api_key"], max_value_length=1000)
        assert out == {"API_KEY": "***"}

    def test_leaves_unknown_keys_alone(self) -> None:
        out = redact({"foo": "bar"}, keys=["api_key"], max_value_length=1000)
        assert out == {"foo": "bar"}

    def test_recurses_into_nested_dicts(self) -> None:
        payload = {"outer": {"token": "abc", "safe": "keep"}}
        out = redact(payload, keys=["token"], max_value_length=1000)
        assert out == {"outer": {"token": "***", "safe": "keep"}}

    def test_recurses_into_lists(self) -> None:
        payload = {"items": [{"password": "p1"}, {"password": "p2"}]}
        out = redact(payload, keys=["password"], max_value_length=1000)
        assert out == {"items": [{"password": "***"}, {"password": "***"}]}


class TestTruncation:
    def test_truncates_long_strings(self) -> None:
        long = "x" * 1000
        out = redact({"note": long}, keys=[], max_value_length=10)
        assert out["note"].startswith("xxxxxxxxxx")
        assert "(truncated" in out["note"]
        assert out["note"].endswith("chars)")

    def test_short_strings_untouched(self) -> None:
        out = redact({"note": "short"}, keys=[], max_value_length=100)
        assert out == {"note": "short"}

    def test_truncation_applied_before_would_exceed(self) -> None:
        out = redact({"note": "x" * 11}, keys=[], max_value_length=10)
        assert "(truncated 11 chars)" in out["note"]


class TestScalars:
    def test_passes_through_int_float_bool_none(self) -> None:
        payload = {"a": 1, "b": 1.5, "c": True, "d": None}
        out = redact(payload, keys=[], max_value_length=1000)
        assert out == payload


class TestImmutability:
    def test_does_not_mutate_input(self) -> None:
        original = {"api_key": "sk-123", "nested": {"token": "abc"}}
        snapshot = {"api_key": "sk-123", "nested": {"token": "abc"}}
        _ = redact(original, keys=["api_key", "token"], max_value_length=1000)
        assert original == snapshot


class TestCircularGuard:
    def test_cycle_replaced_with_marker(self) -> None:
        a: dict = {"name": "a"}
        a["self"] = a
        out = redact(a, keys=[], max_value_length=1000)
        assert out["name"] == "a"
        assert out["self"] == "<circular>"
