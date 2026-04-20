"""Text processing tools."""

from __future__ import annotations

import json
import re
from typing import Any

from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class GrepFilesTool(ToolPlugin):
    """Search for pattern in files using regex.

    What: pure-Python recursive regex search across files under a path; first 100 matches returned.
    Usage: ``{"id": "grep_files", "type": "grep_files"}``; invoke with
    ``{"pattern": "...", "path": ".", "case_sensitive": true}``.
    Depends on: local filesystem; Python ``re`` module.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(reads_files=True)

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        pattern = params.get("pattern", "")
        path = params.get("path", ".")
        case_sensitive = params.get("case_sensitive", True)

        if not pattern:
            raise ValueError("'pattern' parameter is required")

        try:
            from pathlib import Path

            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
            matches = []

            p = Path(path)
            if p.is_file():
                files = [p]
            else:
                files = [f for f in p.rglob("*") if f.is_file()]

            for file in files:
                try:
                    with open(file, "r", encoding="utf-8") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                matches.append({"file": str(file), "line": line_num, "content": line.rstrip()})
                except (UnicodeDecodeError, PermissionError):
                    continue

            return {"pattern": pattern, "matches": matches[:100], "total": len(matches)}
        except Exception as e:
            raise RuntimeError(f"Failed to grep files: {e}")


class RipgrepTool(ToolPlugin):
    """Fast search using ripgrep (rg) if available.

    What: shells out to ``rg --json`` for fast regex search; first 100 matches returned.
    Usage: ``{"id": "ripgrep", "type": "ripgrep"}``; invoke with ``{"pattern": "...", "path": ".", "file_type": "py"}``.
    Depends on: ``rg`` binary on PATH; local filesystem.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(reads_files=True)

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        pattern = params.get("pattern", "")
        path = params.get("path", ".")
        case_sensitive = params.get("case_sensitive", True)
        file_type = params.get("file_type")  # e.g., "py", "js", "md"

        if not pattern:
            raise ValueError("'pattern' parameter is required")

        try:
            import asyncio
            import shutil

            # Check if rg is available
            if not shutil.which("rg"):
                raise RuntimeError("ripgrep (rg) is not installed")

            cmd = ["rg", "--json", pattern, path]
            if not case_sensitive:
                cmd.insert(1, "-i")
            if file_type:
                cmd.insert(1, f"-t{file_type}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode not in (0, 1):  # 0 = found, 1 = not found
                raise RuntimeError(f"rg failed: {stderr.decode('utf-8')}")

            matches = []
            for line in stdout.decode("utf-8").splitlines():
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        match_data = data.get("data", {})
                        matches.append(
                            {
                                "file": match_data.get("path", {}).get("text", ""),
                                "line": match_data.get("line_number", 0),
                                "content": match_data.get("lines", {}).get("text", "").rstrip(),
                            }
                        )
                except json.JSONDecodeError:
                    continue

            return {"pattern": pattern, "matches": matches[:100], "total": len(matches)}
        except Exception as e:
            raise RuntimeError(f"Failed to run ripgrep: {e}")


class JsonParseTool(ToolPlugin):
    """Parse JSON string.

    What: parse a JSON string and return the deserialized value plus its Python type name.
    Usage: ``{"id": "json_parse", "type": "json_parse"}``; invoke with ``{"text": "{...}"}``.
    Depends on: stdlib ``json`` module.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        text = params.get("text", "")
        if not text:
            raise ValueError("'text' parameter is required")

        try:
            data = json.loads(text)
            return {"parsed": data, "type": type(data).__name__}
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")


class TextTransformTool(ToolPlugin):
    """Transform text (upper, lower, title, etc).

    What: apply one of upper/lower/title/capitalize/strip/reverse to a string.
    Usage: ``{"id": "text_transform", "type": "text_transform"}``; invoke with
    ``{"text": "...", "operation": "lower"}``.
    Depends on: nothing (stdlib).
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        text = params.get("text", "")
        operation = params.get("operation", "lower")

        operations = {
            "upper": str.upper,
            "lower": str.lower,
            "title": str.title,
            "capitalize": str.capitalize,
            "strip": str.strip,
            "reverse": lambda s: s[::-1],
        }

        if operation not in operations:
            raise ValueError(f"Unknown operation: {operation}. Available: {list(operations.keys())}")

        result = operations[operation](text)
        return {"original": text, "operation": operation, "result": result}
