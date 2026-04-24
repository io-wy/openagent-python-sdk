"""Observability errors."""

from __future__ import annotations


class RichNotInstalledError(ImportError):
    """Raised when rich-powered pretty output is requested but rich is missing.

    Mirrors the posture of mcp_tool / mem0_memory: if the user explicitly
    asks for the feature and the optional extra isn't installed, fail loud
    and give them the exact pip command.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "rich is required for pretty output. Install with: pip install io-openagent-sdk[rich]"
        )


class LoguruNotInstalledError(ImportError):
    """Raised when loguru-backed multi-sink logging is requested but loguru is missing.

    Mirrors RichNotInstalledError: fail loud with the exact pip command when
    ``LoggingConfig.loguru_sinks`` is non-empty but the optional extra was not
    installed.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "loguru is required for loguru_sinks. Install with: pip install io-openagent-sdk[loguru]"
        )
