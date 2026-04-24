# 开发者指南

这份文档讲的是：**怎样用 OpenAgents 做对的架构分层。**

如果只记住一句话，请记这句：

**不要把产品语义轻易塞进 kernel。**

OpenAgents 最适合的分工是：

- kernel protocol 尽量稳定
- SDK seam 少而硬
- app 自己发明 middle protocol

## 1. 项目边界

OpenAgents 是一个 **single-agent runtime kernel**。

这意味着：

- 一次 `RunRequest` 只对应一个 `agent_id`
- 一次 `Runtime.run()` 只执行一个 agent run
- session、memory、pattern、tool bundle 都围绕这个单 agent 模型组织

这也意味着，它当前 **不负责**：

- multi-agent team orchestration
- subagent delegation
- mailbox / background jobs
- approval UX
- product workflow state machine

这些能力应该放在 SDK 之上。

## 2. 三层结构

### Kernel Protocol

这是运行时最底层、最稳定的一组对象：

- `RunRequest`
- `RunResult`
- `RunUsage`
- `RunArtifact`
- `RunContext`
- `ToolExecutionRequest`
- `ToolExecutionResult`
- `ContextAssemblyResult`
- `SessionArtifact`
- `SessionCheckpoint`

这些对象应该尽量保持小、明确、无产品偏见。

### SDK Seam

这是 runtime 明确开放出来的控制缝，共 **8 个**（2026-04-18 seam 合并：11 → 8）：

- **capability seam**
  - `memory`
  - `pattern`
  - `tool`
- **execution seam**
  - `tool_executor`（tool 执行 + 内置权限判断 `evaluate_policy()`）
  - `context_assembler`
- **app infra seam**
  - `runtime`
  - `session`
  - `events`
  - `skills`

!!! note "Seam 合并说明（0.3.0）"
    `execution_policy`、`followup_resolver`、`response_repair_policy` 三个旧 seam 已于 2026-04-18 移除，合并为 pattern/executor 上的可覆写方法：

    - `ToolExecutorPlugin.evaluate_policy()` — tool 权限判断（默认 allow-all）
    - `PatternPlugin.resolve_followup()` — 本地回答 follow-up（默认 abstain / None）
    - `PatternPlugin.repair_empty_response()` — 降级坏/空响应（默认 abstain / None）

    迁移详情与代码示例见 [`seams-and-extension-points.md`](seams-and-extension-points.md) §2。

#### `PatternPlugin` 可覆写方法

`PatternPlugin` 子类可以通过覆写以下方法来扩展 pattern 行为，无需新建独立 seam：

```python
async def resolve_followup(
    self, *, context: RunContext[Any]
) -> FollowupResolution | None:
    """本地回答 follow-up 请求。返回 None 表示 abstain（交给 LLM）。"""
    return None

async def repair_empty_response(
    self,
    *,
    context: RunContext[Any],
    messages: list[dict[str, Any]],
    assistant_content: list[dict[str, Any]],
    stop_reason: str | None,
    retries: int,
) -> ResponseRepairDecision | None:
    """处理 provider 的坏/空响应。返回 None 表示 abstain（让空响应继续传出）。"""
    return None
```

两个方法均由 builtin `ReActPattern.execute()` 自动调用；自定义 pattern 子类也应在合适时机调用它们。

#### `ToolExecutorPlugin.evaluate_policy()`

`ToolExecutorPlugin` 子类可以覆写此方法来实现工具执行权限判断：

```python
async def evaluate_policy(
    self, request: ToolExecutionRequest
) -> PolicyDecision:
    """Override to restrict tool execution. Default: allow all."""
    return PolicyDecision(allowed=True)
```

基类 `execute()` 和 `execute_stream()` 在调用工具之前都会先调用 `evaluate_policy()`，若 `allowed=False` 则短路返回错误。

### App-Defined Middle Protocol

这才是高设计密度 agent 最应该发力的地方。

例如：

- coding-task envelope
- review contract
- retrieval plan
- permission state
- artifact taxonomy
- action summary

OpenAgents 不会把这些全部做成内建 seam，而是给你 carrier 去承载。

## 3. 一次 run 的主流程

builtin runtime 的执行顺序可以概括为：

