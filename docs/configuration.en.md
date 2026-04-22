# Configuration Reference

This document describes the JSON configuration format accepted by `load_config()` and
`Runtime.from_config()`.

More importantly, it explains which of the three layers each configuration field belongs to:

- App infrastructure
- Agent components and seams
- Product protocol — things that should **not** be modelled in the SDK schema

## 1. Root Structure

The configuration root corresponds to `AppConfig`.

```json
{
  "version": "1.0",
  "runtime": {"type": "default"},
  "session": {"type": "in_memory"},
  "events": {"type": "async"},
  "skills": {"type": "local"},
  "agents": []
}
```

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `version` | string | no | `"1.0"` | Config schema version |
| `runtime` | object | no | `{ "type": "default" }` | Top-level runtime selector |
| `session` | object | no | `{ "type": "in_memory" }` | Top-level session selector |
| `events` | object | no | `{ "type": "async" }` | Top-level event bus selector |
| `skills` | object | no | `{ "type": "local" }` | Top-level skill package manager |
| `agents` | array | **yes** | — | At least one agent is required |

## 2. Selector Rules

OpenAgents has two kinds of selector:

- `type` — selects a builtin plugin or a decorator-registered name
- `impl` — imports a symbol via Python dotted path

### Top-level selectors

The top-level `runtime`, `session`, `events`, and `skills` fields accept exactly one selector.

Valid:

```json
{"runtime": {"type": "default"}}
```

```json
{"runtime": {"impl": "myapp.runtime.CustomRuntime"}}
```

Invalid (both set simultaneously):

```json
{"runtime": {"type": "default", "impl": "myapp.runtime.CustomRuntime"}}
```

### Agent-level selectors

Agent-level selectors must provide at least one of `type` or `impl`.

When both are present, the loader uses `impl`.

Applies to:

- `memory`
- `pattern`
- `tool_executor`
- `context_assembler`
- `tools[]`

!!! warning
    The `execution_policy`, `followup_resolver`, and `response_repair_policy` agent-level
    fields were removed in the 2026-04-18 seam consolidation. The strict schema rejects
    these keys. See the `tool_executor` section below and
    [`docs/seams-and-extension-points.md`](seams-and-extension-points.md) §2 for migration
    guidance.

## 3. Top-level Components

These fields configure the app-level runtime containers, not the agent's business behavior.

### `runtime`

```json
{
  "runtime": {
    "type": "default",
    "config": {}
  }
}
```

Current builtins:

- `default`

### `session`

```json
{
  "session": {
    "type": "in_memory",
    "config": {}
  }
}
```

Current builtins:

- `in_memory`
- `jsonl_file`
- `sqlite` (optional extra: `uv sync --extra sqlite`)

Example `sqlite` configuration (one row per mutation, per-session `asyncio.Lock` for
serialized writes, WAL mode for concurrent reads, cross-process queries via `sqlite3` CLI):

```json
{
  "session": {
    "type": "sqlite",
    "config": {
      "db_path": ".sessions/agent.db",
      "wal": true,
      "synchronous": "NORMAL",
      "busy_timeout_ms": 5000
    }
  }
}
```

!!! warning
    Using `type: "sqlite"` without `aiosqlite` installed raises `PluginLoadError` with
    the message `Install the 'sqlite' extra: uv sync --extra sqlite`.

### `events`

```json
{
  "events": {
    "type": "async",
    "config": {}
  }
}
```

Current builtins:

- `async`
- `file_logging`
- `rich_console` (requires `[rich]` extra: `uv sync --extra rich`)
- `otel_bridge` (optional extra: `uv sync --extra otel`)

#### `rich_console`

Renders each event in color to the terminal while forwarding the event to an inner bus
(subscribers are unaffected).

