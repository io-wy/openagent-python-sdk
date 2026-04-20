# API 参考

这份文档总结当前最重要的 package exports、runtime surface，以及你真正应该关心的协议对象。

它不是源码替代品。  
它的作用是告诉你：**当前稳定 API 面到底在哪里。**

## 1. package exports

`openagents` 当前导出：

### Core 入口

- `AppConfig`
- `LocalSkillsManager`
- `Runtime`
- `RunContext`
- `SessionSkillSummary`
- `SkillsPlugin`
- `load_config`
- `load_config_dict`
- `run_agent`
- `run_agent_detailed`
- `run_agent_detailed_with_config`
- `run_agent_with_config`
- `run_agent_with_dict`

### 流式 API（0.3.0 新增）

- `RunStreamChunk`
- `RunStreamChunkKind`

### 错误类型（0.3.0 新增）

- `ModelRetryError` — pattern 可抛出以请求模型重试
- `OutputValidationError` — 结构化输出校验在用尽重试次数后抛出

### Skills（0.3.0 新增）

- `LocalSkillsManager`
- `SessionSkillSummary`

### Decorator

- `tool`
- `memory`
- `pattern`
- `runtime`
- `session`
- `event_bus`
- `tool_executor`
- `context_assembler`

### Registry accessors

- `get_tool`
- `get_memory`
- `get_pattern`
- `get_runtime`
- `get_session`
- `get_event_bus`
- `get_tool_executor`
- `get_context_assembler`

### Registry list helpers

- `list_tools`
- `list_memories`
- `list_patterns`
- `list_runtimes`
- `list_sessions`
- `list_event_buses`
- `list_tool_executors`
- `list_context_assemblers`

!!! note "seam 合并（2026-04-18）"
    `execution_policy` / `followup_resolver` / `response_repair_policy`
    三套 decorator / registry 已移除。
    - tool 权限 → `ToolExecutorPlugin.evaluate_policy()`
    - follow-up → `PatternPlugin.resolve_followup()`
    - empty response repair → `PatternPlugin.repair_empty_response()`

## 2. Runtime facade

### `Runtime(config: AppConfig, *, _config_path: Path | None = None)`

对外的 runtime facade。`config.agents` 至少要有一个 agent；顶层 `runtime`/`session`/`events`/`skills` 可以全部省略——pydantic schema 会把它们填成 builtin 默认引用（`default`/`in_memory`/`async`/`local`），插件加载器按统一路径解析。

内部持有：

- app config
- 顶层 runtime / session / events / skills 组件（始终经由 loader 加载）
- 按 session + agent 缓存的插件 bundle

```python
Runtime(AppConfig(agents=[...]))       # 只填 agents，其他 schema 补默认
Runtime.from_dict({"agents": [...]})   # 最小 dict
Runtime.from_config("agent.json")      # 完整 JSON
```

### `Runtime.from_config(config_path: str | Path) -> Runtime`

从磁盘加载 JSON 配置，构造 runtime。

### `Runtime.from_dict(payload: dict[str, Any]) -> Runtime`

直接从 Python dict 构造 runtime。

### `await runtime.run(*, agent_id: str, session_id: str, input_text: str) -> Any`

兼容型入口，返回 `RunResult.final_output`。  
如果 run 失败，会抛异常。

### `await runtime.run_detailed(*, request: RunRequest) -> RunResult`

结构化入口。  
如果你在做更高层的 runtime / framework / product，优先用这个。

### `async runtime.run_stream(*, request: RunRequest) -> AsyncGenerator[RunStreamChunk, None]`

流式入口（0.3.0 新增）。异步生成器，按序产出 `RunStreamChunk` 对象。  
最后一个 chunk 的 `kind` 为 `RUN_FINISHED`，携带完整的 `RunResult`。

详见 [流式 API 深度指南](stream-api.md)。

### `runtime.run_sync(*, agent_id: str, session_id: str, input_text: str) -> Any`

`run()` 的同步封装。

### `await runtime.reload() -> None`

重新加载最初的 config 文件。  
只更新 future run 会用到的 agent 定义，不热切换顶层组件。

### `await runtime.reload_agent(agent_id: str) -> None`

失效一个 agent 在各个 session 下的缓存 bundle。

### `runtime.get_session_count() -> int`

返回当前活跃 session 数量。

### `await runtime.list_agents() -> list[dict[str, Any]]`

