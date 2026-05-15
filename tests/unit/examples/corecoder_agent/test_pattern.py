"""Tests for provider-specific tool schema rendering in CoreCoderPattern."""

from __future__ import annotations

from types import SimpleNamespace

from examples.corecoder_agent.app.pattern import CoreCoderPattern
from examples.corecoder_agent.app.tools.read_file import ReadFileTool

from ._helpers import make_ctx


def test_build_tool_schemas_uses_anthropic_shape() -> None:
    pattern = CoreCoderPattern()
    pattern.context = make_ctx(
        llm_client=SimpleNamespace(provider_name="anthropic"),
        tools={"read_file": ReadFileTool()},
    )

    schemas = pattern._build_tool_schemas()

    assert schemas[0]["name"] == "read_file"
    assert "input_schema" in schemas[0]
    assert "function" not in schemas[0]


def test_build_tool_schemas_uses_openai_function_shape() -> None:
    pattern = CoreCoderPattern()
    pattern.context = make_ctx(
        llm_client=SimpleNamespace(provider_name="openai_compatible"),
        tools={"read_file": ReadFileTool()},
    )

    schemas = pattern._build_tool_schemas()

    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "read_file"
    assert schemas[0]["function"]["parameters"]["type"] == "object"
