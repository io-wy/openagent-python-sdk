# Observability & Logging

The OpenAgents SDK ships a structured logging system in the `openagents.observability` module, bound to the `openagents.*` logger namespace. The system supports:

- Global level control with per-logger overrides
- Rich terminal rendering (requires the `[rich]` extra)
- Redaction of sensitive values at log time (API keys, tokens, passwords, etc.)
- Prefix-based allowlist / blocklist filtering
- Environment-variable-driven configuration — no code changes required

For observing runtime events (tool calls, LLM calls, run lifecycle), see the [Event Bus Observability](#event-bus-observability) section at the bottom of this page.

---

## LoggingConfig Field Reference

`LoggingConfig` is a Pydantic model. All fields can be set via config file or environment variables.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_configure` | `bool` | `false` | If `true`, `configure()` is called automatically during `Runtime.__init__` |
| `level` | `str` | `"INFO"` | Root logger level (`CRITICAL`/`ERROR`/`WARNING`/`INFO`/`DEBUG`/`NOTSET`) |
| `per_logger_levels` | `dict[str, str]` | `{}` | Per-logger level overrides; only loggers in the `openagents.*` namespace are honoured |
| `pretty` | `bool` | `false` | Enable Rich terminal rendering (requires `[rich]` extra) |
| `stream` | `"stdout"` \| `"stderr"` | `"stderr"` | Output stream |
| `include_prefixes` | `list[str]` \| `null` | `null` | Logger allowlist; `null` means all loggers pass |
| `exclude_prefixes` | `list[str]` | `[]` | Logger blocklist; messages from loggers matching any prefix are suppressed |
| `redact_keys` | `list[str]` | `["api_key", "authorization", "token", "secret", "password"]` | Case-insensitive list of key names whose values are masked in log output |
| `max_value_length` | `int` | `500` | Maximum character length for string values in log output; longer values are truncated |
| `show_time` | `bool` | `true` | Show timestamp column in Rich mode |
| `show_path` | `bool` | `false` | Show source file path column in Rich mode |
| `loguru_sinks` | `list[LoguruSinkConfig]` | `[]` | Multi-sink loguru backend (requires `[loguru]` extra); mutually exclusive with `pretty=true`. See [Multi-sink Logging (loguru)](#multi-sink-logging-loguru). |

---

## How to Enable

### Option 1: `auto_configure` in config file

Add a `logging` block to `agent.json` (or any config file) and set `auto_configure: true`. The Runtime will call `configure()` on startup:

```json
{
  "logging": {
    "auto_configure": true,
    "level": "DEBUG",
    "pretty": true,
    "stream": "stderr",
    "show_time": true,
    "show_path": false
  }
}
```

### Option 2: Programmatic call

Use this when you need to configure logging before or independently of Runtime initialization:

```python
from openagents.observability.logging import configure
from openagents.observability.config import LoggingConfig

configure(LoggingConfig(
    level="DEBUG",
    pretty=True,
    per_logger_levels={
        "openagents.llm": "DEBUG",
        "openagents.plugins": "WARNING",
    },
))
```

To build a config entirely from environment variables:

```python
from openagents.observability.logging import configure_from_env

configure_from_env()  # reads all OPENAGENTS_LOG_* env vars
```

### Option 3: Pure environment variables

Set the variables and enable `auto_configure` in your config file (or call `configure_from_env()` in code) — no other code changes needed:

```bash
export OPENAGENTS_LOG_LEVEL=DEBUG
export OPENAGENTS_LOG_PRETTY=true
export OPENAGENTS_LOG_AUTOCONFIGURE=true
```

---

## Environment Variable Reference

All environment variables can be mixed with config-file fields. Environment variables take precedence over the config file (applied via `merge_env_overrides()`).

| Environment Variable | Field | Type | Example |
|---------------------|-------|------|---------|
| `OPENAGENTS_LOG_AUTOCONFIGURE` | `auto_configure` | boolean | `true` |
| `OPENAGENTS_LOG_LEVEL` | `level` | string | `DEBUG` |
| `OPENAGENTS_LOG_LEVELS` | `per_logger_levels` | comma-separated key=value pairs | `openagents.llm=DEBUG,openagents.plugins=WARNING` |
| `OPENAGENTS_LOG_PRETTY` | `pretty` | boolean | `true` |
| `OPENAGENTS_LOG_STREAM` | `stream` | string | `stdout` |
| `OPENAGENTS_LOG_INCLUDE` | `include_prefixes` | comma-separated list | `openagents.runtime,openagents.llm` |
| `OPENAGENTS_LOG_EXCLUDE` | `exclude_prefixes` | comma-separated list | `openagents.observability` |
| `OPENAGENTS_LOG_REDACT` | `redact_keys` | comma-separated list | `api_key,secret,token` |
| `OPENAGENTS_LOG_MAX_VALUE_LENGTH` | `max_value_length` | integer | `200` |
| `OPENAGENTS_LOG_LOGURU_DISABLE` | (runtime switch only) | boolean | `1` — force-downgrade non-empty `loguru_sinks` to a plain `StreamHandler`; CI / debug escape hatch |

Boolean fields accept `1`, `true`, `yes`, `on` (case-insensitive) as truthy; anything else is falsy.

!!! note "loguru_sinks has no env var"
    `loguru_sinks` is a structured list and cannot be expressed via an environment variable; multi-sink configuration must come from a `LoggingConfig` object or a YAML/JSON file. `OPENAGENTS_LOG_LOGURU_DISABLE` is a **downgrade switch only**, not a way to add sinks.

---

## Rich Terminal Rendering

Rich mode renders log lines with colour highlighting, an aligned timestamp column, level badges, and optional source paths. This is ideal for local development and debugging.

### Installation

```bash
uv sync --extra rich
# or
pip install "io-openagent-sdk[rich]"
```

### Enabling

```json
{
  "logging": {
    "auto_configure": true,
    "pretty": true,
    "show_time": true,
    "show_path": false
  }
}
```

Or via environment variable:

```bash
export OPENAGENTS_LOG_PRETTY=true
```

!!! warning "Missing `rich` raises an error"
    If `pretty=true` but `rich` is not installed, `configure()` raises `RichNotInstalledError` immediately with the exact install command. This is intentional — a loud failure is easier to diagnose than a silent fallback to plain text.

### Rich-specific fields

| Field | Description |
|-------|-------------|
| `show_time` | Display a timestamp on each line (default `true`) |
| `show_path` | Display the source filename and line number on each line (default `false`; widens output significantly) |

---

## Multi-sink Logging (loguru)

`loguru_sinks` provides a third output mode — one of the three alternatives alongside the plain `StreamHandler` and the `rich` `RichHandler`. Its unique value is **multi-sink + rotation/retention/compression + `serialize=True` JSON lines**: in a single process you can colour-print to stderr, append a rotating verbose log to disk, and emit structured JSON to a third sink, all from one `LoggingConfig`.

### Installation

```bash
uv sync --extra loguru
# or
pip install "io-openagent-sdk[loguru]"
```

### Enabling

```yaml
logging:
  level: INFO
  pretty: false
  loguru_sinks:
    - target: stderr
      level: INFO
      colorize: true
    - target: .logs/app.log
      level: DEBUG
      rotation: "10 MB"
      retention: "7 days"
      compression: gz
    - target: .logs/events.jsonl
      level: INFO
      serialize: true
      enqueue: true
```

### LoguruSinkConfig field reference

Each sink is configured by a small struct that maps directly onto the named arguments of `loguru.logger.add(...)`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `target` | `str` | (required) | `"stderr"` / `"stdout"` / file path |
| `level` | `str` | `"INFO"` | This sink's own minimum level |
| `format` | `str` \| `null` | `null` | loguru format string; `null` uses loguru's default |
| `serialize` | `bool` | `false` | `true` → emit one JSON line per record |
| `colorize` | `bool` \| `null` | `null` | `null` → loguru auto-detects (terminal colour) |
| `rotation` | `str` \| `null` | `null` | Rotation policy, e.g. `"10 MB"`, `"00:00"`, `"1 week"` |
| `retention` | `str` \| `null` | `null` | Retention duration, e.g. `"7 days"` |
| `compression` | `str` \| `null` | `null` | Compression format, e.g. `"gz"`, `"zip"` |
| `enqueue` | `bool` | `false` | Async sink (in-process queue); useful for multi-threaded code |
| `filter_include` | `list[str]` \| `null` | `null` | Additional logger-name prefix filter (applied after the `_openagents` tag check) |

### Constraints and boundaries

- **Mutually exclusive with `pretty=true`**: setting both raises `pydantic.ValidationError` at config validation time. To get colour output, use a stderr sink with `colorize: true`.
- **User-installed loguru sinks are never touched**: each sink we install carries `record["extra"]["_openagents"] is True` as its filter; sinks that the user's application installs via `from loguru import logger; logger.add(...)` will not receive SDK records, and our sinks will not receive the user's records.
- **`reset_logging()` only removes our sinks**: cleanup is by remembered sink ID; we never invoke the no-arg `loguru.logger.remove()` (which would wipe user-installed sinks too).
- **`OPENAGENTS_LOG_LOGURU_DISABLE=1` escape hatch**: in CI / debug scenarios you can force-downgrade `loguru_sinks` to a plain `StreamHandler` without changing the config. A WARNING is emitted on the `openagents.observability.logging` logger to signal the downgrade.
- **Does not cover the EventBus channel**: `loguru_sinks` only intercepts library `logging.getLogger("openagents.*")` records. RuntimeEvent flow (`FileLoggingEventBus` / `OtelBridge` etc.) is a separate channel and is unaffected.

!!! warning "Missing `loguru` raises an error"
    If `loguru_sinks` is non-empty but `loguru` is not installed, `configure()` raises `LoguruNotInstalledError` immediately with the exact `pip install io-openagent-sdk[loguru]` command. This is intentional — a loud failure beats a silent fallback to plain text. If you need a CI / debug workaround, set `OPENAGENTS_LOG_LOGURU_DISABLE=1`.

---

## Redaction

`RedactFilter` masks the string values of any key whose name (case-insensitive) appears in `redact_keys` at log-emit time. The original data objects are never modified — only log output is affected.

**Default redacted keys**: `api_key`, `authorization`, `token`, `secret`, `password`

**Example**:

```python
import logging
logger = logging.getLogger("openagents.mymodule")
logger.info("Calling API", extra={"api_key": "sk-abc123", "model": "claude-3"})
# Output: api_key=*** model=claude-3
```

**Value truncation**: string values longer than `max_value_length` (default 500) characters are truncated in log output. The original object is unchanged.

**Custom redact keys**:

```json
{
  "logging": {
    "redact_keys": ["api_key", "authorization", "token", "secret", "password", "x_api_secret"]
  }
}
```

---

## Prefix Filtering

Use `include_prefixes` and `exclude_prefixes` to control which loggers appear in output:

```json
{
  "logging": {
    "level": "DEBUG",
    "include_prefixes": ["openagents.runtime", "openagents.llm"],
    "exclude_prefixes": ["openagents.observability"]
  }
}
```

- `include_prefixes: null` (the default) allows all `openagents.*` messages through.
- `exclude_prefixes` beats `include_prefixes`: if a logger name matches both, the blocklist wins.

---

## Per-Logger Level Overrides

Use `per_logger_levels` to make individual loggers more or less verbose than the root level:

```json
{
  "logging": {
    "level": "INFO",
    "per_logger_levels": {
      "openagents.llm": "DEBUG",
      "openagents.plugins.loader": "WARNING"
    }
  }
}
```

!!! note "openagents.* namespace only"
    Logger names outside `openagents.*` in `per_logger_levels` are silently ignored, and a warning is emitted. The SDK never modifies third-party loggers or the root logger.

---

## Idempotency and Hot Reload

`configure()` is idempotent: it is safe to call from `Runtime.reload()`. Each call first runs `reset_logging()` to remove all SDK-installed handlers, then installs fresh handlers for the new config.

`reset_logging()` does the following:

1. Removes all handlers tagged `_openagents_installed=True`
2. Restores `propagate=True` on the `openagents` logger
3. Resets the root logger level to `NOTSET`
4. Resets the level of every child logger that was set via `per_logger_levels`

**SDK as a library**: if the application never calls `configure()`, all `openagents.*` log records are silently discarded (`propagate=True`, no handlers). The application's own logging setup is completely unaffected by the SDK.

---

## Event Bus Observability

Beyond structured logging, runtime events (tool calls, LLM calls, run lifecycle, etc.) propagate through the Event Bus. The following builtin Event Bus implementations support observability integrations:

| `type` key | Description |
|-----------|-------------|
| `file_logging` | Appends runtime events as NDJSON to a file, suitable for offline analysis |
| `otel_bridge` | Maps runtime events to OpenTelemetry spans; integrates with Jaeger, Tempo, etc. |
| `rich_console` | Pretty-prints runtime events to the terminal (requires `[rich]` extra) |

The Event Bus is configured in the top-level `events` field of your config file. See [Configuration Reference](configuration.md).

### Example: file_logging

```json
{
  "events": {
    "type": "file_logging",
    "config": {
      "path": "logs/runtime_events.ndjson"
    }
  }
}
```

### Example: rich_console (for local development)

```json
{
  "events": {
    "type": "rich_console",
    "config": {
      "show_payload": true
    }
  }
}
```

---

## Related Documentation

- [Configuration Reference](configuration.md) — Full JSON schema for the `logging` and `events` blocks
- [Plugin Development Guide](plugin-development.md) — Writing a custom Event Bus plugin
- [Seams & Extension Points](seams-and-extension-points.md) — Decision tree for the `events` seam
