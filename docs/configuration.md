# 配置参考

这份文档描述 `load_config()` 和 `Runtime.from_config()` 当前接受的 JSON 配置格式。

更重要的是，它解释配置分别落在哪三层：

- app infrastructure
- agent 组件与 seam
- 不应该被 SDK schema 建模的产品协议

## 1. 根结构

配置根对象对应 `AppConfig`。

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

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `version` | string | 否 | `"1.0"` | 配置版本 |
| `runtime` | object | 否 | `{ "type": "default" }` | 顶层 runtime selector |
| `session` | object | 否 | `{ "type": "in_memory" }` | 顶层 session selector |
| `events` | object | 否 | `{ "type": "async" }` | 顶层 event bus selector |
| `skills` | object | 否 | `{ "type": "local" }` | 顶层 skill package manager |
| `agents` | array | 是 | 无 | 至少要有一个 agent |

## 2. Selector 规则

OpenAgents 有两种 selector：

- `type`
  - 选择 builtin plugin 或 decorator 注册名
- `impl`
  - 通过 Python dotted path 导入符号

### 顶层 selector

顶层 `runtime`、`session`、`events`、`skills` 只能提供一个 selector。

合法：

```json
{"runtime": {"type": "default"}}
```

```json
{"runtime": {"impl": "myapp.runtime.CustomRuntime"}}
```

非法：

```json
{"runtime": {"type": "default", "impl": "myapp.runtime.CustomRuntime"}}
```

### agent 级 selector

agent 级 selector 至少要提供一个 `type` 或 `impl`。

如果两者同时出现，loader 以 `impl` 为准。

适用范围：

- `memory`
- `pattern`
- `tool_executor`
- `context_assembler`
- `tools[]`

> Note: `execution_policy`、`followup_resolver`、`response_repair_policy`
> agent 级字段已在 2026-04-18 seam 合并中移除，strict schema 会拒绝这些 key。
> 迁移方式见下文 `tool_executor` 段、以及
> [`docs/seams-and-extension-points.md`](seams-and-extension-points.md) §2。

## 3. 顶层组件

这些字段配置的是 app 级运行容器，不是 agent 自己的业务行为。

### `runtime`

```json
{
  "runtime": {
    "type": "default",
    "config": {}
  }
}
```

当前 builtin：

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

当前 builtin：

- `in_memory`
- `jsonl_file`
- `sqlite`（可选 extra：`uv sync --extra sqlite`）

`sqlite` 配置示例（每个 mutation 一行，per-session asyncio.Lock 串行写、
WAL 模式让多 reader 可并发读，跨进程的查询直接用 `sqlite3` CLI）：

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

未装 `aiosqlite` 就用 `type: "sqlite"`，构造时会抛 `PluginLoadError`
并附带 `Install the 'sqlite' extra: uv sync --extra sqlite` 提示。

### `events`

```json
{
  "events": {
    "type": "async",
    "config": {}
  }
}
```

当前 builtin：

- `async`
- `file_logging`
- `rich_console`（需要 `[rich]` extra：`uv sync --extra rich`）
- `otel_bridge`（可选 extra：`uv sync --extra otel`）

#### `rich_console`

在终端以彩色格式渲染每条事件，同时把事件透传给 inner bus（subscriber 不受影响）。

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

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `inner` | object | `{"type": "async"}` | inner bus selector，事件总是先转发到 inner |
| `include_events` | list[str] \| null | `null` | fnmatch 白名单，`null` = 所有事件 |
| `exclude_events` | list[str] | `[]` | fnmatch 黑名单，deny 优先于 allow |
| `show_payload` | bool | `true` | 是否渲染 payload 内容 |
| `stream` | `"stdout"` \| `"stderr"` | `"stderr"` | 输出流 |
| `redact_keys` | list[str] | （见上方）| 脱敏字段名（大小写不敏感） |
| `max_value_length` | int | `500` | payload 值截断长度 |
| `max_history` | int | `10000` | 转发给 inner bus 的 history 大小 |

!!! note
    渲染失败只 log warning，不会阻断事件流。未安装 `rich` 包时构造会抛
    `PluginLoadError` 并附带安装提示。

#### `otel_bridge`

`otel_bridge` 包另一个 inner bus，对每个 emit 创建一个一次性的 OTel
span，名为 `openagents.<event_name>`，payload 各 key 平铺成 `oa.<key>`
attribute（长 string 自动截断到 `max_attribute_chars`）。
inner bus 总是先 emit，所以即便 OTel SDK 出问题也不会阻塞 subscribers。

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

`include_events` 用 `fnmatch` 风格通配，`None` 表示不过滤。host 进程
需自行通过 `opentelemetry-sdk` 配置一个 TracerProvider；没配置时
OTel API 会 no-op，bridge 等于零成本。

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

