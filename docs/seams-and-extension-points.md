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

这类问题适合进 seam（或 seam 上的可覆写方法）。

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

## 2. 当前已有 seam（共 8 个）

**Agent capability seam：**

| Seam | 内建实现 |
|---|---|
| `memory` | `buffer`（默认）、`window_buffer`、`chain`、`mem0`（需要 `[mem0]` extra）、`markdown_memory`（可读的文件型长期记忆；跨会话持久化到 `MEMORY.md` 索引 + section 子文件）|
| `pattern` | `react`（默认）、`plan_execute`、`reflexion` |
| `tool` | 无内建（应用自行注册）|

**Agent execution seam：**

| Seam | 内建实现 |
|---|---|
| `tool_executor` | `safe`（默认）、`retry`、`filesystem_aware` |
| `context_assembler` | `truncating`（默认）、`head_tail`、`sliding_window`、`importance_weighted` |

**App infrastructure seam：**

| Seam | 内建实现 |
|---|---|
| `runtime` | `default` |
| `session` | `in_memory`（默认）、`jsonl_file`、`sqlite`（需要 `[sqlite]` extra）|
| `events` | `async`（默认）、`file_logging`、`otel_bridge`（需要 `[otel]` extra）、`rich_console`（需要 `[rich]` extra）|
| `skills` | `local`（默认）|

这些是代码里正式的扩展点，总共 **8 个**。