返回最小 agent 信息列表，只含 `id` 和 `name`。

### `await runtime.get_agent_info(agent_id: str) -> dict[str, Any] | None`

返回：

- 该 agent 的 selector 配置
- 当前是否已有已加载的 plugin 实例

### `await runtime.close_session(session_id: str) -> None`

关闭一个 session 的插件 bundle。也会级联调用 `release_session(session_id)` 释放 runtime 级共享资源（如 MCP session pool）。

### `await runtime.release_session(session_id: str) -> None`

释放 runtime 自身持有、与 `session_id` 挂钩的共享资源（当前：`DefaultRuntime` 的 MCP 会话池共享连接），不动 session 的 agent plugin bundle。幂等，未使用过的 `session_id` 也安全调用。

### `await runtime.close() -> None`

关闭 runtime 及可关闭的下游资源。对 `DefaultRuntime`，会级联排空所有 MCP session pool。

### `runtime.event_bus`

属性，返回当前 event bus 实例。

### `runtime.session_manager`

属性，返回当前 session manager 实例。

## 3. Sync Helper

### `run_agent(config_path, *, agent_id, session_id="default", input_text) -> Any`

从文件路径加载配置并同步运行。

### `run_agent_with_config(config, *, agent_id, session_id="default", input_text) -> Any`

从预加载 config 同步运行。

### `run_agent_detailed(config_path, *, agent_id, session_id="default", input_text) -> RunResult`

从文件路径做同步 detailed run。

### `run_agent_detailed_with_config(config, *, agent_id, session_id="default", input_text) -> RunResult`

从预加载 config 做同步 detailed run。

### `run_agent_with_dict(payload, *, agent_id, session_id="default", input_text) -> Any`

直接从 Python dict 做同步运行。

### `stream_agent_with_dict(payload, *, request: RunRequest) -> Generator[RunStreamChunk]`

从 Python dict 同步流式运行（0.3.0 新增）。  
在非 async 上下文中使用；不可在已运行的 event loop 内调用。

### `stream_agent_with_config(config_path, *, request: RunRequest) -> Generator[RunStreamChunk]`

从 JSON 配置文件路径同步流式运行（0.3.0 新增）。  
内部调用 `stream_agent_with_dict`。

## 4. 流式 API（Streaming）

### `RunStreamChunkKind`

`str` 枚举，表示 chunk 的来源事件类型：

| 枚举成员 | 值 | 描述 |
| --- | --- | --- |
| `RUN_STARTED` | `run.started` | run 开始 |
| `LLM_DELTA` | `llm.delta` | LLM 增量文本输出 |
| `LLM_FINISHED` | `llm.finished` | 单次 LLM 调用完成 |
| `TOOL_STARTED` | `tool.started` | 工具即将执行 |
| `TOOL_DELTA` | `tool.delta` | 工具流式输出 |
| `TOOL_FINISHED` | `tool.finished` | 工具执行结束（成功或失败） |
| `ARTIFACT` | `artifact` | artifact 已产出 |
| `VALIDATION_RETRY` | `validation.retry` | 结构化输出校验失败，正在重试 |
| `RUN_FINISHED` | `run.finished` | run 完成（终结 chunk） |

### `RunStreamChunk`

| 字段 | 类型 | 描述 |
| --- | --- | --- |
| `kind` | `RunStreamChunkKind` | chunk 类型 |
| `run_id` | `str` | 对应的 run ID |
| `session_id` | `str` | 所属 session |
| `agent_id` | `str` | 所属 agent |
| `sequence` | `int` | 单次 run 内单调递增，可用于断连检测 |
| `timestamp_ms` | `int` | Unix 毫秒时间戳 |
| `payload` | `dict[str, Any]` | 事件特定数据（见下表） |
| `result` | `RunResult \| None` | 仅 `RUN_FINISHED` chunk 携带此字段 |

**各 kind 的 payload 关键字段：**

| Kind | payload 字段 |
| --- | --- |
| `llm.delta` | `text: str` |
| `llm.finished` | `model: str` |
| `tool.started` | `tool_id: str`, `params: dict` |
| `tool.delta` | `tool_id: str`, `text: str` |
| `tool.finished` | `tool_id: str`, `result: Any`（成功）或 `error: str`（失败） |
| `artifact` | `name: str`, `kind: str`, `payload: Any` |
| `validation.retry` | `attempt: int`, `error: str` |

