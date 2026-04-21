"""Schema models for config-as-code agent definitions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

from openagents.errors import ConfigValidationError
from openagents.observability.config import LoggingConfig


def _require_dict(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"'{field_name}' must be an object")
    return value


def _clean_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"'{field_name}' must be a string")
    stripped = value.strip()
    return stripped or None


def _clean_required_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string")
    return value.strip()


class PluginRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    impl: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type", "impl", mode="before")
    @classmethod
    def _validate_selector_field(cls, value: Any, info: Any) -> str | None:
        return _clean_optional_str(value, info.field_name)

    @field_validator("config", mode="before")
    @classmethod
    def _validate_config_dict(cls, value: Any) -> dict[str, Any]:
        return _require_dict(value, "config")

    def validate_selector(self, where: str) -> None:
        has_type = bool(self.type)
        has_impl = bool(self.impl)
        if not has_type and not has_impl:
            raise ConfigValidationError(f"'{where}' must set at least one of 'type' or 'impl'")
        if has_type and has_impl:
            raise ConfigValidationError(f"'{where}' must set only one of 'type' or 'impl'")


class MemoryRef(PluginRef):
    on_error: Literal["continue", "fail"] = "continue"


class PatternRef(PluginRef):
    pass


class ToolRef(PluginRef):
    id: str
    enabled: bool = True

    @field_validator("id", mode="before")
    @classmethod
    def _validate_tool_id(cls, value: Any) -> str:
        return _clean_required_str(value, "tool.id")


class ToolExecutorRef(PluginRef):
    pass


class ContextAssemblerRef(PluginRef):
    pass


class RuntimeRef(PluginRef):
    """Runtime plugin reference at global level."""


class SessionRef(PluginRef):
    """Session manager plugin reference at global level."""


class EventBusRef(PluginRef):
    """Event bus plugin reference at global level."""


class SkillsRef(PluginRef):
    """Host-level skills component reference."""


class DiagnosticsRef(PluginRef):
    """Diagnostics plugin reference at global level."""

    error_snapshot_last_n: int = 10
    redact_keys: list[str] = Field(
        default_factory=lambda: [
            "api_key",
            "token",
            "secret",
            "password",
            "authorization",
        ]
    )


class RuntimeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: PositiveInt = 16
    step_timeout_ms: PositiveInt = 30000
    session_queue_size: PositiveInt = 1000
    event_queue_size: PositiveInt = 2000


class LLMPricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float | None = None
    output: float | None = None
    cached_read: float | None = None
    cached_write: float | None = None


class LLMRetryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: PositiveInt = 3
    initial_backoff_ms: PositiveInt = 500
    max_backoff_ms: PositiveInt = 5000
    backoff_multiplier: float = 2.0
    retry_on_connection_errors: bool = True
    total_budget_ms: PositiveInt | None = None

    @field_validator("backoff_multiplier", mode="before")
    @classmethod
    def _validate_backoff_multiplier(cls, value: Any) -> float:
        if not isinstance(value, (int, float)):
            raise ValueError("'llm.retry.backoff_multiplier' must be a number")
        fvalue = float(value)
        if fvalue < 1.0:
            raise ValueError("'llm.retry.backoff_multiplier' must be >= 1.0")
        return fvalue


class LLMOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "mock"
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    max_tokens: PositiveInt | None = None
    timeout_ms: PositiveInt = 30000
    stream_endpoint: str | None = None
    pricing: LLMPricing | None = None
    retry: LLMRetryOptions | None = None
    extra_headers: dict[str, str] | None = None
    reasoning_model: bool | None = None
    openai_api_style: Literal["chat_completions", "responses"] | None = None

    @field_validator("extra_headers", mode="before")
    @classmethod
    def _validate_extra_headers(cls, value: Any) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("'llm.extra_headers' must be an object of string->string")
        cleaned: dict[str, str] = {}
        for key, val in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("'llm.extra_headers' keys must be non-empty strings")
            if not isinstance(val, str):
                raise ValueError(f"'llm.extra_headers[{key!r}]' must be a string")
            cleaned[key] = val
        return cleaned or None

    @field_validator("provider", mode="before")
    @classmethod
    def _validate_provider(cls, value: Any) -> str:
        if value is None:
            return "mock"
        return _clean_required_str(value, "llm.provider")

    @field_validator("model", "api_base", "api_key_env", "stream_endpoint", mode="before")
    @classmethod
    def _validate_optional_strings(cls, value: Any, info: Any) -> str | None:
        return _clean_optional_str(value, f"llm.{info.field_name}")

    @field_validator("temperature", mode="before")
    @classmethod
    def _validate_temperature(cls, value: Any) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)):
            raise ValueError("'llm.temperature' must be a number when provided")
        return float(value)

    @model_validator(mode="after")
    def _validate_llm_rules(self) -> "LLMOptions":
        allowed = {"anthropic", "mock", "openai_compatible"}
        if self.provider not in allowed:
            raise ConfigValidationError(f"'llm.provider' must be one of {sorted(allowed)}")
        if self.provider == "openai_compatible" and not self.api_base:
            raise ConfigValidationError("'llm.api_base' is required for provider 'openai_compatible'")
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ConfigValidationError("'llm.temperature' must be between 0.0 and 2.0")
        return self


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    memory: MemoryRef
    pattern: PatternRef
    llm: LLMOptions | None = None
    tool_executor: ToolExecutorRef | None = None
    context_assembler: ContextAssemblerRef | None = None
    tools: list[ToolRef] = Field(default_factory=list)
    runtime: RuntimeOptions = Field(default_factory=RuntimeOptions)

    @field_validator("id", "name", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: Any, info: Any) -> str:
        return _clean_required_str(value, f"agent.{info.field_name}")

    @field_validator("tools", mode="before")
    @classmethod
    def _validate_tools_list(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("'tools' must be an array")
        return value

    @model_validator(mode="after")
    def _validate_agent_rules(self) -> "AgentDefinition":
        self.memory.validate_selector(f"agents['{self.id}'].memory")
        self.pattern.validate_selector(f"agents['{self.id}'].pattern")

        optional_refs = {
            "tool_executor": self.tool_executor,
            "context_assembler": self.context_assembler,
        }
        for field_name, ref in optional_refs.items():
            if ref is not None:
                ref.validate_selector(f"agents['{self.id}'].{field_name}")

        seen_tool_ids: set[str] = set()
        for tool in self.tools:
            tool.validate_selector(f"agents['{self.id}'].tools['{tool.id}']")
            if tool.id in seen_tool_ids:
                raise ConfigValidationError(f"Duplicate tool id '{tool.id}' in agent '{self.id}'")
            seen_tool_ids.add(tool.id)
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    agents: list[AgentDefinition] = Field(default_factory=list)
    runtime: RuntimeRef = Field(default_factory=lambda: RuntimeRef(type="default"))
    session: SessionRef = Field(default_factory=lambda: SessionRef(type="in_memory"))
    events: EventBusRef = Field(default_factory=lambda: EventBusRef(type="async"))
    skills: SkillsRef = Field(default_factory=lambda: SkillsRef(type="local"))
    logging: LoggingConfig | None = None
    diagnostics: DiagnosticsRef | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_root_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("Config root must be an object")
        agents = value.get("agents", [])
        if not isinstance(agents, list):
            raise ValueError("'agents' must be an array")
        return value

    @field_validator("version", mode="before")
    @classmethod
    def _validate_version(cls, value: Any) -> str:
        if value is None:
            return "1.0"
        return _clean_required_str(value, "version")

    @model_validator(mode="after")
    def _validate_config_rules(self) -> "AppConfig":
        if not self.agents:
            raise ConfigValidationError("'agents' must contain at least one item")

        self.runtime.validate_selector("runtime")
        self.session.validate_selector("session")
        self.events.validate_selector("events")
        self.skills.validate_selector("skills")
        if self.diagnostics is not None:
            self.diagnostics.validate_selector("diagnostics")
        return self
