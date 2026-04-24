# 错误参考

本手册列出所有 `OpenAgentsError` 子类、code、可重试性、典型 hint 及推荐处理策略。

所有错误都带 `.to_dict()` 方法用于序列化；失败的 `RunResult.error_details` 会 mirror 这个结构。

事件层面：所有 `*.failed` 事件 payload 都含 `error_details: dict`（同 `to_dict()` 形状）；
`run.resume_attempted` / `run.resume_exhausted` 含 `error_code: str`。

## 总览表

| code | 类 | retryable | 典型 stop_reason |
|---|---|---|---|
| `openagents.error` | `OpenAgentsError` | ❌ | `failed` |
| `config.error` | `ConfigError` | ❌ | `failed` |
| `config.load` | `ConfigLoadError` | ❌ | `failed` |
| `config.validation` | `ConfigValidationError` | ❌ | `failed` |
| `plugin.error` | `PluginError` | ❌ | `failed` |
| `plugin.load` | `PluginLoadError` | ❌ | `failed` |
| `plugin.capability` | `PluginCapabilityError` | ❌ | `failed` |
| `plugin.config` | `PluginConfigError` | ❌ | `failed` |
| `execution.error` | `ExecutionError` | ❌ | `failed` |
| `execution.max_steps` | `MaxStepsExceeded` | ❌ | `max_steps` |
| `execution.budget_exhausted` | `BudgetExhausted` | ❌ | `budget_exhausted` |
| `execution.output_validation` | `OutputValidationError` | ❌ | `failed` |
| `session.error` | `SessionError` | ❌ | `failed` |
| `pattern.error` | `PatternError` | ❌ | `failed` |
| `tool.error` | `ToolError` | ❌ | `failed` |
| `tool.retryable` | `RetryableToolError` | ✅ | `failed` |
| `tool.permanent` | `PermanentToolError` | ❌ | `failed` |
| `tool.timeout` | `ToolTimeoutError` | ✅ | `failed` |
| `tool.not_found` | `ToolNotFoundError` | ❌ | `failed` |
| `tool.validation` | `ToolValidationError` | ❌ | `failed` |
| `tool.auth` | `ToolAuthError` | ❌ | `failed` |
| `tool.rate_limit` | `ToolRateLimitError` | ✅ | `failed` |
| `tool.unavailable` | `ToolUnavailableError` | ✅ | `failed` |
| `tool.cancelled` | `ToolCancelledError` | ❌ | `failed` |
| `llm.error` | `LLMError` | ❌ | `failed` |
| `llm.connection` | `LLMConnectionError` | ✅ | `failed` |
| `llm.rate_limit` | `LLMRateLimitError` | ✅ | `failed` |
| `llm.response` | `LLMResponseError` | ❌ | `failed` |
| `llm.model_retry` | `ModelRetryError` | ❌ (由 runtime finalize loop 消费) | `failed` |
| `user.error` | `UserError` | ❌ | `failed` |
| `user.invalid_input` | `InvalidInputError` | ❌ | `failed` |
| `user.agent_not_found` | `AgentNotFoundError` | ❌ | `failed` |

## openagents.*

### `openagents.error` — `OpenAgentsError`（基类）

所有 SDK 错误的根类。

- **retryable**: false
- **通用字段**：`agent_id`、`session_id`、`run_id`、`tool_id`、`step_number`、`hint`、`docs_url`
- **序列化**：`.to_dict()` 返回 `{code, message, hint, docs_url, retryable, context}`
- **处理**：通常应捕获更具体的子类；仅在无法预期具体类型时捕获基类

## config.*

### `config.load` — `ConfigLoadError`

- **触发**：`load_config()` 读不到文件 / JSON 语法错误 / env var 未设置
- **retryable**: false
- **典型 hint**: "Run from the repo root, or pass an absolute path to the config file"
- **处理**：修复文件路径、补充环境变量、修复 JSON 语法

### `config.validation` — `ConfigValidationError`

- **触发**：config 不符合 `AppConfig` pydantic schema
- **retryable**: false
- **处理**：参照 [配置参考](configuration.md) 校正字段

