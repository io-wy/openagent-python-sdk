"""Pure-function redactor used by both stdlib logging and event bus sinks."""

from __future__ import annotations

from typing import Any

_TRUNCATED_SUFFIX = " (truncated {n} chars)"


def redact(value: Any, *, keys: list[str], max_value_length: int) -> Any:
    """Return a deep-copied version of value with sensitive keys masked and long strings truncated.

    Rules (applied in order):
    1. Case-insensitive key-name match against keys -> value becomes "***".
    2. String values exceeding max_value_length -> truncated with suffix.
    3. Nested dict/list recursion; circular references replaced with "<circular>".

    Scalars (int/float/bool/None) pass through unchanged.
    """
    lowered = {k.lower() for k in keys}
    return _walk(value, lowered, max_value_length, set())


def _walk(node: Any, keys_lower: set[str], max_len: int, seen: set[int]) -> Any:
    if isinstance(node, dict):
        node_id = id(node)
        if node_id in seen:
            return "<circular>"
        seen = seen | {node_id}
        return {
            key: (
                "***"
                if isinstance(key, str) and key.lower() in keys_lower
                else _walk(val, keys_lower, max_len, seen)
            )
            for key, val in node.items()
        }
    if isinstance(node, list):
        node_id = id(node)
        if node_id in seen:
            return "<circular>"
        seen = seen | {node_id}
        return [_walk(item, keys_lower, max_len, seen) for item in node]
    if isinstance(node, str) and len(node) > max_len:
        return node[:max_len] + _TRUNCATED_SUFFIX.format(n=len(node))
    return node
