# OpenAgents SDK

基于一个小而清晰的 runtime kernel，构建高设计密度、协议感明确的 agent。

OpenAgents 是一个配置驱动、异步优先、可插件化的 Agent SDK，适合那些希望真正掌控
agent 行为、而不是把所有复杂度都塞进一个巨大 `Pattern.execute()` 的开发者。

它特别适合：

- 希望拿到清晰 runtime 而不是黑箱框架的团队
- 在做 coding agent、research agent、workflow agent 的开发者
- 需要自己定义 middle protocol、安全规则、上下文逻辑的产品
- 想先把 kernel 打稳，再在上层补产品基础设施的应用

它有意 **不是** multi-agent control plane。一次 `run` 只执行一个 `agent_id`。
team orchestration、mailbox、scheduler、approval、产品 UX，都应该放在这层之上。

## 为什么是 OpenAgents

很多 agent 框架会把三类完全不同的问题揉成一个抽象：

1. 定义一次 run 是什么的 kernel protocol
2. 决定 run 如何执行的 runtime seam
3. 只属于你自己产品的 middle protocol

OpenAgents 的设计重点，就是把这三层拆开。

```text
App / Product Protocols
    task envelopes, coding plans, review contracts, approvals, UI semantics
            |
            v
SDK Runtime Seams（2026-04-18 seam 合并后共 8 个）
    memory, pattern, tool, tool_executor, context_assembler,
    runtime, session, events, skills
（合并入其他 seam 的旧 seam：
  execution_policy -> tool_executor.evaluate_policy
  followup_resolver -> PatternPlugin.resolve_followup
  response_repair_policy -> PatternPlugin.repair_empty_response）
            |
            v
Kernel Protocols
    RunRequest, RunResult, RunContext,
    ToolExecutionRequest, ToolExecutionResult, SessionArtifact
```

这种分层会带来四个直接好处：

- 一个小而明确的 kernel
- 一组稳定的 runtime seam，而不是到处 monkeypatch
- 不 fork SDK 也能发明 app 自己的协议
- 文档和测试都可以按协议栈去描述系统

## 它是什么

- 一个 **single-agent runtime kernel**
- 一套围绕 memory、pattern、tool、session、runtime、events，以及顶层 `skills` 组件的 **plugin execution model**
- 一组围绕执行策略和语义恢复的 **middle-protocol host**
- 一套围绕 `RunRequest`、`RunResult`、`RunUsage`、`RunArtifact`、`RunContext` 的 **结构化 runtime contract**

## 它不是什么

- 不是内置 multi-agent 平台
- 不是 job scheduler 或 queue system
- 不是 durable product control plane
- 不是 UI 框架
- 不是“所有产品问题都给一个 seam”的大而全 SDK

推荐的分工方式是：

- OpenAgents SDK 负责 kernel 和少量高价值 seam
- 你的产品负责 durable infra、UX、team orchestration、业务语义
- 你的应用在 kernel carriers 之上发明自己的 middle protocol

## 核心心智模型

一个 OpenAgents 应用，最好按三层来理解。

### 1. Kernel Protocols

这些对象应该尽量保持小、清晰、稳定，它们定义的是系统在运行中“到底传的是什么”：

- `RunRequest`
- `RunResult`
- `RunUsage`
- `RunArtifact`
- `RunContext`
- `ToolExecutionRequest`
- `ToolExecutionResult`
- `ContextAssemblyResult`
- `SessionArtifact`

### 2. SDK Seams

这些 seam 是 SDK 明确承认的“可改行为位置”：

- capability seams:
  - `memory`
  - `pattern`
  - `tool`
- execution seams:
  - `tool_executor`（内含 `evaluate_policy()` 做权限判断）
  - `context_assembler`
- app infrastructure seams:
  - `runtime`
  - `session`
  - `events`
  - `skills`

Pattern 子类方法覆写（2026-04-18 起不再是独立 seam）：

- `PatternPlugin.resolve_followup()` — 本地 follow-up 短路
- `PatternPlugin.repair_empty_response()` — 空响应/坏响应降级

这些 seam 的作用不是承载所有产品语义，而是给 runtime 留出少量、清晰、可测试的控制缝。

### 3. App-Defined Middle Protocols

高设计密度 agent 的很多价值，其实应该放在这里。比如：

- coding-task envelope
- review contract
- action summary
- permission envelope
- retrieval plan
- artifact taxonomy

OpenAgents 不试图把这些全部内建，而是提供 carrier，让你自己在 app 层定义协议：

- `RunRequest.context_hints`
- `RunRequest.metadata`
- `RunContext.state`
- `RunContext.scratch`
- `RunContext.assembly_metadata`
- `RunArtifact.metadata`

## 运行时结构

运行时链路是故意做得很显式的。

```text
Caller
  -> Runtime facade
    -> Runtime plugin
      -> Session manager + Event bus
      -> Context assembler
      -> Pattern setup
      -> Memory inject / writeback
      -> Bound tools (policy + executor)
      -> LLM provider
      -> RunResult
```

在代码层，这意味着：

- `Runtime` 是对外 facade
- `DefaultRuntime` 是 builtin orchestrator
- `RunContext` 是单次 run 里给 tool / pattern 使用的状态载体
- plugin 可以来自 builtin registry、decorator registry，或配置里的 `impl`

## 快速开始

安装：

```bash
uv add io-openagent-sdk
```

按需安装 extras：