```json
{
  "events": {
    "type": "rich_console",
    "config": {
      "inner": {"type": "async"},
      "include_events": ["tool.*", "llm.*"],
      "exclude_events": [],
      "show_payload": true,
      "stream": "stderr",
      "redact_keys": ["api_key", "authorization", "token", "secret", "password"],
      "max_value_length": 500,
      "max_history": 10000
    }
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `inner` | object | `{"type": "async"}` | Inner bus selector; events are always forwarded here first |
| `include_events` | list[str] \| null | `null` | fnmatch allowlist; `null` = render all events |
| `exclude_events` | list[str] | `[]` | fnmatch denylist; deny wins over allow |
| `show_payload` | bool | `true` | Whether to render payload content |
| `stream` | `"stdout"` \| `"stderr"` | `"stderr"` | Output stream |
| `redact_keys` | list[str] | (see above) | Payload keys to redact (case-insensitive) |
| `max_value_length` | int | `500` | Max length for payload string values |
| `max_history` | int | `10000` | History buffer size forwarded to the inner bus |

!!! note
    Render failures are logged as warnings and swallowed — event delivery is never
    disrupted. Constructing `rich_console` without the `rich` package installed raises
    `PluginLoadError` with an install hint.

#### `otel_bridge`

Wraps another inner bus. For each `emit` it creates a one-shot OTel span named
`openagents.<event_name>` with payload keys flattened to `oa.<key>` attributes (long
strings are automatically truncated to `max_attribute_chars`). The inner bus always
emits first, so a broken OTel SDK never blocks subscribers.

```json
{
  "events": {
    "type": "otel_bridge",
    "config": {
      "inner": {"type": "async"},
      "tracer_name": "openagents",
      "include_events": ["tool.*", "llm.*"],
      "max_attribute_chars": 4096
    }
  }
}
```

`include_events` uses `fnmatch`-style globs; `None` means no filtering. The host process
must configure a `TracerProvider` via `opentelemetry-sdk`; without one the OTel API is a
no-op and the bridge has zero cost.

### `skills`

```json
{
  "skills": {
    "type": "local",
    "config": {
      "search_paths": ["skills"],
      "enabled": ["openagent-agent-builder"]
    }
  }
}
```

Current builtins:

- `local`

### `logging` (optional)

The `logging` section is at the **root `AppConfig` level**, not inside `runtime.config`.

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_configure` | bool | `false` | If `true`, `Runtime.__init__` calls `configure()` automatically |
| `level` | str | `"INFO"` | Root log level for `openagents.*` |
| `per_logger_levels` | dict[str, str] | `{}` | Per-logger level overrides, e.g. `{"openagents.llm": "DEBUG"}` |
| `pretty` | bool | `false` | Enable rich-rendered logs (requires `[rich]` extra) |
| `stream` | `"stdout"` \| `"stderr"` | `"stderr"` | Output stream |
| `include_prefixes` | list[str] \| null | `null` | Logger allowlist (`null` = allow all) |
| `exclude_prefixes` | list[str] | `[]` | Logger denylist |
| `redact_keys` | list[str] | `["api_key", "authorization", "token", "secret", "password"]` | Keys to redact (case-insensitive) |
| `max_value_length` | int | `500` | String value truncation length |
| `show_time` | bool | `true` | Show timestamp column (rich mode only) |
| `show_path` | bool | `false` | Show source path column (rich mode only) |

If this section is absent or `auto_configure` is `false`, the SDK does not modify any
logging configuration.

## 4. AgentDefinition

A typical agent definition:

