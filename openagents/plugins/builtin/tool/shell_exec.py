"""Shell execution tool — allowlist-aware subprocess (no shell interpretation)."""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any

from pydantic import BaseModel

from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.tool import ToolPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class ShellExecTool(TypedConfigPluginMixin, ToolPlugin):
    """Execute a process using explicit argv (no shell). Allowlist-aware.

    What:
        Async subprocess execution via ``asyncio.create_subprocess_exec`` (no
        shell). Captures stdout/stderr with configurable byte caps, timeout,
        and an argv[0] allowlist. Does not inherit full environment.
        ``truncated`` is set when raw byte length of stdout or stderr exceeds
        ``capture_bytes`` — it measures bytes, not decoded character count, so
        multibyte UTF-8 sequences may cause it to trigger before the character
        limit is reached.
    Usage:
        ``{"id": "shell", "type": "shell_exec", "config": {
            "command_allowlist": ["node", "npx", "npm", "markitdown"],
            "env_passthrough": ["PATH", "HOME"],
            "default_timeout_ms": 120000}}``
    Depends on:
        asyncio stdlib only. Pair with a strict ``tool_executor``.
    """

    durable_idempotent = False

    class Config(BaseModel):
        cwd: str | None = None
        env_passthrough: list[str] = []
        command_allowlist: list[str] | None = None
        default_timeout_ms: int = 60_000
        capture_bytes: int = 1_048_576

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        self._init_typed_config()

    @staticmethod
    def _shlex_split(command: str) -> list[str]:
        """Split a command string into argv, handling Windows paths correctly.

        On POSIX, use standard posix=True mode.  On Windows, use posix=False
        (which preserves backslashes in paths) and then strip one layer of
        surrounding quotes from each token, matching the behaviour of
        posix=True on Unix.
        """
        if os.name == "nt":
            parts = shlex.split(command, posix=False)
            argv: list[str] = []
            for p in parts:
                if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
                    argv.append(p[1:-1])
                else:
                    argv.append(p)
            return argv
        return shlex.split(command)

    def _resolve_argv(self, command: str | list[str]) -> list[str]:
        if isinstance(command, list):
            argv = [str(c) for c in command]
        else:
            argv = self._shlex_split(str(command))
        if not argv:
            raise ValueError("'command' resolved to empty argv")
        allow = self.cfg.command_allowlist
        if allow is not None:
            first = argv[0]
            # Reject path-like argv[0] to prevent bypassing allowlist via absolute/relative paths
            if os.sep in first or (os.altsep and os.altsep in first):
                raise ValueError(f"command {first!r} must be a bare name (no path) when command_allowlist is active")
            if first not in allow:
                raise ValueError(f"command {first!r} not in allowlist {allow!r}")
        return argv

    def _resolve_env(self, extra: dict[str, str] | None) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in self.cfg.env_passthrough:
            if key in os.environ:
                env[key] = os.environ[key]
        if extra:
            env.update({str(k): str(v) for k, v in extra.items()})
        return env

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        argv = self._resolve_argv(params.get("command", ""))
        cwd = params.get("cwd") or self.cfg.cwd
        timeout_ms = int(params.get("timeout_ms") or self.cfg.default_timeout_ms)
        env = self._resolve_env(params.get("env"))
        cap = int(self.cfg.capture_bytes)

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env or None,
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            timed_out = True
            # NOTE: kill() only terminates the direct child process. On POSIX,
            # any grandchildren spawned by the child (e.g. worker processes
            # forked by `npm install`) are not signalled and may become orphans.
            proc.kill()
            stdout, stderr = await proc.communicate()

        truncated = len(stdout) > cap or len(stderr) > cap
        return {
            "exit_code": proc.returncode,
            "stdout": stdout[:cap].decode("utf-8", errors="replace"),
            "stderr": stderr[:cap].decode("utf-8", errors="replace"),
            "timed_out": timed_out,
            "truncated": truncated,
        }
