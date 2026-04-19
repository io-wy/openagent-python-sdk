"""Shared exception types."""

from __future__ import annotations

from typing import Any, Literal, TypeVar

OpenAgentsErrorT = TypeVar("OpenAgentsErrorT", bound="OpenAgentsError")


class OpenAgentsError(Exception):
    """Base exception for SDK errors.

    Subclasses inherit two optional kwargs in addition to context fields:

    - ``hint``: a short human-readable suggestion explaining how to fix the
      situation that triggered the error. Surfaced via ``str(exc)`` on the
      ``hint:`` line and accessible as ``exc.hint``.
    - ``docs_url``: an optional URL to documentation about the error.
      Surfaced via ``str(exc)`` on the ``docs:`` line.

    Both default to ``None`` so existing call sites remain byte-identical
    in their formatting unless they opt in.
    """

    agent_id: str | None
    session_id: str | None
    run_id: str | None
    tool_id: str | None
    step_number: int | None
    hint: str | None
    docs_url: str | None

    def __init__(
        self,
        message: str = "",
        *,
        hint: str | None = None,
        docs_url: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        tool_id: str | None = None,
        step_number: int | None = None,
    ) -> None:
        super().__init__(message)
        self.agent_id = agent_id
        self.session_id = session_id
        self.run_id = run_id
        self.tool_id = tool_id
        self.step_number = step_number
        self.hint = hint
        self.docs_url = docs_url

    def __str__(self) -> str:
        msg = super().__str__()
        parts = [msg] if msg else []
        if self.hint:
            parts.append(f"  hint: {self.hint}")
        if self.docs_url:
            parts.append(f"  docs: {self.docs_url}")
        return "\n".join(parts)

    def with_context(self: OpenAgentsErrorT, **kwargs: str | int | None) -> OpenAgentsErrorT:
        """Attach runtime identifiers to an existing exception."""

        for key in ("agent_id", "session_id", "run_id", "tool_id", "step_number"):
            if key in kwargs:
                setattr(self, key, kwargs[key])
        return self


class ConfigError(OpenAgentsError):
    """Raised when config parsing or validation fails."""


class ConfigValidationError(ConfigError):
    """Raised when a config payload violates the schema."""


class ConfigLoadError(ConfigError):
    """Raised when a config file cannot be read or decoded."""


class PluginError(OpenAgentsError):
    """Base exception for plugin loading and validation failures."""


class PluginLoadError(PluginError):
    """Raised when plugin loading fails."""


class PluginCapabilityError(PluginError):
    """Raised when plugin capabilities do not meet requirements."""


class PluginConfigError(PluginError):
    """Raised when plugin config is invalid."""


class ExecutionError(OpenAgentsError):
    """Base exception for runtime execution failures."""


class MaxStepsExceeded(ExecutionError):
    """Raised when a step or tool-call budget is exceeded."""


class BudgetExhausted(ExecutionError):
    """Raised when runtime budget limits are exceeded."""

    kind: Literal["tool_calls", "duration", "steps", "cost"] | None
    current: float | int | None
    limit: float | int | None

    def __init__(
        self,
        message: str = "",
        *,
        kind: Literal["tool_calls", "duration", "steps", "cost"] | None = None,
        current: float | int | None = None,
        limit: float | int | None = None,
        hint: str | None = None,
        docs_url: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        tool_id: str | None = None,
        step_number: int | None = None,
    ) -> None:
        super().__init__(
            message,
            hint=hint,
            docs_url=docs_url,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            tool_id=tool_id,
            step_number=step_number,
        )
        self.kind = kind
        self.current = current
        self.limit = limit


class OutputValidationError(ExecutionError):
    """Final output failed validation after max retries."""

    output_type: Any
    attempts: int
    last_validation_error: Any

    def __init__(
        self,
        message: str = "",
        *,
        output_type: Any = None,
        attempts: int = 0,
        last_validation_error: Any = None,
        hint: str | None = None,
        docs_url: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        tool_id: str | None = None,
        step_number: int | None = None,
    ) -> None:
        super().__init__(
            message,
            hint=hint,
            docs_url=docs_url,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            tool_id=tool_id,
            step_number=step_number,
        )
        self.output_type = output_type
        self.attempts = attempts
        self.last_validation_error = last_validation_error


class SessionError(ExecutionError):
    """Raised when session management fails."""


class PatternError(ExecutionError):
    """Raised when a pattern fails during execution."""


class ToolError(OpenAgentsError):
    """Base exception for tool errors."""

    tool_name: str

    def __init__(
        self,
        message: str,
        tool_name: str = "",
        *,
        hint: str | None = None,
        docs_url: str | None = None,
    ) -> None:
        super().__init__(
            message,
            hint=hint,
            docs_url=docs_url,
            tool_id=tool_name or None,
        )
        self.tool_name = tool_name


class RetryableToolError(ToolError):
    """Tool error that can be retried."""


class PermanentToolError(ToolError):
    """Tool error that should not be retried."""


class ToolTimeoutError(RetryableToolError):
    """Raised when a tool execution times out."""


class ToolNotFoundError(PermanentToolError):
    """Raised when a requested tool is not registered."""


class ToolValidationError(PermanentToolError):
    """Tool parameters failed schema or semantic validation. Not retryable."""


class ToolAuthError(PermanentToolError):
    """Tool authentication or authorization failed. Not retryable without new creds."""


class ToolRateLimitError(RetryableToolError):
    """Third-party rate-limited us. Retryable with backoff."""


class ToolUnavailableError(RetryableToolError):
    """Transient unreachability (DNS, TCP, 5xx). Retryable."""


class ToolCancelledError(PermanentToolError):
    """Tool invocation was cancelled mid-execution via cancel_event. Not retryable."""


class LLMError(OpenAgentsError):
    """Base exception for LLM/provider failures."""


class LLMConnectionError(LLMError):
    """Raised when a provider connection fails."""


class LLMRateLimitError(LLMError):
    """Raised when a provider rate-limits a request."""


class LLMResponseError(LLMError):
    """Raised when a provider returns an invalid response."""


class ModelRetryError(LLMError):
    """Raised when the model should retry with corrected input."""

    validation_error: Any

    def __init__(
        self,
        message: str = "",
        *,
        validation_error: Any = None,
        hint: str | None = None,
        docs_url: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        tool_id: str | None = None,
        step_number: int | None = None,
    ) -> None:
        super().__init__(
            message,
            hint=hint,
            docs_url=docs_url,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            tool_id=tool_id,
            step_number=step_number,
        )
        self.validation_error = validation_error


class UserError(OpenAgentsError):
    """Raised for caller-side mistakes."""


class InvalidInputError(UserError):
    """Raised when caller-provided input is invalid."""


class AgentNotFoundError(UserError):
    """Raised when the requested agent does not exist."""


# Backward-compatible alias kept during the migration.
CapabilityError = PluginCapabilityError