1. `Runtime.from_config()` 或 `Runtime.from_dict()` 装顶层组件
2. `Runtime.run_detailed()` 找到目标 agent
3. `Runtime` 调顶层 `skills.prepare_session()` 预热 session 里的 skill descriptions
4. `Runtime` 创建或复用 `(session_id, agent_id)` 插件 bundle
5. `DefaultRuntime.run()` 发事件并获取 session lock
6. `context_assembler.assemble()` 组装 transcript / artifacts / metadata
7. runtime budget 注入 pattern
8. 用 `tool_executor`（其 `evaluate_policy()` 方法内置权限判断）重新绑定 tools
9. `pattern.setup()` 构建 `RunContext`
10. `memory.inject()`
11. `pattern.execute()`
12. `memory.writeback()`
13. transcript / artifacts 持久化
14. `context_assembler.finalize()`
15. 返回 `RunResult`

两个关键细节：

- agent 插件 bundle 按 `(session_id, agent_id)` 缓存
- builtin LLM client 按 `agent.id` 缓存

所以"插件生命周期"和"LLM client 生命周期"不是一回事。

## 4. 0.3.0 新增能力

### 类型化结构化输出（Typed Structured Output）

`RunRequest.output_type` 接受一个 `pydantic.BaseModel` 子类。当设置后，runtime 会在 pattern 完成时调用 `PatternPlugin.finalize()` 对原始输出做 `model_validate()`，若失败则抛出 `ModelRetryError` 并触发重试循环。

```python
from pydantic import BaseModel
from openagents import Runtime, RunRequest

class ReviewReport(BaseModel):
    verdict: str
    issues: list[str]
    score: float

request = RunRequest(
    agent_id="reviewer",
    session_id="s1",
    input_text="Review this PR...",
    output_type=ReviewReport,
)
result = await runtime.run_detailed(request)
report: ReviewReport = result.final_output
```

重试次数上限由 `RunBudget.max_validation_retries`（默认 3）控制。超出后抛出 `PermanentToolError`。

### 成本追踪（Cost Tracking）

`RunUsage.cost_usd` 汇总当前 run 的累计 USD 消耗（若 provider 上报则非 None）。

`RunBudget.max_cost_usd` 设置成本上限——超出时 `call_llm()` 抛出 `BudgetExhausted`。

```python
from openagents.interfaces.runtime import RunBudget

request = RunRequest(
    agent_id="coder",
    session_id="s1",
    input_text="...",
    budget=RunBudget(max_cost_usd=0.10, max_steps=20),
)
result = await runtime.run_detailed(request)
print(f"花费 ${result.usage.cost_usd:.4f}")
```

若 provider 不上报 `cost_usd`，字段保持 `None`，且 budget 检查会被静默跳过（会发一次 `budget.cost_skipped` 事件）。

### 流式 API（Streaming API）

`Runtime.run_stream()` 返回 `AsyncIterator[RunStreamChunk]`，逐步推送运行过程：

```python
async for chunk in runtime.run_stream(request):
    match chunk.kind:
        case RunStreamChunkKind.LLM_DELTA:
            print(chunk.payload["delta"], end="", flush=True)
        case RunStreamChunkKind.TOOL_STARTED:
            print(f"\n[tool: {chunk.payload['tool_id']}]")
        case RunStreamChunkKind.RUN_FINISHED:
            result = chunk.result
            break
```

`RunStreamChunkKind` 枚举：`run.started`、`llm.delta`、`llm.finished`、`tool.started`、`tool.delta`、`tool.finished`、`artifact`、`validation.retry`、`run.finished`。

### CLI 工具

`openagents` CLI 提供三个子命令（需要 `[cli]` extra 或直接安装）：

```bash
openagents schema              # 打印所有已注册 builtin plugin 的配置 schema
openagents validate config.json   # 校验 agent 配置文件
openagents list-plugins        # 列出当前已注册的所有 plugin type
```

### 可观测性与日志（Observability & Logging）

SDK 提供两条调试输出通道：

#### Python stdlib 日志（`openagents.*` 命名空间）

```python
from openagents.observability import configure, LoggingConfig

configure(LoggingConfig(level="DEBUG", pretty=True))
```

或在 `agent.json` 里声明：

```json
{
  "logging": {
    "auto_configure": true,
    "level": "INFO",
    "per_logger_levels": {"openagents.llm": "DEBUG"},
    "pretty": true,
    "redact_keys": ["api_key", "authorization"]
  }
}
```

**环境变量覆盖**（CI / 临时调试）：

