# Seam 与扩展点

这份文档专门回答一个问题：

**当你需要新行为时，这个东西应该放哪一层？**

如果这个问题答错了，最后所有东西都会堆进 `Pattern.execute()`，
kernel 会变糊，产品功能也会误伤 SDK 边界。

## 1. 先分三类问题

### Kernel protocol 问题

这类问题会改动最底层的稳定协议对象。

例如：

- `RunRequest`
- `RunResult`
- `RunContext`
- `ToolExecutionRequest`
- `ContextAssemblyResult`

这层应该很少改。

### SDK seam 问题

这类问题改变的是 runtime 的可复用行为。

例如：

- tool 怎么执行
- tool 能不能执行
- run 进来吃什么上下文
- follow-up 能不能本地回答
- provider 坏响应怎么降级

这类问题适合进 seam。

### App protocol 问题

这类问题表达的是你的产品语义。

例如：

- coding-task envelope
- planner contract
- review state
- branch ownership
- artifact taxonomy
- product status semantics

这类问题通常 **不应该** 变成 SDK seam。

## 2. 当前已有 seam

Agent capability seam：

- `memory`
- `pattern`
- `tool`

Agent execution seam：

- `tool_executor`（tool 执行 + 权限判断，二合一）
- `context_assembler`

App infrastructure seam：

- `runtime`
- `session`
- **`events`**：
  - `async`（默认，内存）
  - `file_logging`（NDJSON 落盘）
  - `otel_bridge`（OpenTelemetry span，需要 `[otel]` extra）
  - `rich_console`（终端漂亮打印，需要 `[rich]` extra）
- `skills`

这些已经是当前代码里的正式扩展点，总共 **8 个**。

> **Seam consolidation (2026-04-18)**：`execution_policy`、`followup_resolver`、
> `response_repair_policy` 三个 seam 已被移除（11 → 8）。
>
> - Tool 权限判断改为 `ToolExecutorPlugin.evaluate_policy()` 方法（默认 allow-all），
>   自定义实现可以覆写它（参考 builtin `filesystem_aware` executor）。
> - Follow-up resolution 改为 `PatternPlugin.resolve_followup()` 方法（默认 abstain / None），
>   subclass 想要本地短路答复时覆写即可。
> - Empty-response repair 改为 `PatternPlugin.repair_empty_response()` 方法（默认 abstain），
>   subclass 想要生成降级响应时覆写即可。

## 3. 问题 -> 推荐层

| 你要解决的问题 | 推荐位置 |
| --- | --- |
| 改 agent loop | `pattern` |
| 改 memory inject / writeback | `memory` |
| 改 tool 本身能力 | `tool` |
| 改 tool 的执行方式 / 权限判断 | `tool_executor`（覆写 `evaluate_policy()`） |
| 改 transcript / artifact 装配 | `context_assembler` |
| 回答“你刚做了什么” | `PatternPlugin.resolve_followup()` 覆写 |
| 降级 bad response / empty response | `PatternPlugin.repair_empty_response()` 覆写 |
| 发现/导入/执行 skill package | 顶层 `skills` 组件 |
| 改 provider HTTP / SSE 适配 | `llm` provider |
| 做 team、mailbox、scheduler | app / product 层，不进 SDK core |

## 4. 每个 seam 真正回答的问题

### `tool_executor`

它回答的是：

**这个 tool 应该怎么跑，以及能不能跑？**

典型场景：

- timeout
- 参数校验
- stream passthrough
- 错误规范化
- filesystem allowlist / deny-by-default tool set（覆写 `evaluate_policy()`）
- 动态权限判断 / policy metadata

builtin：`safe`、`retry`、`filesystem_aware`。需要多策略组合（例如 filesystem + network
allowlist）时，写一个 subclass 覆写 `evaluate_policy()` 并组合 `execution_policy/` 下的
helper（`FilesystemExecutionPolicy`、`NetworkAllowlistExecutionPolicy`、`CompositePolicy`）。
`examples/research_analyst/app/executor.py` 是参考实现。

### `context_assembler`

它回答的是：

**这次 run 到底应该吃进什么上下文？**

典型场景：

- transcript trimming
- artifact trimming
- retrieval packaging
- summary injection
- task packet assembly

### `PatternPlugin.resolve_followup()`（pattern 方法，不是独立 seam）

它回答的是：

**这个 follow-up 能不能在本地回答，而不是再问一次模型？**

默认返回 `None`（abstain），builtin `ReActPattern.execute()` 会先调用它短路。