```json
{
  "id": "assistant",
  "name": "demo-agent",
  "memory": {"type": "window_buffer"},
  "pattern": {"type": "react"},
  "llm": {"provider": "mock"},
  "tool_executor": {"type": "filesystem_aware", "config": {"read_roots": ["./src"]}},
  "context_assembler": {"type": "head_tail"},
  "tools": [],
  "runtime": {
    "max_steps": 16,
    "step_timeout_ms": 30000,
    "session_queue_size": 1000,
    "event_queue_size": 2000
  }
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | **yes** | Used by the runtime to locate the agent |
| `name` | string | **yes** | Display name |
| `memory` | object | **yes** | Memory selector |
| `pattern` | object | **yes** | Pattern selector |
| `llm` | object | no | LLM provider configuration |
| `tool_executor` | object | no | Tool execution seam (includes `evaluate_policy`) |
| `context_assembler` | object | no | Context assembly seam |
| `tools` | array | no | List of tool selectors |
| `runtime` | object | no | Per-agent runtime limits — **not** the runtime plugin selector |

!!! note
    `output_type` (a Pydantic model for structured output) and
    `budget.max_validation_retries` are passed in the **`RunRequest`** at call time, not
    in the JSON config file. See [Structured Output (RunRequest Fields)](#runrequest).

## 5. agent.runtime

`agent.runtime` holds per-agent execution limits.

```json
{
  "runtime": {
    "max_steps": 16,
    "step_timeout_ms": 30000,
    "session_queue_size": 1000,
    "event_queue_size": 2000
  }
}
```

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `max_steps` | int | `16` | Maximum number of logical steps |
| `step_timeout_ms` | int | `30000` | Per-step timeout in milliseconds |
| `session_queue_size` | int | `1000` | Schema-level field (not consumed by builtin runtime) |
| `event_queue_size` | int | `2000` | Schema-level field (not consumed by builtin runtime) |

!!! note
    All fields must be positive integers. `max_tool_calls` and `max_duration_ms` are
    passed via `RunRequest.budget`, not in the JSON config file.

## 6. Memory

```json
{
  "memory": {
    "type": "window_buffer",
    "config": {
      "window_size": 20
    },
    "on_error": "continue"
  }
}
```

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `type` / `impl` | string | — | Selector |
| `config` | object | `{}` | Plugin-specific configuration |
| `on_error` | string | `"continue"` | Must be `"continue"` or `"fail"` |

Builtin memory plugins:

- `buffer` — append-only in-session memory
- `window_buffer` — sliding-window version of buffer
- `mem0` — semantic memory backend
- `chain` — composes multiple memory plugins

## 7. Pattern

```json
{
  "pattern": {
    "type": "react",
    "config": {
      "max_steps": 8,
      "step_timeout_ms": 30000
    }
  }
}
```

Builtin patterns:

- `react` — JSON action loop; can fall back without an LLM
- `plan_execute` — plan first, then execute
- `reflexion` — reflects on recent tool results and retries

Common pattern config keys:

- `max_steps`
- `step_timeout_ms`

The `react` pattern additionally supports:

- `tool_prefix`
- `echo_prefix`

## 8. LLM

`llm` is optional. If omitted, the selected pattern must be able to run without an
`llm_client`.

```json
{
  "llm": {
    "provider": "openai_compatible",
    "model": "gpt-4o-mini",
    "api_base": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "temperature": 0.2,
    "max_tokens": 512,
    "timeout_ms": 30000
  }
}
```

Supported providers:

- `mock`
- `anthropic`
- `openai_compatible`
- `litellm` (optional extra, see "LiteLLM Provider" section below)

Validation rules:

- `provider` must be one of the supported values
- `openai_compatible` requires `api_base`
- `timeout_ms` must be a positive integer
- `max_tokens`, if provided, must be a positive integer
- `temperature`, if provided, must be between `0.0` and `2.0`

### LiteLLM Provider (Optional)

`provider: "litellm"` reaches **non-OpenAI protocol** backends through [LiteLLM](https://docs.litellm.ai): AWS Bedrock, Google Vertex AI, native Gemini, Cohere, Azure OpenAI deployments. **If your backend already speaks the OpenAI protocol, prefer `openai_compatible`** — it's lighter.

Install:

```bash
uv pip install "io-openagent-sdk[litellm]"
```

Bedrock example:

```json
{
  "llm": {
    "provider": "litellm",
    "model": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    "aws_region_name": "us-east-1",
    "max_tokens": 4096,
    "pricing": {"input": 3.0, "output": 15.0}
  }
}
```

Vertex example:

```json
{
  "llm": {
    "provider": "litellm",
    "model": "vertex_ai/gemini-1.5-pro",
    "vertex_project": "my-gcp-project",
    "vertex_location": "us-central1"
  }
}
```

Native Gemini example:

```json
{
  "llm": {
    "provider": "litellm",
    "model": "gemini/gemini-1.5-pro",
    "api_key_env": "GEMINI_API_KEY"
  }
}
```

**Forwarded kwargs whitelist** (other extras are dropped with a warning):
`aws_region_name`, `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_profile_name`, `vertex_project`, `vertex_location`, `vertex_credentials`, `azure_deployment`, `api_version`, `seed`, `top_p`, `parallel_tool_calls`, `response_format`.

**Unsupported LiteLLM features** (intentionally excluded as product-layer concerns): router, fallback, budget manager, built-in cache, success/failure callbacks.

**Credentials:** if `api_key_env` is set the SDK reads that env and passes `api_key=...`. Otherwise LiteLLM reads its own standard env chain (`AWS_ACCESS_KEY_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, etc.).