```bash
uv add "io-openagent-sdk[openai]"
uv add "io-openagent-sdk[mem0]"
uv add "io-openagent-sdk[mcp]"
uv add "io-openagent-sdk[all]"
```

最小配置：

```json
{
  "version": "1.0",
  "agents": [
    {
      "id": "assistant",
      "name": "demo-agent",
      "memory": {"type": "window_buffer", "on_error": "continue"},
      "pattern": {"type": "react"},
      "llm": {"provider": "mock"},
      "tools": [
        {"id": "search", "type": "builtin_search"}
      ]
    }
  ]
}
```

异步用法：

```python
import asyncio

from openagents import Runtime


async def main() -> None:
    runtime = Runtime.from_config("agent.json")
    result = await runtime.run(
        agent_id="assistant",
        session_id="demo",
        input_text="hello",
    )
    print(result)


asyncio.run(main())
```

同步用法：

```python
from openagents import run_agent

result = run_agent(
    "agent.json",
    agent_id="assistant",
    session_id="demo",
    input_text="hello",
)
print(result)
```

结构化同步入口：

```python
from openagents import run_agent_detailed, run_agent_with_dict

result = run_agent_detailed(
    "agent.json",
    agent_id="assistant",
    session_id="demo",
    input_text="hello",
)

inline = run_agent_with_dict(
    {
        "version": "1.0",
        "agents": [
            {
                "id": "assistant",
                "name": "demo",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react"},
                "llm": {"provider": "mock"},
                "tools": []
            }
        ]
    },
    agent_id="assistant",
    session_id="demo",
    input_text="hello",
)
```

## 内置组件

Builtin memory：

- `buffer`
- `window_buffer`
- `mem0`
- `chain`

Builtin pattern：

- `react`
- `plan_execute`
- `reflexion`

Builtin app infrastructure：

- runtime: `default`
- session manager: `in_memory`
- event bus: `async`

Builtin execution seams：

- tool executor: `safe`
- execution policy: `filesystem`
- context assembler: `summarizing`
- follow-up resolver: `basic`
- response repair policy: `basic`

Builtin tools：

- Search: `builtin_search`
- Files: `read_file`, `write_file`, `list_files`, `delete_file`
- Text: `grep_files`, `ripgrep`, `json_parse`, `text_transform`
- HTTP / network: `http_request`, `url_parse`, `url_build`, `query_param`, `host_lookup`
- System: `execute_command`, `get_env`, `set_env`
- Time: `current_time`, `date_parse`, `date_diff`
- Random: `random_int`, `random_choice`, `random_string`, `uuid`
- Math: `calc`, `percentage`, `min_max`
- MCP bridge: `mcp`

## Selector 规则

OpenAgents 提供两种 selector：

- `type`
  - 选择 builtin plugin 或 decorator 注册名
- `impl`
  - 通过 Python dotted path 导入符号

规则如下：

- 顶层 `runtime`、`session`、`events` 只能选一个 selector
- agent 级 plugin / seam 至少要有一个 `type` 或 `impl`
- agent 级如果两者都配了，loader 以 `impl` 为准

## 为什么 seam 故意不多

OpenAgents **不会** 为每一种产品问题都发一个 seam。

这是有意为之。当前的原则就是：

- 固定 kernel protocol
- 开放少量高价值 runtime seam
- 提供顶层 `skills` 组件承载 host-style skill package
- 把大量产品协议留给开发者自己发明

如果你的问题是：

- “tool 应该怎么执行 / 能不能执行？”
  - 用 `tool_executor`（覆写 `evaluate_policy()` 做权限）
- “这次 run 应该吃进什么上下文？”
  - 用 `context_assembler`
- “这个 follow-up 能不能本地回答？”
  - 在 pattern 子类上覆写 `PatternPlugin.resolve_followup()`
- “provider 坏响应应该怎么降级？”
  - 在 pattern 子类上覆写 `PatternPlugin.repair_empty_response()`
- “我的 coding agent 应该怎样表示 review task、work plan、product state？”
  - 在 kernel carrier 之上设计 app protocol

## 示例

当前仓库只保留两组维护中的 example：

- [examples/quickstart](examples/quickstart)
  - 第一次接触 kernel 的最小 builtin-only setup
- [examples/production_coding_agent](examples/production_coding_agent)
  - production-style coding agent，展示 task packet、persistent memory、follow-up recovery、delivery artifacts，以及 app-defined protocol 如何压在 SDK 之上

完整示例导览请直接看 [docs/examples.md](docs/examples.md)。

## 文档

开发者文档在 [docs/](docs/README.md)。

推荐阅读顺序：

1. [仓库结构](docs/repository-layout.md)
2. [Developer Guide](docs/developer-guide.md)
3. [Seams And Extension Points](docs/seams-and-extension-points.md)
4. [Configuration](docs/configuration.md)
5. [Plugin Development](docs/plugin-development.md)
6. [API Reference](docs/api-reference.md)
7. [Examples](docs/examples.md)

如果你更关心“如何设计协议，而不是如何跑 hello world”，建议直接从
`Developer Guide` 和 `Seams And Extension Points` 开始看。

## 当前边界

作为 single-agent kernel，它已经足够支撑很多高设计密度 agent。

下一层更适合放的是：

- multi-agent orchestration
- background jobs
- approvals
- durable infra
- UI 和产品工作流

那一层应该消费这个 SDK，而不是把它反向塞进 SDK 里。
