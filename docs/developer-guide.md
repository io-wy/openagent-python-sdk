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

这是 runtime 明确开放出来的控制缝：

- capability seam
  - `memory`
  - `pattern`
  - `tool`
- execution seam
  - `tool_executor`
  - `execution_policy`
  - `context_assembler`
- semantic recovery seam
  - `followup_resolver`
  - `response_repair_policy`
- app infra seam
  - `runtime`
  - `session`
  - `events`
  - `skills`

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
8. 用 `execution_policy + tool_executor` 重新绑定 tools
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

所以“插件生命周期”和“LLM client 生命周期”不是一回事。

## 4. 真正应该用好的 state carrier

绝大多数 middle protocol，并不需要新 seam。  
它们需要的是“放在对的 carrier 上”。

### `RunRequest.context_hints`

适合调用方传入的运行提示。

例如：

- `task_id`
- `workspace_root`
- `interaction_mode`
- `requested_depth`

如果这个信息是 caller 在发起 run 时就知道的，优先放这里。

### `RunRequest.metadata`

适合外部追踪和观测信息。

例如：

- trace id
- upstream request id
- source
- user id

如果主要是给系统或观测链路看的，用 `metadata`。

### `RunContext.state`

适合跨 step、跨 turn 保留的 durable state。

例如：

- 协议状态机
- planner state
- session task state
- memory 持久状态
- last successful delivery

### `RunContext.scratch`

适合单轮 run 内的临时状态。

例如：

- pending tool id
- 当前计划草稿
- 临时 parse 结果
- 当前 step 的局部变量

如果这个东西当前 run 结束后丢掉也没关系，就放 `scratch`。

### `RunContext.assembly_metadata`

适合由 `context_assembler` 产出、再被 pattern / skill / tool 消费的协议。

例如：

- context packet
- transcript trimming 统计
- retrieval selection metadata
- task envelope
- summary provenance

这是做 app-defined context protocol 最好的位置之一。

### `RunArtifact`

适合“本轮 run 真正产出的命名结果”。

例如：

- delivery report
- patch plan
- generated file
- research note
- evaluation output

如果某个结果未来可能被 session、UI、上层系统消费，就不要只藏在 `state` 里。

## 5. 一个新协议到底该放哪？

按下面顺序判断。

### 它改变 tool 的执行方式吗？

用 `tool_executor`。

例如：

- timeout
- 参数校验
- stream passthrough
- 错误规范化

### 它决定 tool 能不能执行吗？

用 `execution_policy`。

例如：

- allow / deny
- filesystem root 限制
- 动态权限判断
- 策略元信息

### 它决定 run 进来时吃什么上下文吗？

用 `context_assembler`。

例如：

- transcript trimming
- artifact trimming
- retrieval packaging
- task packet assembly

### 它是在回答“你刚做了什么”之类的 follow-up 吗？

用 `followup_resolver`。

### 它是在修 provider 的空响应、坏响应、降级路径吗？

用 `response_repair_policy`。

### 它只是产品自己的任务语义吗？

不要急着加 seam。

优先把它做成 app protocol，放进：

- `context_hints`
- `state`
- `scratch`
- `assembly_metadata`
- `skill_metadata`
- `RunArtifact`

## 6. 高设计密度 agent 的常见正确姿势

对很多复杂 single-agent 系统来说，最健康的组合是：

- `pattern` 负责 agent loop
- `memory` 负责记忆读写
- `tool_executor` 负责 tool 执行形态
- `execution_policy` 负责权限判断
- `context_assembler` 负责上下文入口
- `skills` 负责 host-level skill package 的发现、预热、执行
- app protocol 放在 context carrier

这已经足够支撑很多复杂 agent，而不需要 seam 爆炸。

## 7. 什么时候值得新建 seam？

只有在下面这些条件同时满足时，才值得认真考虑：

- 这个问题在多个应用里重复出现
- 它影响的是 runtime 行为，不只是产品语义
- 它需要自己的 selector 和生命周期
- 用现有 carrier 表达会很别扭
- 你准备长期维护 builtin default 和测试

如果没有同时满足，正确答案通常是：

**先做成 app-defined protocol。**

## 8. Hot Reload 与生命周期

`Runtime.reload()` 的语义是：

- 重新加载 config 文件
- 更新未来 run 会用到的 agent 定义
- 清理 removed agent 的缓存
- 失效发生变化 agent 的 LLM client
- 不热切换顶层 `runtime` / `session` / `events`

这再次说明：  
top-level runtime machinery 是稳定容器，不应该混进太多产品基础设施。

## 9. 常见反模式

### 反模式：所有逻辑都塞进 `Pattern.execute()`

这样最容易把整个系统写糊。

应该往外拆：

- execution shape -> `tool_executor`
- permission -> `execution_policy`
- context entry -> `context_assembler`
- follow-up fallback -> `followup_resolver`
- response degradation -> `response_repair_policy`

### 反模式：所有协议都塞进一个大 `state` dict

按语义分层：

- durable state -> `state`
- transient state -> `scratch`
- assembled context -> `assembly_metadata`
- caller hint -> `context_hints`
- persisted output -> `RunArtifact`

### 反模式：过早把产品语义升级成 seam

如果只有你的 app 会用，先不要进 SDK。

### 反模式：把产品基础设施塞进 SDK

queue、approval、orchestration、UI workflow 应该在 kernel 之上。

## 10. 推荐演化策略

最稳的演化顺序是：

1. 先用现有 seam + carrier 在 app 层实现真实需求
2. 在真实示例或真实产品里证明这个需求是稳定存在的
3. 再判断它是否值得升级为 seam
4. 最后才考虑 builtin / registry / docs

这样可以避免 seam 越抽越多、kernel 越做越胖。

## 11. 下一步看什么

- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [API 参考](api-reference.md)
- [示例说明](examples.md)

## 新增 builtin (0.3.x)

| seam | type key | 说明 |
| --- | --- | --- |
| `tool_executor` | `retry` | 包裹另一个 executor；按错误类别做指数退避重试 |
| `execution_policy` | `composite` | AND / OR 组合子 policy 列表 |
| `execution_policy` | `network_allowlist` | 对 `http_request` 类工具做 host/scheme 白名单 |
| `followup_resolver` | `rule_based` | 基于 regex → 模板的本地跟进回答（替代走模型）|
| `session` | `jsonl_file` | append-only NDJSON 落盘；重启可重放 |
| `events` | `file_logging` | 包裹内层事件总线 + 把每条事件追加进 NDJSON 审计日志 |
| `response_repair_policy` | `strict_json` | 从 markdown fenced / 裸 JSON 片段里抢救结构化输出，miss 时可 fallback 到 `basic` |

完整示例见 [`examples/research_analyst/`](../examples/research_analyst/README.md)，对应集成测试在 `tests/integration/test_research_analyst_example.py`。