**Telemetry:** instantiating `LiteLLMClient` disables LiteLLM telemetry and success/failure callbacks process-wide, and sets `drop_params = True` to silently drop unknown kwargs.

### `pricing` (optional)

The `pricing` field overrides the provider's default token costs for cost tracking. All
prices are in US dollars per million tokens.

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-3-5-sonnet-20241022",
    "pricing": {
      "input": 3.0,
      "output": 15.0,
      "cached_read": 0.30,
      "cached_write": 3.75
    }
  }
}
```

| Field | Type | Description |
|---|---|---|
| `input` | float \| null | Input token price ($/M tokens) |
| `output` | float \| null | Output token price ($/M tokens) |
| `cached_read` | float \| null | Cache read price ($/M, prompt caching) |
| `cached_write` | float \| null | Cache write price ($/M, prompt caching) |

## 9. Tools

Single tool configuration example:

```json
{
  "id": "search",
  "type": "builtin_search",
  "enabled": true,
  "config": {}
}
```

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `id` | string | **yes** | — | ID used by the pattern when invoking the tool |
| `type` / `impl` | string | conditional | — | At least one selector is required |
| `enabled` | boolean | no | `true` | If `false`, the tool is not loaded |
| `config` | object | no | `{}` | Plugin-specific configuration |

Builtin tool IDs:

- Search: `builtin_search`
- Files: `read_file`, `write_file`, `list_files`, `delete_file`
- Text: `grep_files`, `ripgrep`, `json_parse`, `text_transform`
- HTTP / network: `http_request`, `url_parse`, `url_build`, `query_param`, `host_lookup`
- System: `execute_command`, `get_env`, `set_env`
- Time: `current_time`, `date_parse`, `date_diff`
- Random: `random_int`, `random_choice`, `random_string`, `uuid`
- Math: `calc`, `percentage`, `min_max`
- MCP bridge: `mcp`

## 10. Agent-level Execution Seams

These fields are declared under the agent, not at the top level.

### `tool_executor`

```json
{
  "tool_executor": {
    "type": "safe",
    "config": {
      "default_timeout_ms": 2000
    }
  }
}
```

Use cases:

- Parameter validation
- Timeouts
- Stream passthrough
- Error normalization
- Tool access control (override `evaluate_policy()`)

Builtin executors:

- **`safe`** — basic timeout + error normalization, no access control

  ```json
  {
    "tool_executor": {
      "type": "safe",
      "config": {
        "default_timeout_ms": 30000,
        "allow_stream_passthrough": true
      }
    }
  }
  ```

- **`retry`** — wraps an inner executor with exponential backoff per error type

- **`filesystem_aware`** — embeds `FilesystemExecutionPolicy`; replaces the old
  `execution_policy: filesystem` usage:

  ```json
  {
    "tool_executor": {
      "type": "filesystem_aware",
      "config": {
        "read_roots": ["./workspace"],
        "write_roots": ["./workspace"],
        "allow_tools": ["read_file", "write_file", "list_files"],
        "deny_tools": []
      }
    }
  }
  ```

  | Field | Type | Default | Description |
  |---|---|---|---|
  | `read_roots` | list[str] | `[]` | Allowed read path prefixes; empty = no restriction |
  | `write_roots` | list[str] | `[]` | Allowed write path prefixes; empty = no restriction |
  | `allow_tools` | list[str] | `[]` | Tool ID allowlist; **empty = allow all tools** |
  | `deny_tools` | list[str] | `[]` | Tool ID denylist; deny wins over allow |

  !!! note
      An empty `allow_tools` means **all tools are allowed**. To restrict to a subset,
      list the permitted tool IDs explicitly. `deny_tools` always takes precedence over
      `allow_tools`.

For multi-policy combinations (e.g. filesystem + network allowlist), write a custom
`ToolExecutorPlugin` subclass and override `evaluate_policy()`, composing helpers from
`openagents.plugins.builtin.execution_policy`
(`FilesystemExecutionPolicy` / `NetworkAllowlistExecutionPolicy` / `CompositePolicy`).
See `examples/research_analyst/app/executor.py`.

### `context_assembler`

```json
{
  "context_assembler": {
    "type": "head_tail",
    "config": {
      "head_messages": 4,
      "tail_messages": 8,
      "include_summary_message": true
    }
  }
}
```

Use cases:

- Transcript trimming
- Artifact trimming
- Assembly metadata injection
- App-defined context packets

Builtins: `truncating`, `head_tail`, `sliding_window`, `importance_weighted`

## 11. Structured Output (RunRequest Fields) {#runrequest}

`output_type` and related budget fields are **runtime call-time parameters** — they are
not declared in the JSON config file. Pass them via `RunRequest` on each call:

```python
from pydantic import BaseModel
from openagents.interfaces.runtime import RunRequest, RunBudget