!!! tip
    `sequence` 字段在 run 范围内保证单调递增。消费者可通过检测序号跳跃来判断是否发生了断连。

## 5. 结构化输出

`RunRequest.output_type` 接受一个 Pydantic 模型类，runtime 会用它对最终输出进行校验：

```python
from pydantic import BaseModel
from openagents.interfaces.runtime import RunRequest, RunBudget

class Answer(BaseModel):
    value: str
    confidence: float

request = RunRequest(
    agent_id="assistant",
    session_id="s1",
    input_text="What is 2+2?",
    output_type=Answer,
    budget=RunBudget(max_validation_retries=3),
)
result = await runtime.run_detailed(request=request)
answer: Answer = result.final_output
```

**校验重试机制：**

1. pattern 执行完成后，runtime 对 `final_output` 做 Pydantic 校验。
2. 失败时，错误信息注入 `context.scratch["last_validation_error"]`，并向 event bus 发送 `validation.retry` 事件。
3. runtime 重新进入 `pattern.execute()`，pattern 可读取 scratch 调整输出。
4. 超过 `RunBudget.max_validation_retries`（默认 3）次后，抛出 `OutputValidationError`。

**相关符号：**

- `RunRequest.output_type: type[T] | None` — 目标 Pydantic 模型
- `RunBudget.max_validation_retries: int | None = 3` — 最大校验重试次数
- `OutputValidationError` — 重试耗尽后抛出；携带 `output_type`、`attempts`、`last_validation_error`
- `ModelRetryError` — pattern 可主动抛出，请求 runtime 重试当前步骤

## 6. 成本追踪

### `RunUsage` 成本字段

| 字段 | 类型 | 描述 |
| --- | --- | --- |
| `cost_usd` | `float \| None` | 本次 run 的总美元成本（当 LLM 能提供 token 计数时自动计算） |
| `cost_breakdown` | `dict[str, float]` | 按成本类别分解（如 `input`、`output`、`cached_read`） |

### LLM 定价配置