当前 builtin：

- `local`

### `logging`（可选）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `auto_configure` | bool | `false` | 是否让 `Runtime.__init__` 自动调 `configure()` |
| `level` | str | `"INFO"` | `openagents.*` 根 level |
| `per_logger_levels` | dict[str, str] | `{}` | 按 logger 名覆盖 level，如 `{"openagents.llm": "DEBUG"}` |
| `pretty` | bool | `false` | 启用 rich 渲染（需要 `[rich]` extra） |
| `stream` | `"stdout"` \| `"stderr"` | `"stderr"` | 输出流 |
| `include_prefixes` | list[str] \| null | `null` | logger 白名单（`null` = 允许所有） |
| `exclude_prefixes` | list[str] | `[]` | logger 黑名单 |
| `redact_keys` | list[str] | `["api_key", "authorization", "token", "secret", "password"]` | 脱敏 key 名（大小写不敏感） |
| `max_value_length` | int | `500` | 字符串 value 截断长度 |
| `show_time` | bool | `true` | 是否显示时间列（rich 模式） |
| `show_path` | bool | `false` | 是否显示代码路径（rich 模式） |
| `loguru_sinks` | list[LoguruSinkConfig] | `[]` | 多 sink loguru 后端（需要 `[loguru]` extra）；与 `pretty=true` **互斥**。详见下文 LoguruSinkConfig 字段表 |

如果该 section 缺失或 `auto_configure=false`，SDK 不会修改任何 logging 配置。

#### LoguruSinkConfig 字段表

每个 sink 是一组配置，全部映射到 `loguru.logger.add(...)` 的同名参数。`null` 表示走 loguru 默认值。

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `target` | str | （必填） | `"stderr"` / `"stdout"` / 文件路径 |
| `level` | str | `"INFO"` | 该 sink 的级别下限 |
| `format` | str \| null | `null` | loguru format 字符串 |
| `serialize` | bool | `false` | `true` → 每条记录输出为一行 JSON |
| `colorize` | bool \| null | `null` | `null` → loguru 自动检测（终端着色） |
| `rotation` | str \| null | `null` | 轮转策略，例如 `"10 MB"`、`"00:00"`、`"1 week"` |
| `retention` | str \| null | `null` | 保留时长，例如 `"7 days"` |
| `compression` | str \| null | `null` | 压缩格式，例如 `"gz"`、`"zip"` |
| `enqueue` | bool | `false` | 异步 sink（进程内队列） |
| `filter_include` | list[str] \| null | `null` | 进一步按 logger 名前缀过滤 |

环境变量补充：
- `OPENAGENTS_LOG_LOGURU_DISABLE`：设为 `1`/`true`/`yes`/`on` 时，强制把非空 `loguru_sinks` 降级为纯文本 `StreamHandler`，CI / debug 用。`loguru_sinks` 列表本身**不**可通过环境变量配置。

## 4. AgentDefinition

一个 agent 定义大概长这样：

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

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | runtime 定位 agent 用 |
| `name` | string | 是 | 展示名称 |
| `memory` | object | 是 | memory selector |
| `pattern` | object | 是 | pattern selector |
| `llm` | object | 否 | provider 配置 |
| `tool_executor` | object | 否 | tool 执行 seam（含 `evaluate_policy`） |
| `context_assembler` | object | 否 | context seam |
| `tools` | array | 否 | tool 列表 |
| `runtime` | object | 否 | agent 级运行限制，不是 runtime plugin selector |