class MyOutput(BaseModel):
    answer: str
    confidence: float

request = RunRequest(
    agent_id="assistant",
    session_id="s1",
    input_text="hello",
    output_type=MyOutput,          # Pydantic model for structured output
    budget=RunBudget(
        max_steps=8,
        max_validation_retries=3,  # retries when structured output validation fails
        max_duration_ms=60000,
    ),
)
result = await runtime.run_detailed(request)
```

| Field | Type | Description |
|---|---|---|
| `output_type` | `type[BaseModel]` \| null | Pydantic model for structured output; `None` = plain text |
| `budget.max_steps` | int | Overrides the `max_steps` in the agent config |
| `budget.max_validation_retries` | int | Maximum retries when structured output fails validation |
| `budget.max_duration_ms` | int \| null | Overall run timeout |

## 12. Follow-up / Empty-response Fallbacks (PatternPlugin Method Overrides) {#followup}

The previously independent `followup_resolver` and `response_repair_policy` seams were
consolidated into two optional method overrides on `PatternPlugin`. To short-circuit
follow-up answers locally or to degrade gracefully on empty responses, subclass your
`PatternPlugin` and override them:

```python
class MyPattern(ReActPattern):
    async def resolve_followup(self, *, context):
        # Return FollowupResolution(status="resolved", output=...) to short-circuit the LLM
        return None  # abstain -> fall through to the LLM loop

    async def repair_empty_response(self, *, context, messages, assistant_content, stop_reason, retries):
        # Return ResponseRepairDecision(status="repaired", output=...) or status="error"
        return None  # abstain -> let the empty response propagate
```

References:

- `examples/research_analyst/app/followup_pattern.py` — rule-based follow-up override
- `examples/production_coding_agent/app/plugins.py` — coding journal follow-up + error-mode repair

## 13. Seam Defaults in `runtime.config`

The builtin `default` runtime supports declaring seam defaults inside `runtime.config`.

```json
{
  "runtime": {
    "type": "default",
    "config": {
      "tool_executor": {
        "type": "safe",
        "config": {"default_timeout_ms": 1000}
      },
      "context_assembler": {
        "type": "head_tail",
        "config": {"head_messages": 4, "tail_messages": 8}
      }
    }
  }
}
```

Priority rules:

- If the agent declares its own seam, the agent-level config wins.
- If not, the builtin runtime falls back to the runtime-level default.

Use cases:

- Multiple agents sharing the same default execution policy
- Avoiding repeated seam configuration on every agent

## 14. Decorator Registration

The following categories support the decorator registry:

- `tool`
- `memory`
- `pattern`
- `runtime`
- `skill`
- `session`
- `event_bus`
- `tool_executor`
- `context_assembler`

!!! note
    Decorator registration is process-local. The module declaring the decorator must be
    imported before config load, or the type name will not resolve.

## 15. What Should Not Go in the Config Schema

The SDK config should not model all product protocol. Things that typically do not belong
in the schema:

- Coding-task DSL
- Review contracts
- Mailbox semantics
- Team routing policy
- UI workflow state
- Product state trees

These belong in the app-defined protocol layer.

## 16. Further Reading

- [Developer Guide](developer-guide.md)
- [Seams and Extension Points](seams-and-extension-points.md)
- [Plugin Development](plugin-development.md)
- [API Reference](api-reference.md)
- [Examples](examples.md)
