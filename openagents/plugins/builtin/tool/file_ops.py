"""File operation tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class ReadFileTool(ToolPlugin):
    """Read file content.

    What: read a single text file as UTF-8 and return its content.
    Usage: ``{"id": "read_file", "type": "read_file"}``; invoke with ``{"path": "..."}``.
    Depends on: local filesystem.
    """

    name = "read_file"
    description = "Read the content of a file from the filesystem"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read",
                },
            },
            "required": ["path"],
        }

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(reads_files=True)

    def validate_params(self, params: dict[str, Any]) -> tuple[bool, str | None]:
        path = params.get("path", "")
        if not path:
            return False, "'path' parameter is required"
        return True, None

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        is_valid, error = self.validate_params(params)
        if not is_valid:
            raise ValueError(error)

        path = params.get("path", "")
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            return {"path": path, "content": content, "size": len(content)}
        except FileNotFoundError:
            raise FileNotFoundError(f"File not found: {path}")
        except Exception as e:
            raise RuntimeError(f"Failed to read file: {e}")


class WriteFileTool(ToolPlugin):
    """Write content to file.

    What: write or append UTF-8 content to a file, creating parent directories as needed.
    Usage: ``{"id": "write_file", "type": "write_file"}``; invoke with
    ``{"path": "...", "content": "...", "mode": "w"}``.
    Depends on: local filesystem.
    """

    durable_idempotent = False

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(writes_files=True)

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        path = params.get("path", "")
        content = params.get("content", "")
        mode = params.get("mode", "w")

        if not path:
            raise ValueError("'path' parameter is required")
        if mode not in ("w", "a"):
            raise ValueError("'mode' must be 'w' (write) or 'a' (append)")

        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)
            return {"path": path, "bytes_written": len(content.encode("utf-8")), "mode": mode}
        except Exception as e:
            raise RuntimeError(f"Failed to write file: {e}")


class ListFilesTool(ToolPlugin):
    """List files in directory.

    What: glob files under a directory (optionally recursive).
    Usage: ``{"id": "list_files", "type": "list_files"}``; invoke with
    ``{"path": ".", "pattern": "*", "recursive": false}``.
    Depends on: local filesystem.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(reads_files=True)

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        path = params.get("path", ".")
        pattern = params.get("pattern", "*")
        recursive = params.get("recursive", False)

        try:
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"Directory not found: {path}")

            if recursive:
                files = [str(f.relative_to(p)) for f in p.rglob(pattern) if f.is_file()]
            else:
                files = [f.name for f in p.glob(pattern) if f.is_file()]

            return {"path": path, "pattern": pattern, "files": sorted(files), "count": len(files)}
        except Exception as e:
            raise RuntimeError(f"Failed to list files: {e}")


class DeleteFileTool(ToolPlugin):
    """Delete file or directory.

    What: remove a single file (``Path.unlink``) or directory tree (``shutil.rmtree``).
    Usage: ``{"id": "delete_file", "type": "delete_file"}``; invoke with ``{"path": "..."}``.
    Depends on: local filesystem.
    """

    durable_idempotent = False

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(writes_files=True)

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        path = params.get("path", "")
        if not path:
            raise ValueError("'path' parameter is required")

        try:
            p = Path(path)
            if not p.exists():
                raise FileNotFoundError(f"Path not found: {path}")

            if p.is_file():
                p.unlink()
                return {"path": path, "type": "file", "deleted": True}
            elif p.is_dir():
                import shutil

                shutil.rmtree(p)
                return {"path": path, "type": "directory", "deleted": True}
        except Exception as e:
            raise RuntimeError(f"Failed to delete: {e}")