### `config.error` — `ConfigError`（基类）

- **触发**：其他配置问题；一般由更具体的子类抛出
- **retryable**: false

## plugin.*

### `plugin.load` — `PluginLoadError`

- **触发**：`plugins/loader.py` 无法解析 `type` / `impl`，或导入失败
- **retryable**: false
- **典型 hint**: 给出 "Did you mean?" near_match 提示
- **处理**：修 `type` / `impl` 字段，确认模块已安装

### `plugin.capability` — `PluginCapabilityError`

- **触发**：插件缺少声明的 capability（required method 检查失败）
- **retryable**: false
- **处理**：实现插件接口所要求的全部方法

### `plugin.config` — `PluginConfigError`

- **触发**：插件的 `config` 子对象不合法（`TypedConfigPluginMixin` 校验失败）
- **retryable**: false
- **处理**：参照对应 plugin 的 `Config` schema 修正字段

### `plugin.error` — `PluginError`（基类）

- **触发**：其他插件相关问题；一般由更具体的子类抛出
- **retryable**: false

## execution.*

### `execution.max_steps` — `MaxStepsExceeded`

- **触发**：pattern 执行超过 `max_steps` / 工具调用预算 / 会话步数
- **retryable**: false
- **stop_reason**: `max_steps`
- **处理**：增大 `agent.runtime.max_steps` 或优化 pattern 使之更快收敛

### `execution.budget_exhausted` — `BudgetExhausted`

- **触发**：`RunBudget` 的某个维度（tool_calls / duration / cost）超限
- **retryable**: false
- **stop_reason**: `budget_exhausted`
- **额外字段**：`kind` (tool_calls|duration|steps|cost)、`current`、`limit`
- **处理**：放宽 `RunBudget` 对应字段；若 `kind="cost"` 则检查 `max_cost_usd`

### `execution.output_validation` — `OutputValidationError`

- **触发**：`finalize` 阶段连续 `max_validation_retries` 次都无法通过 pydantic `output_type.model_validate`
- **retryable**: false（整体不可重试；单次由 `ModelRetryError` → runtime finalize loop 消化）
- **额外字段**：`attempts`、`last_validation_error`、`output_type`
- **处理**：调整 pattern 输出或放宽 `output_type` schema；可通过 `RunBudget.max_validation_retries` 增加重试次数

### `session.error` — `SessionError`

- **触发**：session 管理失败（如 session lock 获取超时、持久化错误）
- **retryable**: false
- **处理**：检查 session 存储配置

### `pattern.error` — `PatternError`

- **触发**：pattern 执行中未被 typed 的异常（其它异常会被 runtime wrap 成 `PatternError`）
- **retryable**: false
- **处理**：检查 `cause` 字段获取原始异常；修复 pattern 实现

### `execution.error` — `ExecutionError`（基类）

- **触发**：其他运行时执行失败；一般由更具体的子类抛出
- **retryable**: false

## tool.*

### `tool.timeout` — `ToolTimeoutError`

- **触发**：`tool.invoke` 超过 `execution_spec.default_timeout_ms`
- **retryable**: ✅ true
- **处理**：`RetryToolExecutor` 自动重试；若持续超时则增大超时或加速工具

### `tool.rate_limit` — `ToolRateLimitError`

- **触发**：工具返回 rate-limit 提示
- **retryable**: ✅ true
- **额外字段**：`retry_after_ms: int | None` — 若非空，`RetryToolExecutor._delay_for` 将把它作为 sleep 下限
- **处理**：`RetryToolExecutor` 自动退避重试；若 `retry_after_ms` 持续过大，考虑升级 API 配额

### `tool.unavailable` — `ToolUnavailableError`

- **触发**：工具暂时不可达（DNS/5xx/临时故障）
- **retryable**: ✅ true
- **处理**：`RetryToolExecutor` 自动重试；排查网络或服务可用性

### `tool.retryable` — `RetryableToolError`（基类）

- **触发**：可重试的工具错误；具体子类包括 `ToolTimeoutError`、`ToolRateLimitError`、`ToolUnavailableError`
- **retryable**: ✅ true

### `tool.permanent` — `PermanentToolError`（基类）

