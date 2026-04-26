"""Random and UUID generation tools."""

from __future__ import annotations

import random
import uuid
from typing import Any

from openagents.interfaces.tool import ToolPlugin


class RandomIntTool(ToolPlugin):
    """Generate random integer.

    What: ``random.randint(min, max)`` (single or batch up to 100).
    Usage: ``{"id": "rand_int", "type": "random_int"}``; invoke with ``{"min": 0, "max": 100, "count": 1}``.
    Depends on: stdlib ``random``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        min_val = params.get("min", 0)
        max_val = params.get("max", 100)
        count = params.get("count", 1)

        if not isinstance(min_val, int) or not isinstance(max_val, int):
            raise ValueError("'min' and 'max' must be integers")
        if min_val >= max_val:
            raise ValueError("'min' must be less than 'max'")
        if count < 1 or count > 100:
            raise ValueError("'count' must be between 1 and 100")

        if count == 1:
            return {"value": random.randint(min_val, max_val)}
        return {"values": [random.randint(min_val, max_val) for _ in range(count)]}


class RandomChoiceTool(ToolPlugin):
    """Random choice from a list.

    What: ``random.choice`` or ``random.sample`` for batch picks without replacement.
    Usage: ``{"id": "rand_choice", "type": "random_choice"}``; invoke with ``{"choices": ["a", "b"], "count": 1}``.
    Depends on: stdlib ``random``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        choices = params.get("choices", [])
        count = params.get("count", 1)

        if not choices:
            raise ValueError("'choices' parameter is required and must not be empty")
        if count < 1 or count > len(choices):
            raise ValueError(f"'count' must be between 1 and {len(choices)}")

        if count == 1:
            return {"value": random.choice(choices)}
        return {"values": random.sample(choices, count)}


class UUIDTool(ToolPlugin):
    """Generate UUID.

    What: emit ``uuid4`` (default) or ``uuid1`` strings, single or batch up to 100.
    Usage: ``{"id": "uuid", "type": "uuid"}``; invoke with ``{"version": 4, "count": 1}``.
    Depends on: stdlib ``uuid``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        count = params.get("count", 1)
        version = params.get("version", 4)  # 4 = random, 1 = time-based

        if count < 1 or count > 100:
            raise ValueError("'count' must be between 1 and 100")

        if version == 1:
            uuids = [str(uuid.uuid1()) for _ in range(count)]
        else:
            uuids = [str(uuid.uuid4()) for _ in range(count)]

        if count == 1:
            return {"uuid": uuids[0]}
        return {"uuids": uuids}


class RandomStringTool(ToolPlugin):
    """Generate random string.

    What: pick characters from one of several built-in charsets (alphanumeric/alpha/numeric/hex/ascii).
    Usage: ``{"id": "rand_str", "type": "random_string"}``; invoke with ``{"length": 16, "charset": "alphanumeric"}``.
    Depends on: stdlib ``random``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        length = params.get("length", 16)
        charset = params.get("charset", "alphanumeric")

        if length < 1 or length > 1000:
            raise ValueError("'length' must be between 1 and 1000")

        charsets = {
            "alphanumeric": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "alpha": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "numeric": "0123456789",
            "hex": "0123456789abcdef",
            "ascii": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*",
        }

        chars = charsets.get(charset, charsets["alphanumeric"])

        if len(chars) == 1:
            result = chars * length
        else:
            result = "".join(random.choice(chars) for _ in range(length))

        return {"value": result, "length": length}