典型场景：

- 上一轮做了什么
- 读了哪些文件
- 调了哪些工具

参考 `examples/research_analyst/app/followup_pattern.py`、
`examples/production_coding_agent/app/plugins.py`。

### `PatternPlugin.repair_empty_response()`（pattern 方法，不是独立 seam）

它回答的是：

**provider 给了空响应或坏响应时，系统应该怎么降级？**

默认返回 `None`（abstain），builtin patterns 会在 provider 给空串时调用它一次。

典型场景：

- empty response 诊断
- malformed response
- structured fallback
- provider-specific degradation

## 5. 什么时候不要新建 seam

仅仅因为“我有一个协议”并不意味着“我应该有一个 seam”。

如果一个行为：

- 只属于你的 app
- 本质上是结构化数据，不是 runtime 控制行为
- 只会被你的 custom pattern / tool / app protocol 消费
- 不会跨产品复用

那它就应该留在 app 层。

推荐 carrier：

- `RunRequest.context_hints`
- `RunRequest.metadata`
- `RunContext.state`
- `RunContext.scratch`
- `RunContext.assembly_metadata`
- `RunArtifact.metadata`

这就是很多 middle protocol 的正确落点。

## 6. 最常见的正确设计

对很多复杂 single-agent 系统来说，最健康的架构是：

- `pattern` 负责 loop（含 `resolve_followup` / `repair_empty_response` 两个可选覆写）
- `memory` 负责记忆
- `tool_executor` 负责 execution shape + permission（覆写 `evaluate_policy`）
- `context_assembler` 负责 context entry
- `skills` 负责 host-level skill package 的发现、预热、执行
- app-defined protocol 放在 context carrier

这样可以做到高设计密度，而不需要 seam 爆炸。

## 7. Follow-up / Repair 状态语义

这两个 pattern 方法故意保持轻量状态树。

### `PatternPlugin.resolve_followup()`

返回类型：`FollowupResolution | None`。推荐 status：

- `resolved`（直接使用 `output`）
- `abstain`（继续走 LLM loop）
- `error`（让调用方 raise）

返回 `None` 等同于 abstain。

### `PatternPlugin.repair_empty_response()`

返回类型：`ResponseRepairDecision | None`。推荐 status：

- `repaired`（直接使用 `output`）
- `abstain`（让空响应继续传出）
- `error`（让调用方 raise）

返回 `None` 等同于 abstain。

这是有意为之。  
SDK 不应该替所有产品定义一棵庞大的语义恢复状态树。

## 8. 什么时候值得从 app protocol 升到 seam

只有当下面这些条件同时满足时，才值得升级：

- 这个问题在多个应用里重复出现
- 它影响的是 runtime 行为，而不是产品语义
- 它需要自己的 selector 和生命周期
- 用现有 carrier 表达会很别扭
- 你准备维护 builtin default 和测试

否则，正确答案通常是：

**继续把它留在 app-defined protocol。**

## 9. 常见反模式

### 反模式：一切都塞进 `Pattern.execute()`

这样最容易失控。

应该往外拆：

- execution shape + permission -> `tool_executor`（覆写 `evaluate_policy()`）
- context entry -> `context_assembler`
- follow-up fallback -> 覆写 `PatternPlugin.resolve_followup()`（在 `PatternPlugin` 子类上）
- provider degradation -> 覆写 `PatternPlugin.repair_empty_response()`（在 `PatternPlugin` 子类上）

### 反模式：一个巨大的无类型 state blob

应该按语义拆：

- durable state -> `state`
- transient state -> `scratch`
- assembled context -> `assembly_metadata`
- caller hint -> `context_hints`
- persisted output -> `RunArtifact`

### 反模式：产品基础设施塞进 SDK

queue、approval、orchestration、UI workflow 不应该塞进 kernel。

## 10. 最稳的演化顺序

推荐的顺序是：

1. 先在 app 或 example 里用 `impl` 做出真实需求
2. 证明这个需求是稳定、可复用的
3. 再考虑提升成 seam
4. 最后再补 builtin / registry / docs

这是避免“先抽象、后后悔”的最好方式。

## 11. 长期 trade-off

OpenAgents 最健康的长期路线应该是：

- **small kernel**
- **few strong seams**
- **rich app protocols**

而不是：

- 巨大的 seam catalog
- 模糊的产品边界
- 所有语义都被迫进 SDK

## 12. 继续阅读

- [开发者指南](developer-guide.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [API 参考](api-reference.md)
- [示例说明](examples.md)
