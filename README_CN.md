# OpenAgents SDK

> 配置驱动、异步优先、可插件化的单 Agent 运行时内核 SDK。

[![PyPI](https://img.shields.io/pypi/v/io-openagent-sdk)](https://pypi.org/project/io-openagent-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/io-openagent-sdk)](https://pypi.org/project/io-openagent-sdk/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A592%25-brightgreen)](#开发)

[English](README.md) | **中文**

---

## 概述

OpenAgents 提供一个小而清晰、行为完全显式的单 Agent 运行时内核。它适合那些希望真正掌控 agent 行为、而不是把所有复杂度都埋进一个黑箱框架的开发者。

**适合的场景：**

- 希望拿到清晰、可审计的 agent runtime 而不是魔法框架的团队
- 在做 coding agent、research agent、workflow agent 的开发者
- 需要自定义 middle protocol、安全规则、上下文逻辑的产品
- 想先把 kernel 打稳，再在上层补产品基础设施的应用

**有意不做的事：**

- Multi-agent control plane（一次 `run` 只执行一个 `agent_id`）
- Job scheduler 或 queue system
- Durable product control plane
- UI 框架

Team orchestration、mailbox、scheduler、approval、产品 UX 都应该放在这层之上。

---

## 为什么选择 OpenAgents

很多 agent 框架把三类完全不同的问题揉进了一个抽象：

1. **Kernel protocol** — 一次 run 是*什么*（输入、输出、状态）
2. **Runtime seam** — 一次 run 如何*执行*（memory、tools、context assembly）
3. **Product middle protocol** — 只有*你的应用*才理解的东西（task envelope、review contract、权限模型）

OpenAgents 把这三层拆开：

```
┌─────────────────────────────────────────────────┐
│           App / Product Protocols               │
│  task envelopes · coding plans · approvals      │
│  review contracts · artifact taxonomies         │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│              SDK Runtime Seams (8 个)           │
│  memory · pattern · tool · tool_executor        │
│  context_assembler · runtime · session          │
│  events · skills                                │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│             Kernel Protocols                    │
│  RunRequest · RunResult · RunContext            │
│  ToolExecutionRequest · SessionArtifact         │
└─────────────────────────────────────────────────┘
```

这种分层带来的好处：
- 一个小而明确的 kernel，行为完全可预期
- 一组稳定的 runtime seam，而不是到处 monkeypatch
- 不 fork SDK 也能发明 app 自己的协议
- 文档和测试可以按协议栈去描述系统

---

## 安装

**核心安装（零可选依赖）：**

```bash
pip install io-openagent-sdk
# 或
uv add io-openagent-sdk
```

**可选 extras：**

| Extra | 安装内容 | 适用场景 |
|---|---|---|
| `cli` | `rich`、`questionary`、`watchdog`、`pyyaml` | 交互式 CLI、热重载、彩色输出 |
| `openai` | `openai`、`httpx` | OpenAI 兼容 LLM provider |
| `mem0` | `mem0ai` | 跨 session 持久语义记忆 |
| `mcp` | `mcp` | MCP tool bridge |
| `otel` | `opentelemetry-api` | OpenTelemetry 事件桥接 |
| `sqlite` | `aiosqlite` | SQLite 持久化 session |
| `tokenizers` | `tiktoken` | OpenAI 精确 token 计数 |
| `yaml` | `pyyaml` | YAML 配置文件支持 |
| `all` | 以上全部 | 开发 / 全功能部署 |

```bash
uv add "io-openagent-sdk[cli]"
uv add "io-openagent-sdk[openai,mcp]"
uv add "io-openagent-sdk[all]"
```

**Python ≥ 3.10。**

---

## 快速开始

### 1. 用 JSON 定义你的 agent

```json
{
  "version": "1.0",
  "agents": [
    {
      "id": "assistant",
      "name": "demo-agent",
      "memory": {"type": "window_buffer", "on_error": "continue"},
      "pattern": {"type": "react"},
      "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
      "tools": [
        {"id": "search", "type": "builtin_search"},
        {"id": "files", "type": "read_file"}
      ]
    }
  ]
}
```

### 2. 通过 CLI 运行

```bash
# 单轮执行
openagents run agent.json --input "你好"

# 交互式多轮对话 REPL
openagents chat agent.json

# 开发模式：配置变更时自动热重载
openagents dev agent.json
```

### 3. Python 异步调用

```python
import asyncio
from openagents import Runtime

async def main() -> None:
    runtime = Runtime.from_config("agent.json")
    result = await runtime.run(
        agent_id="assistant",
        session_id="demo",
        input_text="当前目录有哪些文件？",
    )
    print(result.output)

asyncio.run(main())
```

### 4. Python 同步调用

```python
from openagents import run_agent, run_agent_detailed, run_agent_with_dict

# 简单同步包装
result = run_agent("agent.json", agent_id="assistant", session_id="s1", input_text="你好")

# 详细结果（含 usage、artifacts、stop_reason）
result = run_agent_detailed("agent.json", agent_id="assistant", session_id="s1", input_text="你好")
print(result.usage.cost_usd, result.stop_reason)

# 内联配置（无需文件）
result = run_agent_with_dict(
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
    input_text="你好",
)
```

### 5. 流式输出

```python
from openagents import Runtime, RunRequest

async def stream_example() -> None:
    runtime = Runtime.from_config("agent.json")
    request = RunRequest(agent_id="assistant", session_id="s1", input_text="你好")
    async for chunk in runtime.run_stream(request):
        print(chunk.kind, chunk.data)
```

---

## 核心概念

### 运行时链路

```
调用方
  → Runtime facade（Runtime.run / run_stream）
    → Runtime plugin（DefaultRuntime）
      → Session manager + Event bus
      → Context assembler（本轮吃进什么上下文？）
      → Pattern.setup() → Memory.inject()
      → Pattern.execute() ↔ Tool calls（policy + executor）
      → Memory.writeback() → Context assembler.finalize()
      → RunResult
```

### 核心对象

| 对象 | 作用 |
|---|---|
| `RunRequest` | 单次 run 的输入（agent_id、session_id、input_text、context_hints、budget）|
| `RunResult[OutputT]` | run 的输出（output、usage、artifacts、stop_reason、error）|
| `RunContext[DepsT]` | 单次 run 内 tools 和 pattern 共享的状态载体 |
| `RunUsage` | token 统计 + `cost_usd` + cache 统计 |
| `RunBudget` | 限额：`max_cost_usd`、`max_tokens`、`max_turns`、`max_validation_retries` |
| `RunArtifact` | run 中产出的命名制品（携带 `metadata`）|
| `StopReason` | 类型化终止状态（`end_turn`、`budget_exhausted`、`error` 等）|

### Plugin Selector 规则

每个 plugin 通过以下两种方式之一加载：

```json
{"type": "react"}                              // builtin 或 decorator 注册名
{"impl": "myapp.patterns.custom.MyPattern"}   // Python dotted path
```

两者同时存在时，`impl` 优先。顶层 `runtime`、`session`、`events` 各需要且只能设置一个 selector；agent 级 plugin 至少需要 `type` 或 `impl` 中的一个。

---

## 内置组件

### Memory（记忆）

| 类型名 | 说明 |
|---|---|
| `buffer` | 完整对话历史（内存） |
| `window_buffer` | 滑动窗口，只保留最近 N 轮 |
| `markdown_memory` | 文件持久化长期记忆（MEMORY.md 索引 + 分节文件）|
| `mem0` | 基于 mem0ai 的跨 session 语义记忆（需 `[mem0]`）|
| `chain` | 多个 memory plugin 串联 |

### Pattern（推理循环）

| 类型名 | 说明 |
|---|---|
| `react` | ReAct：思考 → 行动 → 观察循环 |
| `plan_execute` | 先规划，再逐步执行 |
| `reflexion` | 自我反思与迭代改进 |

### Context Assembler（上下文组装）

| 类型名 | 说明 |
|---|---|
| `truncating` | 简单头部截断，保持 token 预算 |
| `head_tail` | 保留头部 + 尾部，丢弃中间 |
| `sliding_window` | 保留最近消息的滚动窗口 |
| `importance_weighted` | 基于重要性打分保留 |

### 内置 Tools

| 分类 | Tools |
|---|---|
| **搜索** | `builtin_search`、`tavily_search` |
| **文件** | `read_file`、`write_file`、`list_files`、`delete_file` |
| **文本** | `grep_files`、`ripgrep`、`json_parse`、`text_transform` |
| **HTTP** | `http_request`、`url_parse`、`url_build`、`query_param`、`host_lookup` |
| **系统** | `shell_exec`、`execute_command`、`get_env`、`set_env` |
| **时间** | `current_time`、`date_parse`、`date_diff` |
| **随机** | `random_int`、`random_choice`、`random_string`、`uuid` |
| **数学** | `calc`、`percentage`、`min_max` |
| **记忆** | `remember_preference` |
| **MCP** | `mcp`（桥接任意 MCP server，需 `[mcp]`）|

### App 基础设施

| Seam | 内置实现 | 说明 |
|---|---|---|
| `runtime` | `default` | DefaultRuntime 编排器 |
| `session` | `in_memory` | 进程内 session 存储 |
| `events` | `async` | 异步事件总线 |

---

## CLI 参考

安装 `cli` extra 获得完整 CLI 体验：

```bash
uv add "io-openagent-sdk[cli]"
```

| 命令 | 说明 |
|---|---|
| `openagents run <path>` | 执行单轮 |
| `openagents chat <path>` | 交互式多轮 REPL |
| `openagents dev <path>` | 配置变更时热重载 |
| `openagents validate <path>` | 验证 agent.json（不执行）|
| `openagents schema` | 输出完整 AppConfig JSON Schema |
| `openagents list-plugins` | 按 seam 列出所有注册 plugin |
| `openagents config show <path>` | 打印完全解析后的 AppConfig |
| `openagents init <name>` | 从模板脚手架新项目 |
| `openagents new plugin <seam> <name>` | 脚手架 plugin 骨架 + 测试存根 |
| `openagents replay <path>` | 重放持久化的 transcript |
| `openagents doctor` | 环境健康检查 |
| `openagents version` | 打印 SDK / Python / extras / plugin 数量 |
| `openagents completion <shell>` | 输出 shell 补全脚本 |

**退出码：** `0` 成功 · `1` 用法错误 · `2` 验证错误 · `3` 运行时错误

---

## 自定义插件

任意 seam 都可以通过实现接口 + 配置 `impl` 来扩展：

```python
# myapp/patterns/my_pattern.py
from openagents.interfaces import PatternPlugin, RunContext

class MyPattern(PatternPlugin):
    async def execute(self, context: RunContext) -> str:
        # 在这里实现你的推理循环
        return "done"
```

```json
{
  "pattern": {"impl": "myapp.patterns.my_pattern.MyPattern"}
}
```

也可以用 decorator 注册为命名 plugin：

```python
from openagents.decorators import pattern

@pattern("my_react")
class MyReact(PatternPlugin):
    ...
```

```json
{"pattern": {"type": "my_react"}}
```

完整指南：[Plugin 开发文档](docs/plugin-development.md)

---

## App-Defined Middle Protocol

SDK 提供 carrier，让你在 app 层定义产品协议，无需 fork kernel：

| Carrier | 用途 |
|---|---|
| `RunRequest.context_hints` | 向 context assembly 传入结构化提示 |
| `RunRequest.metadata` | 调用方元数据（task ID、trace ID 等）|
| `RunContext.state` | tools / pattern 共享的可变 per-run 状态 |
| `RunContext.scratch` | run 内临时暂存区 |
| `RunContext.assembly_metadata` | context assembler 向 pattern 发信号 |
| `RunArtifact.metadata` | 产出制品上的结构化元数据 |

---

## 示例

| 示例 | 说明 |
|---|---|
| [`examples/quickstart/`](examples/quickstart) | 最小 builtin-only 设置，第一次接触 kernel |
| [`examples/production_coding_agent/`](examples/production_coding_agent) | 生产级 coding agent：task packet、持久记忆、follow-up 恢复、delivery artifact |
| [`examples/pptx_generator/`](examples/pptx_generator) | 7 阶段交互式 PPT 生成器 CLI（`pptx-agent`），含 MCP + 多 pattern 管线 |

完整示例说明：[docs/examples.md](docs/examples.md)

---

## 文档

开发者文档在 [`docs`](docs/README.md) 目录下。

| 文档 | 内容 |
|---|---|
| [开发者指南](docs/developer-guide.md) | 架构边界、runtime 生命周期、状态 carrier |
| [仓库结构](docs/repository-layout.md) | 目录结构、文档拓扑、测试约定 |
| [Seam 与扩展点](docs/seams-and-extension-points.md) | 决策树：遇到什么问题用哪个 seam |
| [配置参考](docs/configuration.md) | JSON schema、selector 规则、builtin 名称 |
| [Plugin 开发](docs/plugin-development.md) | loader 机制、plugin 契约、测试模式 |
| [API 参考](docs/api-reference.md) | package exports、runtime 方法、协议对象 |
| [CLI 参考](docs/cli.md) | 完整 CLI 接口与退出码 |
| [示例说明](docs/examples.md) | 每个示例解决什么问题 |
| [0.2 → 0.3 迁移指南](docs/migration-0.2-to-0.3.md) | 升级指引 |

**新用户推荐阅读顺序：**
1. [仓库结构](docs/repository-layout.md)
2. [开发者指南](docs/developer-guide.md)
3. [Seam 与扩展点](docs/seams-and-extension-points.md)
4. [配置参考](docs/configuration.md)

---

## 开发

本项目使用 [`uv`](https://github.com/astral-sh/uv) 管理依赖。

```bash
# 克隆并安装所有开发依赖
git clone https://github.com/your-org/openagent-python-sdk
cd openagent-python-sdk
uv sync

# 运行完整测试套件
uv run pytest -q

# 运行单个测试
uv run pytest -q tests/unit/test_runtime_core.py::MyTest::test_case

# 覆盖率检查（floor: 92%）
uv run coverage run -m pytest && uv run coverage report

# Lint
uv run ruff check .
uv run ruff format --check .
```

**强制规则：** 在 `openagents` 包下增加、删除或修改代码时，**必须**在同一次改动中同步增加/更新/删除对应的测试。源码和测试套件是协同演化的。

---

## 0.4.0 新增

- **`shell_exec`** — 带 allowlist 的异步子进程工具，支持 cwd/env/timeout 控制
- **`tavily_search`** — 基于 REST 的 Tavily 搜索工具（读取 `TAVILY_API_KEY`）
- **`markdown_memory`** — 文件持久化长期记忆（MEMORY.md 索引 + 分节文件）
- **`remember_preference`** — 配套 agent 侧偏好捕获工具
- **`openagents.utils.env_doctor`** — 可复用的环境健康检查框架
- **`openagents.cli.wizard`** — Rich + questionary 向导组件，用于构建交互式多步 CLI
- **`examples/pptx_generator/`** — 生产级 7 阶段 PPT 生成器（`pptx-agent`）

完整变更日志：[CHANGELOG.md](CHANGELOG.md) | 迁移指南：[0.2 → 0.3](docs/migration-0.2-to-0.3.md)

---

## License

Apache License 2.0 — 详见 [LICENSE](LICENSE)。
