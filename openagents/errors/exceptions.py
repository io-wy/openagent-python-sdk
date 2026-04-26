"""Shared exception types."""

from __future__ import annotations

from typing import Any, ClassVar, Literal, TypeVar

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

    code: ClassVar[str] = "openagents.error"
    retryable: ClassVar[bool] = False

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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a stable shape for HTTP / SSE / trace exporters.

        Cause chain is intentionally excluded — ``ErrorDetails.from_exception``
        owns that recursion so callers cannot get the same walk in two places.
        """
        message = super().__str__() or ""
        return {
            "code": type(self).code,
            "message": message.splitlines()[0] if message else "",
            "hint": self.hint,
            "docs_url": self.docs_url,
            "retryable": type(self).retryable,
            "context": {
                "agent_id": self.agent_id,
                "session_id": self.session_id,
                "run_id": self.run_id,
                "tool_id": self.tool_id,
                "step_number": self.step_number,
            },
        }

    def with_context(self: OpenAgentsErrorT, **kwargs: str | int | None) -> OpenAgentsErrorT:
        """Attach runtime identifiers to an existing exception."""

        for key in ("agent_id", "session_id", "run_id", "tool_id", "step_number"):
            if key in kwargs:
                setattr(self, key, kwargs[key])
        return self


class ConfigError(OpenAgentsError):
    """Raised when config parsing or validation fails."""

    code: ClassVar[str] = "config.error"
    retryable: ClassVar[bool] = False


class ConfigValidationError(ConfigError):
    """Raised when a config payload violates the schema."""

    code: ClassVar[str] = "config.validation"
    retryable: ClassVar[bool] = False


class ConfigLoadError(ConfigError):
    """Raised when a config file cannot be read or decoded."""

    code: ClassVar[str] = "config.load"
    retryable: ClassVar[bool] = False


class PluginError(OpenAgentsError):
    """Base exception for plugin loading and validation failures."""

    code: ClassVar[str] = "plugin.error"
    retryable: ClassVar[bool] = False


class PluginLoadError(PluginError):
    """Raised when plugin loading fails."""

    code: ClassVar[str] = "plugin.load"
    retryable: ClassVar[bool] = False


class PluginConfigError(PluginError):
    """Raised when plugin config is invalid."""

    code: ClassVar[str] = "plugin.config"
    retryable: ClassVar[bool] = False


class ExecutionError(OpenAgentsError):
    """Base exception for runtime execution failures."""

    code: ClassVar[str] = "execution.error"
    retryable: ClassVar[bool] = False


class MaxStepsExceeded(ExecutionError):
    """Raised when a step or tool-call budget is exceeded."""

    code: ClassVar[str] = "execution.max_steps"
    retryable: ClassVar[bool] = False


class BudgetExhausted(ExecutionError):
    """Raised when runtime budget limits are exceeded."""

    code: ClassVar[str] = "execution.budget_exhausted"
    retryable: ClassVar[bool] = False

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

    code: ClassVar[str] = "execution.output_validation"
    retryable: ClassVar[bool] = False

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

    code: ClassVar[str] = "session.error"
    retryable: ClassVar[bool] = False


class PatternError(ExecutionError):
    """Raised when a pattern fails during execution."""

    code: ClassVar[str] = "pattern.error"
    retryable: ClassVar[bool] = False


class ToolError(OpenAgentsError):
    """Base exception for tool errors."""

    code: ClassVar[str] = "tool.error"
    retryable: ClassVar[bool] = False

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

    code: ClassVar[str] = "tool.retryable"
    retryable: ClassVar[bool] = True


class PermanentToolError(ToolError):
    """Tool error that should not be retried."""

    code: ClassVar[str] = "tool.permanent"
    retryable: ClassVar[bool] = False


class ToolTimeoutError(RetryableToolError):
    """Raised when a tool execution times out."""

    code: ClassVar[str] = "tool.timeout"
    retryable: ClassVar[bool] = True


class ToolNotFoundError(PermanentToolError):
    """Raised when a requested tool is not registered."""

    code: ClassVar[str] = "tool.not_found"
    retryable: ClassVar[bool] = False


class ToolValidationError(PermanentToolError):
    """Tool parameters failed schema or semantic validation. Not retryable."""

    code: ClassVar[str] = "tool.validation"
    retryable: ClassVar[bool] = False


class ToolAuthError(PermanentToolError):
    """Tool authentication or authorization failed. Not retryable without new creds."""

    code: ClassVar[str] = "tool.auth"
    retryable: ClassVar[bool] = False


class ToolRateLimitError(RetryableToolError):
    """Third-party rate-limited us. Retryable with backoff."""

    code: ClassVar[str] = "tool.rate_limit"
    retryable: ClassVar[bool] = True

    retry_after_ms: int | None

    def __init__(
        self,
        message: str,
        tool_name: str = "",
        *,
        retry_after_ms: int | None = None,
        hint: str | None = None,
        docs_url: str | None = None,
    ) -> None:
        super().__init__(message, tool_name=tool_name, hint=hint, docs_url=docs_url)
        self.retry_after_ms = retry_after_ms

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["context"]["retry_after_ms"] = self.retry_after_ms
        return data


class ToolUnavailableError(RetryableToolError):
    """Transient unreachability (DNS, TCP, 5xx). Retryable."""

    code: ClassVar[str] = "tool.unavailable"
    retryable: ClassVar[bool] = True


class ToolCancelledError(PermanentToolError):
    """Tool invocation was cancelled mid-execution via cancel_event. Not retryable."""

    code: ClassVar[str] = "tool.cancelled"
    retryable: ClassVar[bool] = False


class LLMError(OpenAgentsError):
    """Base exception for LLM/provider failures."""

    code: ClassVar[str] = "llm.error"
    retryable: ClassVar[bool] = False


class LLMConnectionError(LLMError):
    """Raised when a provider connection fails."""

    code: ClassVar[str] = "llm.connection"
    retryable: ClassVar[bool] = True


class LLMRateLimitError(LLMError):
    """Raised when a provider rate-limits a request."""

    code: ClassVar[str] = "llm.rate_limit"
    retryable: ClassVar[bool] = True

    retry_after_ms: int | None

    def __init__(
        self,
        message: str = "",
        *,
        retry_after_ms: int | None = None,
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
        self.retry_after_ms = retry_after_ms

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["context"]["retry_after_ms"] = self.retry_after_ms
        return data


class LLMResponseError(LLMError):
    """Raised when a provider returns an invalid response."""

    code: ClassVar[str] = "llm.response"
    retryable: ClassVar[bool] = False


class ModelRetryError(LLMError):
    """Raised when the model should retry with corrected input."""

    code: ClassVar[str] = "llm.model_retry"
    retryable: ClassVar[bool] = False

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

    code: ClassVar[str] = "user.error"
    retryable: ClassVar[bool] = False


class InvalidInputError(UserError):
    """Raised when caller-provided input is invalid."""

    code: ClassVar[str] = "user.invalid_input"
    retryable: ClassVar[bool] = False


class AgentNotFoundError(UserError):
    """Raised when the requested agent does not exist."""

    code: ClassVar[str] = "user.agent_not_found"
    retryable: ClassVar[bool] = False