在 `llm` 配置中添加 `pricing` 字段可覆盖内置价格表（单位：每百万 token 美元）：

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "pricing": {
      "input": 3.00,
      "output": 15.00,
      "cached_read": 0.30,
      "cached_write": 3.75
    }
  }
}
```

### 内置价格表

**Anthropic（每百万 token，美元）：**

| 模型 | input | output | cached_read | cached_write |
| --- | --- | --- | --- | --- |
| `claude-opus-4-6` | 15.00 | 75.00 | 1.50 | 18.75 |
| `claude-sonnet-4-6` | 3.00 | 15.00 | 0.30 | 3.75 |
| `claude-haiku-4-5` | 0.80 | 4.00 | 0.08 | 1.00 |

**OpenAI / openai_compatible（每百万 token，美元）：**

| 模型 | input | output | cached_read |
| --- | --- | --- | --- |
| `gpt-4o` | 2.50 | 10.00 | 1.25 |
| `gpt-4o-mini` | 0.15 | 0.60 | 0.075 |
| `o1` | 15.00 | 60.00 | 7.50 |

!!! note
    `RunBudget.max_cost_usd` 设置后，当累计成本超过限额时，runtime 会以 `stop_reason=budget_exhausted` 终止 run。

## 7. 配置对象

### `AppConfig`

主要字段：

- `version: str`
- `agents: list[AgentDefinition]`
- `runtime: RuntimeRef`
- `session: SessionRef`
- `events: EventBusRef`
- `skills: SkillsRef`
- `logging: LoggingConfig | None`

### `AgentDefinition`

主要字段：

- `id: str`
- `name: str`
- `memory: MemoryRef`
- `pattern: PatternRef`
- `llm: LLMOptions | None`
- `tool_executor: ToolExecutorRef | None`
- `context_assembler: ContextAssemblerRef | None`
- `tools: list[ToolRef]`
- `runtime: RuntimeOptions`

!!! warning
    `execution_policy` / `followup_resolver` / `response_repair_policy` 三个字段在
    2026-04-18 seam 合并中已移除；strict schema 会拒绝这些旧 key。

### `RuntimeOptions`

字段：

- `max_steps: int = 16`
- `step_timeout_ms: int = 30000`
- `session_queue_size: int = 1000`
- `event_queue_size: int = 2000`

### `LLMOptions`

字段：

- `provider: str = "mock"` — `"anthropic"` / `"openai_compatible"` / `"mock"`
- `model: str | None`
- `api_base: str | None` — `openai_compatible` 必须提供
- `api_key_env: str | None`
- `temperature: float | None`
- `max_tokens: int | None`
- `timeout_ms: int = 30000`
- `stream_endpoint: str | None`
- `pricing: LLMPricing | None` — 覆盖内置价格表
- `retry: LLMRetryOptions | None` — 传输层重试策略（默认 `None` → provider 使用内置默认：3 次，指数退避 500ms→2000ms→5000ms，自动重试 429/502/503/504 + Anthropic 529 + `httpx.ConnectError`/`ReadTimeout`）
- `extra_headers: dict[str, str] | None` — 合入每次请求的自定义 header；用户 key 覆盖 provider 默认（如 `{"anthropic-beta": "prompt-caching-2024-07-31"}`）
- `reasoning_model: bool | None` — 仅 `openai_compatible`：显式标记是否为推理模型（o1/o3/o4/gpt-5-thinking…）。`None` 时基于 model 名称正则自动判定。`True` 时使用 `max_completion_tokens` 并丢弃 `temperature`
- `openai_api_style: Literal["chat_completions", "responses"] | None` — 仅 `openai_compatible`：选择 OpenAI API 风格。`None` 时根据 `api_base` 自动判定（以 `/responses` 结尾 → `"responses"`，否则 `"chat_completions"`）。Responses API (v2) 的 payload 形状完全不同：`messages` → `input` + `instructions`；`max_tokens` → `max_output_tokens`；`response_format` → `text.format`（扁平化，不再嵌套 `json_schema`）。`response.output[]` 的 `type` 为 `message`/`reasoning`/`function_call`。目前流式仅 Chat Completions 原生支持；Responses API 的 `complete_stream()` 会降级为一次性非流式调用
- `seed` / `top_p` / `parallel_tool_calls` — 仅 `openai_compatible`（通过 `extra="allow"` 透传）；每次请求自动写入 payload

### `LLMRetryOptions`

- `max_attempts: int = 3`（设为 `1` 可关闭重试）
- `initial_backoff_ms: int = 500`
- `max_backoff_ms: int = 5000`
- `backoff_multiplier: float = 2.0`
- `retry_on_connection_errors: bool = True`
- `total_budget_ms: int | None = None` — 总墙钟预算上限；预算耗尽前不再发起新 attempt

### `LLMChunk`

流式响应块，新增字段：

- `error_type: Literal["rate_limit", "connection", "response", "unknown"] | None = None` — 非错误块恒为 `None`；错误块按 `LLMError` 子类归一化分类

### Provider 行为差异摘要

**Anthropic** — `content` 保留 `thinking` / `redacted_thinking` 块（不计入 `output_text`）；`system` 接受 `str` 或 `list[dict]`（后者保留块级 `cache_control`）；`tools` 与消息内容块的 `cache_control` 原样透传；`529` 归类为 `rate_limit` 并纳入重试。

**OpenAI-compatible** — 推理模型（`o\d+(-.*)?` / `gpt-5-thinking*` 或 `reasoning_model=True`）使用 `max_completion_tokens` 并丢弃 `temperature`；`usage.completion_tokens_details.reasoning_tokens` 写入 `LLMUsage.metadata["reasoning_tokens"]`（不重复计入 `output_tokens`）；`finish_reason="tool_calls"` 归一化为 `stop_reason="tool_use"`。

## 8. Runtime protocol

### `RunBudget`

单次 run 的可选限制：

| 字段 | 类型 | 默认值 | 描述 |
| --- | --- | --- | --- |
| `max_steps` | `int \| None` | `None` | 最大步数 |
| `max_duration_ms` | `int \| None` | `None` | 最大执行时长（毫秒） |
| `max_tool_calls` | `int \| None` | `None` | 最大工具调用次数 |
| `max_validation_retries` | `int \| None` | `3` | 结构化输出校验最大重试次数 |
| `max_cost_usd` | `float \| None` | `None` | 最大成本上限（美元） |
| `max_resume_attempts` | `int \| None` | `3` | durable run 的最大自动恢复次数（0.4.x 新增） |

### `RunArtifact`

run 产物：

- `name: str`
- `kind: str = "generic"`
- `payload: Any`
- `metadata: dict[str, Any]`

### `RunUsage`

run 的 usage 聚合：

- `llm_calls: int`
- `tool_calls: int`
- `input_tokens: int`
- `output_tokens: int`
- `total_tokens: int`
- `input_tokens_cached: int`
- `input_tokens_cache_creation: int`
- `cost_usd: float | None`
- `cost_breakdown: dict[str, float]`

### `RunRequest`

结构化输入：

- `agent_id: str`
- `session_id: str`
- `input_text: str`
- `run_id: str` — 默认 UUID4 自动生成
- `parent_run_id: str | None`
- `metadata: dict[str, Any]`
- `context_hints: dict[str, Any]`
- `budget: RunBudget | None`
- `deps: Any`
- `output_type: type[BaseModel] | None` — 结构化输出目标类型（0.3.0 新增）
- `durable: bool = False` — 开启 durable execution：每个 step 边界自动 checkpoint，retryable 错误自动从最近 checkpoint 恢复（0.4.x 新增）
- `resume_from_checkpoint: str | None = None` — 显式从给定 checkpoint 恢复一个新 run；`DefaultRuntime` 会跳过 `context_assembler.assemble()` 和 `memory.inject()`，直接从 checkpoint 的 transcript / artifacts / usage 重建状态（0.4.x 新增）

### Durable execution

Durable execution 是 runtime 层面的容错机制，不是新 seam。开启方式：

```python
from openagents.interfaces.runtime import RunBudget, RunRequest