| 变量 | 示例 |
|---|---|
| `OPENAGENTS_LOG_AUTOCONFIGURE` | `1` |
| `OPENAGENTS_LOG_LEVEL` | `DEBUG` |
| `OPENAGENTS_LOG_LEVELS` | `openagents.llm=DEBUG,openagents.events=INFO` |
| `OPENAGENTS_LOG_PRETTY` | `1` |
| `OPENAGENTS_LOG_STREAM` | `stderr` |
| `OPENAGENTS_LOG_INCLUDE` | `openagents.llm,openagents.events` |
| `OPENAGENTS_LOG_EXCLUDE` | `openagents.events.file_logging` |
| `OPENAGENTS_LOG_REDACT` | `api_key,authorization` |
| `OPENAGENTS_LOG_MAX_VALUE_LENGTH` | `500` |

!!! warning
    `pretty: true` 要求安装 `[rich]` extra：`pip install io-openagent-sdk[rich]`。缺少时抛出 `RichNotInstalledError`。

#### 运行时事件流

`file_logging`（NDJSON）、`otel_bridge`（OTel span）、`rich_console`（终端漂亮打印）均为 `EventBusPlugin` 包装器，可通过 `inner` 字段叠加：

```json
{
  "events": {
    "type": "rich_console",
    "config": {
      "inner": {"type": "file_logging", "config": {"path": "events.ndjson",
        "inner": {"type": "async"}}},
      "include_events": ["tool.*", "llm.succeeded"],
      "show_payload": true,
      "redact_keys": ["api_key"]
    }
  }
}
```

## 5. TypedConfigPluginMixin

所有 builtin plugin 均使用 `TypedConfigPluginMixin` 进行 config 校验。用法：

```python
from pydantic import BaseModel
from openagents.interfaces.typed_config import TypedConfigPluginMixin
from openagents.interfaces.tool import ToolExecutorPlugin

class MyExecutor(TypedConfigPluginMixin, ToolExecutorPlugin):
    class Config(BaseModel):
        timeout_ms: int = 5000
        strict_mode: bool = False

    def __init__(self, config=None):
        super().__init__(config=config or {})
        self._init_typed_config()  # 必须在 __init__ 末尾调用
        # 之后通过 self.cfg.timeout_ms 访问
```

!!! note
    未知 config key 会触发 **warning** 日志，但不会报错（0.3.x 兼容性决策）。未来大版本可能改为 `extra='forbid'`。

## 6. 新增 builtin（0.3.x）

| 所在位置 | key | 说明 |
| --- | --- | --- |
| `tool_executor` | `retry` | 包裹另一个 executor；按错误类别做指数退避重试 |
| `tool_executor` | `filesystem_aware` | 内嵌 `FilesystemExecutionPolicy`（替代旧 `execution_policy: filesystem`）|
| `session` | `jsonl_file` | append-only NDJSON 落盘；重启可重放 |
| `events` | `file_logging` | 包裹内层事件总线 + 把每条事件追加进 NDJSON 审计日志 |
| execution_policy helper（非 seam） | `CompositePolicy` | AND / OR 组合子 policy 列表，嵌到自定义 executor 的 `evaluate_policy` 里用 |
| execution_policy helper（非 seam） | `NetworkAllowlistExecutionPolicy` | 对 `http_request` 类工具做 host/scheme 白名单 |

## 7. 真正应该用好的 state carrier

绝大多数 middle protocol，并不需要新 seam。  
它们需要的是"放在对的 carrier 上"。

### `RunRequest.context_hints`

适合调用方传入的运行提示。例如：

- `task_id`
- `workspace_root`
- `interaction_mode`
- `requested_depth`

### `RunRequest.metadata`

适合外部追踪和观测信息。例如：

- trace id、upstream request id、source、user id

### `RunContext.state`

适合跨 step、跨 turn 保留的 durable state。例如：

- 协议状态机、planner state、session task state、memory 持久状态

### `RunContext.scratch`

适合单轮 run 内的临时状态。例如：

- pending tool id、当前计划草稿、临时 parse 结果

### `RunContext.assembly_metadata`

适合由 `context_assembler` 产出、再被 pattern / skill / tool 消费的协议。例如：

- context packet、transcript trimming 统计、retrieval selection metadata

### `RunArtifact`

适合"本轮 run 真正产出的命名结果"。例如：

- delivery report、patch plan、generated file、research note

## 8. 一个新协议到底该放哪？

按下面顺序判断。

### 它改变 tool 的执行方式吗？

用 `tool_executor`。

### 它决定 tool 能不能执行吗？