!!! note "Durable execution 不是新 seam"
    `RunRequest.durable=True` 启用的自动 checkpoint + retryable 错误自动恢复是 runtime 层面的行为装饰，**不是新的 seam**。细节见 [api-reference.md §8 Durable execution](api-reference.md#durable-execution)。

!!! info "Seam 合并（2026-04-18，11 → 8）"
    `execution_policy`、`followup_resolver`、`response_repair_policy` 三个独立 seam 被移除。
    它们的功能以可覆写方法的形式归入了现有 seam：

    - `ToolExecutorPlugin.evaluate_policy()` — 取代 `execution_policy`
    - `PatternPlugin.resolve_followup()` — 取代 `followup_resolver`
    - `PatternPlugin.repair_empty_response()` — 取代 `response_repair_policy`

### 从旧 seam 迁移

**`execution_policy` → `ToolExecutorPlugin.evaluate_policy()`**

旧写法（0.2.x）：

```json
{
  "tool_executor": {"type": "safe"},
  "execution_policy": {"type": "filesystem", "config": {"root": "/workspace"}}
}
```

新写法（0.3.x）：

```json
{
  "tool_executor": {"type": "filesystem_aware", "config": {"root": "/workspace"}}
}
```

或者自己写子类：

```python
from openagents.interfaces.tool import ToolExecutorPlugin, PolicyDecision, ToolExecutionRequest
from openagents.plugins.builtin.execution_policy import FilesystemExecutionPolicy

class MyExecutor(ToolExecutorPlugin):
    def __init__(self, config=None):
        super().__init__(config=config or {})
        self._fs_policy = FilesystemExecutionPolicy(root="/workspace")

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        decision = self._fs_policy.check(request)
        if not decision.allowed:
            return decision
        # 自定义额外检查
        return PolicyDecision(allowed=True)
```

**`followup_resolver` → `PatternPlugin.resolve_followup()`**

旧写法（0.2.x）：

```json
{
  "pattern": {"type": "react"},
  "followup_resolver": {"type": "transcript_summary"}
}
```

新写法（0.3.x）——在 pattern 子类里覆写：

```python
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.followup import FollowupResolution
from openagents.plugins.builtin.pattern.react import ReActPattern

class MyReAct(ReActPattern):
    async def resolve_followup(self, *, context):
        # 读 context.transcript，判断能否本地回答
        last_tools = [
            r["tool_id"] for r in context.tool_results[-5:]
        ]
        if "what files did you read" in context.input_text.lower():
            files = [r for r in last_tools if "read_file" in r]
            if files:
                return FollowupResolution(
                    status="resolved",
                    output=f"I read: {', '.join(files)}",
                )
        return None  # abstain，交给 LLM
```

Config 里使用 `impl` 指向自定义类：

```json
{
  "pattern": {"impl": "myapp.plugins.MyReAct"}
}
```

**`response_repair_policy` → `PatternPlugin.repair_empty_response()`**

旧写法（0.2.x）：

```json
{
  "response_repair_policy": {"type": "default_message", "config": {"message": "I was unable to complete the task."}}
}
```

新写法（0.3.x）——在 pattern 子类里覆写：

```python
from openagents.interfaces.response_repair import ResponseRepairDecision

class MyReAct(ReActPattern):
    async def repair_empty_response(
        self, *, context, messages, assistant_content, stop_reason, retries
    ):
        if retries >= 2:
            return ResponseRepairDecision(
                status="repaired",
                output="I was unable to complete the task.",
                reason="max_retries_reached",
            )
        return None  # abstain，让空响应继续传出
```

## 3. 问题 → 推荐层

| 你要解决的问题 | 推荐位置 |
| --- | --- |
| 改 agent loop | `pattern` |
| 改 memory inject / writeback | `memory` |
| 改 tool 本身能力 | `tool` |
| 改 tool 的执行方式 / 权限判断 | `tool_executor`（覆写 `evaluate_policy()`）|
| 改 transcript / artifact 装配 | `context_assembler` |
| 回答"你刚做了什么" | `PatternPlugin.resolve_followup()` 覆写 |
| 降级 bad response / empty response | `PatternPlugin.repair_empty_response()` 覆写 |
| 发现/导入/执行 skill package | 顶层 `skills` 组件 |
| 改 provider HTTP / SSE 适配 | `llm` provider |
| 做 team、mailbox、scheduler | app / product 层，不进 SDK core |

## 4. 每个 seam 真正回答的问题

### `memory`

**这次 run 需要记住什么，以及怎么记？**

典型场景：

- 短期 buffer（`buffer`）
- 滑动窗口（`window_buffer`）
- 链式记忆（`chain`：先问 buffer，再问长期存储）
- 向量/语义检索记忆（`mem0`，需要 `[mem0]` extra）
- 可读的文件型长期记忆（`markdown_memory`：用户目标 / 反馈 / 决策 / 引用，跨会话持久化到 `MEMORY.md` 索引 + section 子文件）

Config 示例：

```json
{
  "memory": {"type": "window_buffer", "config": {"window_size": 10}}
}
```

### `pattern`

**agent loop 长什么样？**

builtin：`react`（默认）、`plan_execute`、`reflexion`。

`react` 实现 ReAct 循环（Thought → Act → Observe），`plan_execute` 先规划再分步执行，`reflexion` 在多轮内做自省修正。

Config 示例：

```json
{
  "pattern": {"type": "react", "config": {"max_steps": 10}}
}
```

### `tool_executor`

**这个 tool 应该怎么跑，以及能不能跑？**

典型场景：

- timeout（`safe`）
- 参数校验（`safe`）
- stream passthrough
- 错误规范化
- 指数退避重试（`retry`）
- filesystem allowlist（`filesystem_aware`）
- 动态权限判断（覆写 `evaluate_policy()`）

builtin：`safe`、`retry`、`filesystem_aware`。

Config 示例（组合 retry + filesystem_aware）：

```json
{
  "tool_executor": {
    "type": "retry",
    "config": {
      "max_attempts": 3,
      "inner": {
        "type": "filesystem_aware",
        "config": {"root": "/workspace", "allow_writes": true}
      }
    }
  }
}
```

### `context_assembler`

**这次 run 到底应该吃进什么上下文？**

典型场景：

- transcript trimming（`truncating`：超出 token 限制时截断）
- head + tail 保留（`head_tail`：保留最早和最新消息）
- 滑动窗口（`sliding_window`）
- 重要性加权保留（`importance_weighted`：高评分消息优先保留）

Config 示例：

```json
{
  "context_assembler": {
    "type": "head_tail",
    "config": {"max_tokens": 8192, "head_turns": 2, "tail_turns": 8}
  }
}
```

### `runtime`

**运行时整体如何初始化和调度？**

通常用 `default`；只有在替换整个执行引擎时才需要自定义。

### `session`

**transcript 和 artifacts 如何持久化？**

- `in_memory`（默认）：不落盘，适合测试和短会话
- `jsonl_file`：append-only NDJSON，重启可重放
- `sqlite`（需要 `[sqlite]` extra）：带索引的持久化

Config 示例：

```json
{
  "session": {"type": "jsonl_file", "config": {"path": "sessions/", "compress": true}}
}
```

### `events`

**运行时事件流如何消费？**

- `async`（默认）：内存异步队列
- `file_logging`：追加到 NDJSON 审计日志
- `otel_bridge`（需要 `[otel]` extra）：导出为 OpenTelemetry span
- `rich_console`（需要 `[rich]` extra）：终端彩色打印

三者均为 `EventBusPlugin` 包装器，可通过 `inner` 字段叠加。

Config 示例（rich_console 包 file_logging 包 async）：

```json
{
  "events": {
    "type": "rich_console",
    "config": {
      "inner": {
        "type": "file_logging",
        "config": {"path": "audit.ndjson", "inner": {"type": "async"}}
      },
      "include_events": ["tool.*", "llm.*"],
      "show_payload": false
    }
  }
}
```

### `skills`

**host-level skill package 如何发现、预热和执行？**

目前仅有 `local` 实现（从本地目录加载 skill bundle）。`skills.prepare_session()` 在每次 run 前被调用，将 skill 描述注入 pattern context。

## 5. Pattern 方法覆写详解

### `PatternPlugin.resolve_followup()`

```python
async def resolve_followup(
    self, *, context: RunContext[Any]
) -> FollowupResolution | None:
    ...
```

**回答什么：这个 follow-up 能不能在本地回答，而不是再问一次模型？**

- 返回 `None` → abstain（继续走 LLM loop），等价于 `FollowupResolution(status="abstain")`
- 返回 `FollowupResolution(status="resolved", output="...")` → 短路，直接用 `output` 作为本次 run 的 `final_output`
- 返回 `FollowupResolution(status="error", reason="...")` → 让调用方 raise

builtin `ReActPattern.execute()` 在开始 LLM loop 之前会先调用此方法一次。  
返回 `resolved` 时，pattern 会跳过 LLM 调用，直接结束 run。

典型场景：

- "上一轮做了什么" / "读了哪些文件" / "调了哪些工具"
- 对话类 follow-up 可以从 transcript 里直接回答

参考实现：`examples/production_coding_agent/app/plugins.py`

### `PatternPlugin.repair_empty_response()`

```python
async def repair_empty_response(
    self,
    *,
    context: RunContext[Any],
    messages: list[dict[str, Any]],
    assistant_content: list[dict[str, Any]],
    stop_reason: str | None,
    retries: int,
) -> ResponseRepairDecision | None:
    ...
```

**回答什么：provider 给了空响应或坏响应时，系统应该怎么降级？**

- 返回 `None` → abstain（让空响应继续传出），等价于 `ResponseRepairDecision(status="abstain")`
- 返回 `ResponseRepairDecision(status="repaired", output="...")` → 用 `output` 替换空响应
- 返回 `ResponseRepairDecision(status="error", reason="...")` → 让调用方 raise

builtin patterns 在 provider 给空串时调用此方法一次（每次发生空响应时）。  
`retries` 参数标识这次是第几次尝试修复（从 0 开始）。

典型场景：

- empty response 诊断（`stop_reason` 异常时输出诊断信息）
- malformed JSON response（在降级响应里提示重试）
- provider-specific degradation（给出兜底文案）

## 6. `ToolExecutorPlugin.evaluate_policy()` 详解

```python
async def evaluate_policy(
    self, request: ToolExecutionRequest
) -> PolicyDecision:
    ...
```

**回答什么：这个 tool 在当前请求下能不能执行？**

默认实现 return `PolicyDecision(allowed=True)`（allow-all）。

`PolicyDecision` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `allowed` | `bool` | 是否允许执行 |
| `reason` | `str` | 拒绝原因（建议填，方便调试）|
| `metadata` | `dict` | 策略元信息（审计、UI 展示）|

**如何组合多个 policy：**

```python
from openagents.plugins.builtin.execution_policy import (
    FilesystemExecutionPolicy,
    NetworkAllowlistExecutionPolicy,
    CompositePolicy,
)
from openagents.interfaces.tool import ToolExecutorPlugin, PolicyDecision

class SandboxedExecutor(ToolExecutorPlugin):
    def __init__(self, config=None):
        super().__init__(config=config or {})
        self._policy = CompositePolicy(
            mode="AND",  # 全部通过才放行
            policies=[
                FilesystemExecutionPolicy(root="/workspace", allow_writes=True),
                NetworkAllowlistExecutionPolicy(
                    allowed_hosts=["api.github.com"],
                    allowed_schemes=["https"],
                ),
            ],
        )

    async def evaluate_policy(self, request) -> PolicyDecision:
        return self._policy.check(request)
```

**execution_policy helper 列表：**

| 类名 | 说明 |
|---|---|
| `FilesystemExecutionPolicy` | 限制文件操作到指定 root 目录 |
| `NetworkAllowlistExecutionPolicy` | host / scheme 白名单 |
| `CompositePolicy` | AND / OR 组合多个子 policy |

这些 helper 是独立类，不是 plugin，通过 `openagents.plugins.builtin.execution_policy` 导入后嵌到自定义 executor 里用。

完整参考：`examples/research_analyst/app/executor.py`

## 7. 什么时候不要新建 seam

仅仅因为"我有一个协议"并不意味着"我应该有一个 seam"。

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

## 8. 最常见的正确设计

对很多复杂 single-agent 系统来说，最健康的架构是：

- `pattern` 负责 loop（含 `resolve_followup` / `repair_empty_response` 两个可选覆写）
- `memory` 负责记忆
- `tool_executor` 负责 execution shape + permission（覆写 `evaluate_policy`）
- `context_assembler` 负责 context entry
- `skills` 负责 host-level skill package 的发现、预热、执行
- app-defined protocol 放在 context carrier

这样可以做到高设计密度，而不需要 seam 爆炸。

## 9. Follow-up / Repair 状态语义

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

## 10. 什么时候值得从 app protocol 升到 seam

只有当下面这些条件同时满足时，才值得升级：

- 这个问题在多个应用里重复出现
- 它影响的是 runtime 行为，而不是产品语义
- 它需要自己的 selector 和生命周期
- 用现有 carrier 表达会很别扭
- 你准备维护 builtin default 和测试

否则，正确答案通常是：

**继续把它留在 app-defined protocol。**

## 11. 常见反模式

### 反模式：一切都塞进 `Pattern.execute()`

应该往外拆：

- execution shape + permission → `tool_executor`（覆写 `evaluate_policy()`）
- context entry → `context_assembler`
- follow-up fallback → 覆写 `PatternPlugin.resolve_followup()`（在 `PatternPlugin` 子类上）
- provider degradation → 覆写 `PatternPlugin.repair_empty_response()`（在 `PatternPlugin` 子类上）

### 反模式：一个巨大的无类型 state blob

应该按语义拆：

- durable state → `state`
- transient state → `scratch`
- assembled context → `assembly_metadata`
- caller hint → `context_hints`
- persisted output → `RunArtifact`

### 反模式：产品基础设施塞进 SDK

queue、approval、orchestration、UI workflow 不应该塞进 kernel。

## 12. 最稳的演化顺序

推荐的顺序是：

1. 先在 app 或 example 里用 `impl` 做出真实需求
2. 证明这个需求是稳定、可复用的
3. 再考虑提升成 seam
4. 最后再补 builtin / registry / docs

这是避免"先抽象、后后悔"的最好方式。

## 13. 长期 trade-off

OpenAgents 最健康的长期路线应该是：

- **small kernel**
- **few strong seams**
- **rich app protocols**

而不是：

- 巨大的 seam catalog
- 模糊的产品边界
- 所有语义都被迫进 SDK

## 14. 继续阅读

- [开发者指南](developer-guide.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [API 参考](api-reference.md)
- [示例说明](examples.md)
- [可观测性](observability.md)
- [0.2 → 0.3 迁移指南](migration-0.2-to-0.3.md)