- **触发**：不可重试的工具错误；具体子类包括 `ToolNotFoundError`、`ToolValidationError`、`ToolAuthError`、`ToolCancelledError`
- **retryable**: false

### `tool.not_found` — `ToolNotFoundError`

- **触发**：pattern 请求不存在的 tool id
- **retryable**: false
- **处理**：检查 tool 注册；确认 tool 的 `id` 与 pattern 请求的名称一致

### `tool.validation` — `ToolValidationError`

- **触发**：`tool.validate_params` 返回 false，或工具自己判定参数错误
- **retryable**: false
- **处理**：修正调用参数；检查工具 schema 与 LLM 输出的匹配情况

### `tool.auth` — `ToolAuthError`

- **触发**：工具端点 401/403
- **retryable**: false（需要换 token）
- **处理**：更新 API token / 凭据；检查 IAM 权限

### `tool.cancelled` — `ToolCancelledError`

- **触发**：`cancel_event` 被设置，工具执行被取消
- **retryable**: false（取消是终态：重试只会再次命中同一 cancel 信号）
- **处理**：用户主动取消属正常流程；检查是否有意外的 cancel_event 触发

### `tool.error` — `ToolError`（基类）

- **触发**：其他工具错误；一般由更具体的子类抛出
- **retryable**: false
- **额外字段**：`tool_name: str`

## llm.*

### `llm.connection` — `LLMConnectionError`

- **触发**：连接失败 / 超时 / 5xx
- **retryable**: ✅ true
- HTTP 层已内置重试；到达 runtime 层意味着重试预算耗尽
- **处理**：检查网络连通性；检查 API endpoint 配置；确认 provider 状态页

### `llm.rate_limit` — `LLMRateLimitError`

- **触发**：429 / 529 / provider overload
- **retryable**: ✅ true
- **额外字段**：`retry_after_ms: int | None` — 从 `Retry-After` 头（delta-seconds 或 HTTP-date）解析；LiteLLM provider 会从 `exc.retry_after` best-effort 读取
- **处理**：检查 provider 配额；升级 tier；合理控制并发

### `llm.response` — `LLMResponseError`

- **触发**：非可重试 4xx（401/400 等）或非 JSON 响应
- **retryable**: false
- **处理**：检查 API key；检查请求参数（model 名、消息格式）

### `llm.model_retry` — `ModelRetryError`

- **触发**：`pattern.finalize` 校验失败，由 finalize loop 向 LLM 注入 correction 并重试
- **retryable**: false（由 runtime finalize loop 消费；工具执行器不应捕获）
- **额外字段**：`validation_error` 透传原始 pydantic `ValidationError`
- **处理**：一般无需手动处理；若 `OutputValidationError` 被抛出则调整 `output_type` schema

### `llm.error` — `LLMError`（基类）

- **触发**：其他 LLM/provider 失败；一般由更具体的子类抛出
- **retryable**: false

## user.*

### `user.invalid_input` — `InvalidInputError`

- **触发**：调用方传入的 input 不合法（如空 `input_text`、非法字段值）
- **retryable**: false
- **处理**：修正 `RunRequest` 参数

### `user.agent_not_found` — `AgentNotFoundError`

- **触发**：`Runtime.run(agent_id=...)` 找不到对应 agent
- **retryable**: false
- **典型 hint**: near_match "Did you mean?" 提示
- **处理**：检查 config 中 agent 的 `id` 字段；确认拼写

### `user.error` — `UserError`（基类）

- **触发**：调用方侧的错误；一般由更具体的子类抛出
- **retryable**: false

## 自定义错误类

```python
from openagents.errors import RetryableToolError

class MyToolQuotaError(RetryableToolError):
    code = "tool.my_quota"
    # retryable 继承 True
```

声明后：

- `RetryToolExecutor` 自动把它作为可重试
- `DefaultRuntime` durable resume 自动捕获
- `ErrorDetails.from_exception` 正确序列化 `code = "tool.my_quota"`

**约束**：`code` 必须是 dotted 格式（如 `tool.my_quota`，匹配 `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`），且全局唯一（不与内置 code 冲突）。