覆写 `ToolExecutorPlugin.evaluate_policy()`，或直接用 builtin `filesystem_aware`。

### 它决定 run 进来时吃什么上下文吗？

用 `context_assembler`。

### 它是在回答"你刚做了什么"之类的 follow-up 吗？

覆写 `PatternPlugin.resolve_followup()`（自己的 pattern 子类）。

### 它是在修 provider 的空响应、坏响应、降级路径吗？

覆写 `PatternPlugin.repair_empty_response()`（自己的 pattern 子类）。

### 它只是产品自己的任务语义吗？

不要急着加 seam。优先把它做成 app protocol，放进：

- `context_hints`、`state`、`scratch`、`assembly_metadata`、`skill_metadata`、`RunArtifact`

## 9. 高设计密度 agent 的常见正确姿势

对很多复杂 single-agent 系统来说，最健康的组合是：

- `pattern` 负责 agent loop（含可选的 `resolve_followup` / `repair_empty_response` 覆写）
- `memory` 负责记忆读写
- `tool_executor` 负责 tool 执行形态 + 权限判断（`evaluate_policy`）
- `context_assembler` 负责上下文入口
- `skills` 负责 host-level skill package 的发现、预热、执行
- app protocol 放在 context carrier

这已经足够支撑很多复杂 agent，而不需要 seam 爆炸。

## 10. 什么时候值得新建 seam？

只有在下面这些条件同时满足时，才值得认真考虑：

- 这个问题在多个应用里重复出现
- 它影响的是 runtime 行为，不只是产品语义
- 它需要自己的 selector 和生命周期
- 用现有 carrier 表达会很别扭
- 你准备长期维护 builtin default 和测试

如果没有同时满足，正确答案通常是：

**先做成 app-defined protocol。**

## 11. Hot Reload 与生命周期

`Runtime.reload()` 的语义是：

- 重新加载 config 文件
- 更新未来 run 会用到的 agent 定义
- 清理 removed agent 的缓存
- 失效发生变化 agent 的 LLM client
- 不热切换顶层 `runtime` / `session` / `events`

这再次说明：  
top-level runtime machinery 是稳定容器，不应该混进太多产品基础设施。

## 12. 常见反模式

### 反模式：所有逻辑都塞进 `Pattern.execute()`

应该往外拆：

- execution shape + permission → `tool_executor`（覆写 `evaluate_policy()`）
- context entry → `context_assembler`
- follow-up fallback → 覆写 `PatternPlugin.resolve_followup()`
- response degradation → 覆写 `PatternPlugin.repair_empty_response()`

### 反模式：所有协议都塞进一个大 `state` dict

按语义分层：

- durable state → `state`
- transient state → `scratch`
- assembled context → `assembly_metadata`
- caller hint → `context_hints`
- persisted output → `RunArtifact`

### 反模式：过早把产品语义升级成 seam

如果只有你的 app 会用，先不要进 SDK。

### 反模式：把产品基础设施塞进 SDK

queue、approval、orchestration、UI workflow 应该在 kernel 之上。

## 13. 推荐演化策略

最稳的演化顺序是：

1. 先用现有 seam + carrier 在 app 层实现真实需求
2. 在真实示例或真实产品里证明这个需求是稳定存在的
3. 再判断它是否值得升级为 seam
4. 最后才考虑 builtin / registry / docs

这样可以避免 seam 越抽越多、kernel 越做越胖。

## 14. 内置 CLI

随 SDK 一并安装的 `openagents` 命令覆盖了"脚手架 → 运行 → 迭代 → 发布"完整
开发闭环，包括 `init`、`run`、`chat`、`dev`、`new plugin`、`doctor`、
`config show`、`replay`、`completion`、`version` 等 13 个子命令。实现保持
kernel-clean：CLI 只通过 `Runtime.from_config` / `Runtime.run_detailed` /
`Runtime.reload` 等**公开 API** 消费 kernel，不引入新 seam。

细节见 [内置 CLI 参考](cli.md)（英文：[cli.en.md](cli.en.md)）。

## 15. 下一步看什么

- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [API 参考](api-reference.md)
- [内置 CLI 参考](cli.md)
- [示例说明](examples.md)
- [流式 API](stream-api.md)
- [可观测性](observability.md)
- [0.2 → 0.3 迁移指南](migration-0.2-to-0.3.md)
- [错误参考 / errors.md](errors.md) — 每个 `OpenAgentsError` 子类的 code、retryable、处理建议