request = RunRequest(
    agent_id="coding-agent",
    session_id="my-session",
    input_text="refactor this module...",
    durable=True,  # 自动 checkpoint + 自动 resume
    budget=RunBudget(max_resume_attempts=3),
)
result = await runtime.run_detailed(request=request)
```

**checkpoint 粒度**：每次成功的 `llm.succeeded` / `tool.succeeded` 事件后写一个 checkpoint，`checkpoint_id = f"{run_id}:step:{n}"`。批量工具调用（`call_tool_batch`）整体算一个 step。

**retryable 错误分类**：`LLMRateLimitError`、`LLMConnectionError`、`ToolRateLimitError`、`ToolUnavailableError` 会触发自动 resume；其余错误（`PermanentToolError`、`ConfigError`、`BudgetExhausted`、`OutputValidationError`）直接终止 run。

**显式恢复**：若进程崩溃，可以用持久化的 session backend（`jsonl_file` / `sqlite`）跨进程恢复：

```python
# 在新进程里
request = RunRequest(
    agent_id="coding-agent",
    session_id="my-session",
    input_text="refactor this module...",
    resume_from_checkpoint="abc123:step:7",  # 从第 7 步继续
)
```

**事件**：durable execution 会发出五个新事件：
- `run.checkpoint_saved` — 每次成功 checkpoint 后
- `run.checkpoint_failed` — create_checkpoint 抛错（run 继续，不失败）
- `run.resume_attempted` — 捕获 retryable 错误后准备 resume
- `run.resume_succeeded` — resume 状态重建完成
- `run.resume_exhausted` — 达到 `max_resume_attempts` 上限
- `run.durable_idempotency_warning` — 工具声明 `durable_idempotent=False` 时一次性提示（每 (run, tool) 只发一次）

**ToolPlugin.durable_idempotent**（类属性，默认 `True`）：写文件、发 HTTP、shell 子进程等有副作用的工具应声明为 `False`，在 durable run 中被调用时 runtime 会发出一次性警告。内建工具中 `WriteFileTool`、`DeleteFileTool`、`HttpRequestTool`、`ShellExecTool`、`ExecuteCommandTool`、`SetEnvTool` 已默认标为 `False`。

### `RunResult[T]`

结构化输出（泛型，0.3.0 起）：

- `run_id: str`
- `final_output: T | None`
- `stop_reason: StopReason`
- `usage: RunUsage`
- `artifacts: list[RunArtifact]`
- `error: str | None`
- `exception: OpenAgentsError | None`
- `metadata: dict[str, Any]`

### `StopReason`

取值：

- `completed`
- `failed`
- `cancelled`
- `timeout`
- `max_steps`
- `budget_exhausted`

## 9. RunContext

`RunContext` 是 pattern 和 tool 真正消费的运行态对象。

主要字段：

- `agent_id`
- `session_id`
- `run_id`
- `input_text`
- `deps`
- `state`
- `tools`
- `llm_client`
- `llm_options`
- `event_bus`
- `memory_view`
- `tool_results`
- `scratch`
- `system_prompt_fragments`
- `transcript`
- `session_artifacts`
- `assembly_metadata`
- `run_request`
- `tool_executor`
- `usage`
- `artifacts`

> `execution_policy` / `followup_resolver` / `response_repair_policy` 属性在
> 2026-04-18 seam 合并中移除 —— 权限判断由 `tool_executor.evaluate_policy()` 负责，
> follow-up / empty-response 走 `PatternPlugin` 上的方法覆写。

这是 app-defined middle protocol 最重要的 carrier。

## 10. Tool execution protocol

### `ToolExecutionSpec`

执行元信息：

- `concurrency_safe`
- `interrupt_behavior`
- `side_effects`
- `approval_mode`
- `default_timeout_ms`
- `reads_files`
- `writes_files`

### `PolicyDecision`

policy 输出：

- `allowed`
- `reason`
- `metadata`

### `ToolExecutionRequest`

结构化 tool 执行输入：

- `tool_id`
- `tool`
- `params`
- `context`
- `execution_spec`
- `metadata`

### `ToolExecutionResult`

结构化 tool 执行输出：

- `tool_id`
- `success`
- `data`
- `error`
- `exception`
- `metadata`

## 11. Context assembly protocol

### `ContextAssemblyResult`

结构化 pre-run context：

- `transcript`
- `session_artifacts`
- `metadata`

## 12. Follow-up / response repair protocol

### `FollowupResolution`

字段：

- `status`
- `output`
- `reason`
- `metadata`

当前推荐状态：

- `resolved`
- `abstain`
- `error`

### `ResponseRepairDecision`

字段：

- `status`
- `output`
- `reason`
- `metadata`

当前推荐状态：

- `repaired`
- `abstain`
- `error`

## 13. Session protocol

### `SessionArtifact`

字段：

- `name`
- `kind`
- `payload`
- `metadata`

### `SessionCheckpoint`

字段：

- `checkpoint_id`
- `state`
- `transcript_length`
- `artifact_count`
- `created_at`

## 14. Plugin contract

### `ToolPlugin`

主要方法：

- `async invoke(params, context) -> Any`
- `async invoke_stream(params, context)`
- `execution_spec() -> ToolExecutionSpec`
- `schema() -> dict`
- `describe() -> dict`
- `validate_params(params) -> tuple[bool, str | None]`
- `get_dependencies() -> list[str]`
- `async fallback(error, params, context) -> Any`

**扩展方法（2026-04-19）**—— 全部带默认实现，单工具按需覆写：

- `async invoke_batch(items: list[BatchItem], context) -> list[BatchResult]` —— 默认顺序循环 `invoke`；可覆写以下沉（MCP 单会话批量、多文件批读等）。结果顺序与 `item_id` 与输入严格一致。
- `async invoke_background(params, context) -> JobHandle` —— 提交长任务，立即返回句柄；默认 `NotImplementedError`。
- `async poll_job(handle, context) -> JobStatus` —— 查询后台任务状态；默认 `NotImplementedError`。
- `async cancel_job(handle, context) -> bool` —— 取消后台任务；默认 `NotImplementedError`。
- `requires_approval(params, context) -> bool` —— 是否需要人工审批；默认读 `execution_spec().approval_mode == "always"`。
- `async before_invoke(params, context)` / `async after_invoke(params, context, result, exception=None)` —— 每次调用前/后钩子（区别于每 run 一次的 `preflight`）。`after_invoke` 在成功与失败分支都会运行。

伴随的新 pydantic 模型：`BatchItem` / `BatchResult` / `JobHandle` / `JobStatus`（见 `openagents.interfaces.tool`）。

### `ToolExecutorPlugin`

主要方法：

- `async evaluate_policy(request) -> PolicyDecision` — override to restrict tool execution (default：allow all)
- `async execute(request) -> ToolExecutionResult`
- `async execute_stream(request)`
- `async execute_batch(requests) -> list[ToolExecutionResult]` —— 默认顺序循环 `execute`；builtin `ConcurrentBatchExecutor` 按 `execution_spec.concurrency_safe` 分组并发。

`ToolExecutionRequest` 新增 `cancel_event: asyncio.Event | None` 字段；`DefaultRuntime` 在每个 run 前种入 `ctx.scratch['__cancel_event__']`，`_BoundTool.invoke` 把它串入 request，`SafeToolExecutor.execute` 会与 `cancel_event` / `timeout` 三方竞速。`ToolExecutionSpec.interrupt_behavior == "block"` 时忽略 cancel 并等待 tool 自然完成。

**新错误子类（`openagents.errors.exceptions`）**：
`ToolValidationError` / `ToolAuthError`（不重试）、`ToolRateLimitError` / `ToolUnavailableError`（`RetryToolExecutor` 默认重试）、`ToolCancelledError`（cancel_event 触发时由 `SafeToolExecutor` 抛出，不重试）。

**Pattern 便捷方法**：`PatternPlugin.call_tool_batch(requests: list[tuple[str, dict]]) -> list[Any]` —— 按 `tool_id` 分组调用 `invoke_batch`，保持输入顺序；发 `tool.batch.started` / `tool.batch.completed` 事件。

### `MemoryPlugin`

主要方法：

- `async inject(context) -> None`
- `async writeback(context) -> None`
- `async retrieve(query, context) -> list[dict[str, Any]]`
- `async close() -> None`

### `PatternPlugin`

主要方法：

- `async setup(...) -> None`
- `async execute() -> Any`
- `async react() -> dict[str, Any]`
- `async emit(event_name, **payload) -> None`
- `async call_tool(tool_id, params=None) -> Any`
- `async call_llm(...) -> str`
- `async compress_context() -> None`
- `add_artifact(...) -> None`
- `async resolve_followup(*, context) -> FollowupResolution | None` — override to answer follow-ups locally (default：abstain)
- `async repair_empty_response(*, context, messages, assistant_content, stop_reason, retries) -> ResponseRepairDecision | None` — override to recover from bad LLM responses (default：abstain)

### `SkillsPlugin`

主要方法：

- `prepare_session(session_id, session_manager) -> dict[str, SessionSkillSummary]`
- `load_references(session_id, skill_name, session_manager) -> list[dict[str, str]]`
- `run_skill(session_id, skill_name, payload, session_manager) -> dict[str, Any]`

### `ContextAssemblerPlugin`

主要方法：

- `async assemble(request, session_state, session_manager) -> ContextAssemblyResult`
- `async finalize(request, session_state, session_manager, result) -> result`

### `RuntimePlugin`

主要方法：

- `async initialize() -> None`
- `async validate() -> None`
- `async health_check() -> bool`
- `async run(...) -> RunResult`
- `async pause() -> None`
- `async resume() -> None`
- `async close() -> None`

### `SessionManagerPlugin`

主要方法：

- `async with session(session_id)`
- `async get_state(session_id) -> dict[str, Any]`
- `async set_state(session_id, state) -> None`
- `async delete_session(session_id) -> None`
- `async list_sessions() -> list[str]`
- `async append_message(session_id, message) -> None`
- `async load_messages(session_id) -> list[dict[str, Any]]`
- `async save_artifact(session_id, artifact) -> None`
- `async list_artifacts(session_id) -> list[SessionArtifact]`
- `async create_checkpoint(session_id, checkpoint_id) -> SessionCheckpoint`
- `async load_checkpoint(session_id, checkpoint_id) -> SessionCheckpoint | None`
- `async close() -> None`

### `EventBusPlugin`

主要方法：

- `subscribe(event_name, handler) -> None`
- `async emit(event_name, **payload) -> RuntimeEvent`
- `async get_history(event_name=None, limit=None) -> list[RuntimeEvent]`
- `async clear_history() -> None`
- `async close() -> None`

## 15. Registry helper

`get_*` helper 返回的是 decorator registry 里的类。  
`list_*` helper 返回的是 decorator registry 里的名称。

它们不是 builtin registry 的完整替代品。

## 16. Plugin authoring helpers

供自定义 combinator 与 pattern 作者使用的公开 helper。

| Symbol | Module | Purpose |
| --- | --- | --- |
| `load_plugin(kind, ref, *, required_methods=())` | `openagents.plugins.loader` | 公开的子插件加载入口，combinator (`memory.chain`, `tool_executor.retry`, `execution_policy.composite`, `events.file_logging`) 内部都走它 |
| `unwrap_tool_result(result) -> tuple[data, metadata \| None]` | `openagents.interfaces.pattern` | 把 `_BoundTool.invoke()` 返回的 `ToolExecutionResult` 解包成 `(data, executor_metadata)`；对 raw `ToolPlugin.invoke()` 返回值则直接 passthrough，metadata 为 `None` |
| `TypedConfigPluginMixin` | `openagents.interfaces.typed_config` | Mixin，提供基于嵌套 `Config(BaseModel)` 的 `self.cfg` 校验；未知键发 warning 而非报错 |

`openagents.plugins.loader._load_plugin` 仍保留为 deprecated 别名，
会发 `DeprecationWarning`。

## 17. 错误与诊断 helper（Spec B WP1 / WP2）

| Symbol | Module | Purpose |
| --- | --- | --- |
| `OpenAgentsError(message, *, hint=None, docs_url=None, ...)` | `openagents.errors.exceptions` | 基类异常；新增可选 `hint` / `docs_url`。`str(exc)` 在被设置时会多输出 `hint: ...` / `docs: ...` 行，首行保持原 message 不变 |
| `near_match(needle, candidates, *, cutoff=0.6)` | `openagents.errors.suggestions` | 轻量 "did you mean?" 包装，基于 `difflib.get_close_matches`；返回最近匹配或 `None` |
| `EVENT_SCHEMAS` | `openagents.interfaces.event_taxonomy` | 已声明事件名 → `EventSchema(name, required_payload, optional_payload, description)` 的字典。`AsyncEventBus.emit` 在缺少必需 key 时 `logger.warning`，从不 raise |
| `EventSchema` | `openagents.interfaces.event_taxonomy` | 单个事件 schema 的 frozen dataclass |
| `gen_event_doc.render_doc()` / `write_doc(target)` / `main(argv)` | `openagents.tools.gen_event_doc` | 从 `EVENT_SCHEMAS` 重新生成 `docs/event-taxonomy.md` 的 helper |

## 18. Optional builtin index（Spec C）

These builtins ship under `openagents/plugins/builtin/` but require an
optional extra to construct. Module import always succeeds; instantiation
without the extra raises `PluginLoadError` with an install hint.

| Class | Seam / type key | Module | Extra |
| --- | --- | --- | --- |
| `Mem0Memory` | `memory` / `mem0` | `openagents.plugins.builtin.memory.mem0_memory` | `mem0` |
| `McpTool` | `tool` / `mcp` | `openagents.plugins.builtin.tool.mcp_tool` | `mcp` |
| `SqliteSessionManager` | `session` / `sqlite` | `openagents.plugins.builtin.session.sqlite_backed` | `sqlite` |
| `OtelEventBusBridge` | `events` / `otel_bridge` | `openagents.plugins.builtin.events.otel_bridge` | `otel` |

Install with `uv sync --extra <name>` (or `uv sync --extra all`). Each
module is also added to `[tool.coverage.report] omit` in `pyproject.toml`
so the 92% coverage floor stays intact when the extra is not installed.

### 18.1 `McpTool` 生命周期配置（0.3.x 新增）

`McpTool.Config` 除了 `server` 与 `tools` 外，还支持：

- `connection_mode: "per_call" | "pooled"`（默认 `per_call`）。`per_call` 保持 anyio cancel-scope 不跨调用泄漏；`pooled` 复用长连接、N 次调用只 fork 一次子进程。
- `probe_on_preflight: bool`（默认 `false`）。开启后 `preflight()` 会在 agent 循环启动前多开一次临时连接并 `list_tools()`。
- `dedup_inflight: bool`（默认 `true`）。合并 `per_call` 模式下同 `(tool, arguments)` 的并发调用，减少重复子进程启动。

`ToolPlugin` 新增可选钩子 `async def preflight(self, context) -> None`，默认实现为 no-op；`DefaultRuntime` 在每个 session 第一轮 agent turn 之前会依次调用。`McpTool` 重写此钩子，验证 `mcp` SDK、`shutil.which` 命令、URL 合法性，失败抛 `PermanentToolError` 由运行时翻译成 `StopReason.FAILED` 的 `RunResult`（不会进入 pattern loop）。

运行时会发射 `tool.preflight`、`tool.mcp.preflight`、`tool.mcp.connect`、`tool.mcp.call`、`tool.mcp.close` 结构化事件；payload 仅携带 id / 状态 / 耗时，**不记录参数与返回值**。

## 19. 继续阅读

- [开发者指南](developer-guide.md)
- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [示例说明](examples.md)
- [流式 API 深度指南](stream-api.md)