!!! note
    `output_type`（结构化输出的 Pydantic model）和 `budget.max_validation_retries`
    是在 **`RunRequest`** 调用时传入的，不在 JSON 配置文件里声明。
    详见[结构化输出配置（RunRequest 字段）](#runrequest)一节。

## 5. agent.runtime

`agent.runtime` 是这个 agent 的运行限制配置。

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

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `max_steps` | int | `16` | 逻辑 step 上限 |
| `step_timeout_ms` | int | `30000` | 单 step timeout |
| `session_queue_size` | int | `1000` | 目前主要是 schema 级字段 |
| `event_queue_size` | int | `2000` | 目前主要是 schema 级字段 |

注意：

- 这些字段都必须是正整数
- builtin `DefaultRuntime` 当前直接消费 `max_steps` 和 `step_timeout_ms`
- `session_queue_size`、`event_queue_size` 当前会被校验，但 builtin runtime 不直接消费
- `max_tool_calls` 和 `max_duration_ms` 通过 `RunRequest.budget` 传入，不在 JSON 配置文件里

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

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `type` / `impl` | string | 无 | selector |
| `config` | object | `{}` | memory 自己消费的配置 |
| `on_error` | string | `"continue"` | 只能是 `continue` 或 `fail` |

builtin memory：

- `buffer`
  - append-only in-session memory
- `window_buffer`
  - 最近窗口版 buffer
- `mem0`
  - 语义记忆 backend
- `chain`
  - 组合多个 memory

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

builtin pattern：

- `react`
  - JSON action loop
  - 没有 LLM 时也能 fallback
- `plan_execute`
  - 先 plan 再 execute
- `reflexion`
  - 基于最近 tool result 做反思和重试

常见 pattern config：

- `max_steps`
- `step_timeout_ms`

`react` 额外支持：

- `tool_prefix`
- `echo_prefix`

## 8. LLM

`llm` 是可选字段。  
如果省略，所选 pattern 必须能在没有 `llm_client` 的情况下运行。

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

支持的 provider：

- `mock`
- `anthropic`
- `openai_compatible`
- `litellm`（可选 extra，详见下方"LiteLLM Provider"小节）

校验规则：

- `provider` 必须是支持值之一
- `openai_compatible` 必须提供 `api_base`
- `timeout_ms` 必须是正整数
- `max_tokens` 如果提供，必须是正整数
- `temperature` 如果提供，必须在 `0.0` 到 `2.0` 之间

### LiteLLM Provider（可选）

`provider: "litellm"` 通过 [LiteLLM](https://docs.litellm.ai) 对接**非 OpenAI 协议**的后端：AWS Bedrock、Google Vertex AI、Gemini 原生 API、Cohere、Azure OpenAI deployment 等。**如果后端已经是 OpenAI 兼容协议，优先使用 `openai_compatible`**，更轻量。

安装：

```bash
uv pip install "io-openagent-sdk[litellm]"
```

Bedrock 示例：

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

Vertex 示例：

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

Gemini 原生示例：

```json
{
  "llm": {
    "provider": "litellm",
    "model": "gemini/gemini-1.5-pro",
    "api_key_env": "GEMINI_API_KEY"
  }
}
```

**透传白名单**（其他 extra 字段会被忽略并告警）：
`aws_region_name` · `aws_access_key_id` · `aws_secret_access_key` · `aws_session_token` · `aws_profile_name` · `vertex_project` · `vertex_location` · `vertex_credentials` · `azure_deployment` · `api_version` · `seed` · `top_p` · `parallel_tool_calls` · `response_format`

**不支持的 LiteLLM 特性**（本 SDK 不接入，属于应用层产品语义）：router、fallback、budget manager、内置缓存、success/failure callbacks。

**凭证**：`api_key_env` 给了就读环境变量塞到 `api_key`；没给则由 LiteLLM 自行从 AWS/GCP 标准环境变量链读取凭证（如 `AWS_ACCESS_KEY_ID`、`GOOGLE_APPLICATION_CREDENTIALS` 等）。

**Telemetry**：实例化 `LiteLLMClient` 会在进程级禁用 LiteLLM 的 telemetry 与 success/failure callbacks，并开启 `drop_params` 以丢弃未知 kwarg。

### `pricing`（可选）

可以通过 `pricing` 字段覆盖 provider 默认定价，用于统计 cost 信息。
单位：每百万 token 的美元价格。

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

| 字段 | 类型 | 说明 |
|---|---|---|
| `input` | float \| null | 输入 token 单价（$/M） |
| `output` | float \| null | 输出 token 单价（$/M） |
| `cached_read` | float \| null | cache read 单价（$/M，prompt caching） |
| `cached_write` | float \| null | cache write 单价（$/M，prompt caching） |

## 9. Tools

单个 tool 配置示例：

```json
{
  "id": "search",
  "type": "builtin_search",
  "enabled": true,
  "config": {}
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `id` | string | 是 | 无 | pattern 调用时使用的 id |
| `type` / `impl` | string | 条件必填 | 无 | 至少要有一个 selector |
| `enabled` | boolean | 否 | `true` | `false` 时不会被加载 |
| `config` | object | 否 | `{}` | tool 自己消费的配置 |

builtin tool id：

- Search：`builtin_search`
- Files：`read_file`、`write_file`、`list_files`、`delete_file`
- Text：`grep_files`、`ripgrep`、`json_parse`、`text_transform`
- HTTP / network：`http_request`、`url_parse`、`url_build`、`query_param`、`host_lookup`
- System：`execute_command`、`get_env`、`set_env`
- Time：`current_time`、`date_parse`、`date_diff`
- Random：`random_int`、`random_choice`、`random_string`、`uuid`
- Math：`calc`、`percentage`、`min_max`
- MCP bridge：`mcp`

## 10. Agent 级 execution seam

这几类配置写在 agent 下，而不是顶层。

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

适合解决：

- 参数校验
- timeout
- stream passthrough
- 执行错误规范化
- tool 权限判断（覆写 `evaluate_policy()`）

builtin：

- `safe` — 基础 timeout + 错误规范化，不做权限判断

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

- `retry` — 包装一个 inner executor，按错误类型指数退避重试

- `filesystem_aware` — 内嵌 `FilesystemExecutionPolicy`，替代旧 `execution_policy: filesystem` 用法：

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

  | 字段 | 类型 | 默认值 | 说明 |
  |---|---|---|---|
  | `read_roots` | list[str] | `[]` | 可读路径前缀列表；空列表 = 不限制 |
  | `write_roots` | list[str] | `[]` | 可写路径前缀列表；空列表 = 不限制 |
  | `allow_tools` | list[str] | `[]` | 白名单 tool id；**空列表 = 允许所有 tool** |
  | `deny_tools` | list[str] | `[]` | 黑名单 tool id；deny 优先于 allow |

  !!! note
      `allow_tools` 为空代表**允许所有**工具；如果只想放行部分工具，需显式列出。
      `deny_tools` 的优先级高于 `allow_tools`。

需要多种 policy 组合（例如 filesystem + network allowlist）时，写一个自定义
`ToolExecutorPlugin` 子类并覆写 `evaluate_policy()`，内部组合
`openagents.plugins.builtin.execution_policy` 下的 helper
（`FilesystemExecutionPolicy` / `NetworkAllowlistExecutionPolicy` / `CompositePolicy`）。
参考 `examples/research_analyst/app/executor.py`。

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

适合解决：

- transcript trimming
- artifact trimming
- assembly metadata 注入
- app-defined context packet

builtin：

- `truncating`、`head_tail`、`sliding_window`、`importance_weighted`

## 11. 结构化输出配置（RunRequest 字段）{#runrequest}

`output_type` 和相关 budget 字段是运行时传参，**不在 JSON 配置文件**里声明。
它们通过 `RunRequest` 在每次调用时传入：

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
    output_type=MyOutput,          # 结构化输出 Pydantic model
    budget=RunBudget(
        max_steps=8,
        max_validation_retries=3,  # 结构化输出验证失败最多重试次数
        max_duration_ms=60000,
    ),
)
result = await runtime.run_detailed(request)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `output_type` | `type[BaseModel]` \| null | 结构化输出 Pydantic model；None = 纯文本 |
| `budget.max_steps` | int | 覆盖 agent config 里的 `max_steps` |
| `budget.max_validation_retries` | int | 结构化输出验证失败时的最大重试次数 |
| `budget.max_duration_ms` | int \| null | 整体运行超时 |

## 12. Follow-up / Empty-response 兜底（pattern 方法覆写）{#followup}

旧版本独立的 `followup_resolver` / `response_repair_policy` 两个 seam 已经合并为
`PatternPlugin` 上的两个可选方法覆写。需要本地短路回答 follow-up 或降级空响应时，
写一个 `PatternPlugin` 子类覆写它们：

```python
class MyPattern(ReActPattern):
    async def resolve_followup(self, *, context):
        # 返回 FollowupResolution(status="resolved", output=...) 短路 LLM
        return None  # abstain -> 走 LLM 循环

    async def repair_empty_response(self, *, context, messages, assistant_content, stop_reason, retries):
        # 返回 ResponseRepairDecision(status="repaired", output=...) 或 status="error"
        return None  # abstain -> 让空响应继续传出
```

参考：

- `examples/research_analyst/app/followup_pattern.py`（rule-based follow-up 覆写）
- `examples/production_coding_agent/app/plugins.py`（coding journal follow-up + error-mode repair）

## 13. runtime.config 里的 seam 默认值

builtin `default` runtime 还支持在 `runtime.config` 里声明 seam 默认值。

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

优先级规则：

- agent 自己声明了 seam，就以 agent 级为准
- 没声明时，builtin runtime 才会回退到 runtime-level default

适合场景：

- 多个 agent 共享同一套默认执行策略
- 不想在每个 agent 上重复写一遍相同 seam 配置

## 14. Decorator 注册

当前代码里，这些类别都支持 decorator registry：

- `tool`
- `memory`
- `pattern`
- `runtime`
- `skill`
- `session`
- `event_bus`
- `tool_executor`
- `context_assembler`

注意：

- decorator 注册是进程内生效
- 对应模块必须先被 import，注册名才会存在

## 15. 哪些东西不该放进配置 schema

SDK config 不应该建模所有产品协议。

例如这些通常不该进 schema：

- coding-task DSL
- review contract
- mailbox 语义
- team routing policy
- UI workflow state
- 产品状态树

这些东西更应该放在 app-defined protocol 里。

## 16. 继续阅读

- [开发者指南](developer-guide.md)
- [Seam 与扩展点](seams-and-extension-points.md)
- [插件开发](plugin-development.md)
- [API 参考](api-reference.md)
- [示例说明](examples.md)
