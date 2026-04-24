# Agent SDK 横向对比报告

**对比对象：**
- **OpenAgents Python SDK**（本项目，v0.4.0，Python）
- **Vercel AI SDK 6**（TypeScript，2025-12-22 GA）
- **OpenAI Agents SDK**（Python，v0.14.5，2026-04-23）

**调研日期：** 2026-04-24  
**调研方法：** 官方文档全文爬取 + 源码精读

---

## 目录

1. [定位与设计哲学](#1-定位与设计哲学)
2. [核心 Agent 抽象](#2-核心-agent-抽象)
3. [执行循环 & 停止控制](#3-执行循环--停止控制)
4. [工具系统](#4-工具系统)
5. [多 Agent / Handoffs](#5-多-agent--handoffs)
6. [内存系统](#6-内存系统)
7. [安全与 Guardrails](#7-安全与-guardrails)
8. [Human-in-the-Loop](#8-human-in-the-loop)
9. [可观测性与 Tracing](#9-可观测性与-tracing)
10. [会话与状态管理](#10-会话与状态管理)
11. [LLM Provider 支持](#11-llm-provider-支持)
12. [结构化输出](#12-结构化输出)
13. [流式处理](#13-流式处理)
14. [配置方式](#14-配置方式)
15. [生态集成](#15-生态集成)
16. [差距矩阵](#16-差距矩阵)
17. [结论与建议](#17-结论与建议)

---

## 1. 定位与设计哲学

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **语言** | Python 3.10+ | TypeScript | Python 3.9+ |
| **核心定位** | 单 Agent 运行时内核 | 全栈 AI 应用工具包（前端为重） | 轻量多 Agent 工作流框架 |
| **设计原则** | Kernel Protocol 极度稳定；产品语义不进内核 | "模型 + 工具 + 循环"最小原语；端到端类型安全 | "足够多的功能值得用，足够少的原语便于学" |
| **多 Agent** | **刻意不做**（单 agent kernel 设计） | 通过 subagent-as-tool 实现 | 一等公民 Handoffs 原语 |
| **配置范式** | JSON config-as-code（无代码可运行） | TypeScript 编程式 API | Python 编程式 API |
| **核心关注点** | 可插拔性、内核稳定性、可控性 | 前端集成、类型安全、开发体验 | 多 Agent 路由、安全、可观测性 |

**核心设计差异**：

- OpenAgents 是**内核（kernel）**：三层分离（Protocol / Seam / App），产品语义驻留应用层，内核保持最小
- Vercel AI SDK 是**全栈工具包**：LLM 能力直达 React UI，`createAgentUIStreamResponse` → `useChat` 闭环
- OpenAI Agents SDK 是**多 Agent 框架**：Handoff、Guardrail、Tracing 是一等公民，生产级开箱即用

---

## 2. 核心 Agent 抽象

### 2.1 OpenAgents — `RunRequest` + `RunContext` + `PatternPlugin`

Agent 无单独类，通过 JSON 配置定义，运行时由 `Runtime.run_detailed(request)` 驱动。

```python
# 配置示例（config.json 片段）
{
  "agents": [{
    "id": "my_agent",
    "llm": { "provider": "anthropic", "model": "claude-sonnet-4-6" },
    "pattern": { "type": "react", "config": { "max_steps": 16 } },
    "memory": { "type": "buffer" },
    "tools": [{ "type": "http_request" }]
  }]
}

# 运行
runtime = Runtime.from_config("config.json")
result: RunResult = await runtime.run_detailed(RunRequest(
    agent_id="my_agent",
    session_id="s1",
    input_text="What is 2+2?",
    budget=RunBudget(max_steps=10, max_cost_usd=0.05),
))
```

**RunRequest 完整字段：**
```python
class RunRequest(BaseModel):
    agent_id: str
    session_id: str
    input_text: str
    run_id: str                          # 自动生成 UUID
    parent_run_id: str | None = None
    metadata: dict[str, Any] = {}
    context_hints: dict[str, Any] = {}   # 向 assembler 传递 app-level 元数据
    budget: RunBudget | None = None
    deps: Any = None                     # 类型安全的应用依赖注入
    output_type: type[BaseModel] | None  # 结构化输出目标类型
    durable: bool = False                # 启用自动检查点
    resume_from_checkpoint: str | None  # 从检查点恢复
```

**RunContext 完整字段（注入进 tool/pattern/executor）：**
```python
class RunContext(BaseModel, Generic[DepsT]):
    agent_id, session_id, run_id, input_text
    deps: DepsT | None                   # 类型安全依赖
    state: dict[str, Any]                # 跨 step 状态
    tools: dict[str, Any]                # 绑定工具
    llm_client                           # LLM 客户端
    llm_options
    event_bus                            # 事件总线
    memory_view: dict[str, Any]          # 注入的记忆
    tool_results: list[dict]             # 工具调用历史
    scratch: dict[str, Any]              # Pattern 临时状态
    system_prompt_fragments: list[str]   # 动态 system prompt 片段
    transcript: list[dict]               # 会话转录
    session_artifacts: list[SessionArtifact]
    assembly_metadata: dict[str, Any]
    run_request: RunRequest | None
    tool_executor
    usage: RunUsage | None
    artifacts: list[RunArtifact]
```

### 2.2 Vercel AI SDK 6 — `ToolLoopAgent`

```typescript
const agent = new ToolLoopAgent({
    model: anthropic("claude-sonnet-4-5"),
    instructions: "You are a helpful assistant.",
    tools: { search: searchTool, calculate: calcTool },
    stopWhen: stepCountIs(20),
    activeTools: ['search'],              // 运行时过滤工具
    prepareStep: async ({ stepNumber }) => ({ /* 动态调整工具/参数 */ }),
    onStepFinish: async ({ stepNumber, text, toolCalls, usage }) => {},
    temperature: 0.7,
    maxOutputTokens: 4096,
});
```

没有 `RunContext` 概念：状态通过工具函数的 **闭包** 传递，类型安全由泛型推导保证。

### 2.3 OpenAI Agents SDK — `Agent`

```python
agent = Agent(
    name="Research Agent",
    instructions="You are a research assistant.",  # 或 async def(ctx, agent) -> str
    model="gpt-5",
    tools=[get_weather, search_web],
    handoffs=[billing_agent, refund_agent],
    output_type=ReportOutput,              # Pydantic BaseModel
    model_settings=ModelSettings(temperature=0.7),
    input_guardrails=[safety_check],
    output_guardrails=[quality_check],
    mcp_servers=[my_mcp_server],
    hooks=MyAgentHooks(),
    tool_use_behavior="run_llm_again",    # 工具调用后行为
    reset_tool_choice=True,
)

result = await Runner.run(agent, "What is the temperature in SF?")
print(result.final_output)
print(result.last_agent.name)  # 最终执行的 agent（handoff 后可能变化）
```

`RunContextWrapper[T]` 是类型安全的依赖注入容器，流经 tools/hooks/guardrails/instructions，但**不发送给 LLM**。

---

## 3. 执行循环 & 停止控制

### 3.1 循环模型对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **循环名称** | Pattern（React/PlanExecute/Reflexion） | Tool Loop | Agent Loop |
| **一次循环单元** | 1 个 step（LLM call + tool dispatch） | 1 个 step | 1 个 turn（1 LLM call + 所有 tool calls） |
| **默认上限** | `max_steps: 16`（ReAct 默认） | `stepCountIs(20)` | `max_turns=10` |
| **停止条件 API** | `RunBudget.max_steps`（整数） | `stopWhen` 声明式 API | `max_turns`（整数） |
| **成本控制** | `RunBudget.max_cost_usd`（内置） | 需自定义 stopWhen | 无内置成本控制 |
| **时间控制** | `RunBudget.max_duration_ms` | `timeout` 参数 | 无内置 |
| **工具调用上限** | `RunBudget.max_tool_calls` | 无内置 | 无内置 |

### 3.2 Vercel AI SDK 6 — 声明式 stopWhen

```typescript
import { stepCountIs, hasToolCall, isLoopFinished, StopCondition } from 'ai';

stopWhen: stepCountIs(50)                    // 达到 N 步停止
stopWhen: hasToolCall('done')               // 特定工具被调用后停止
stopWhen: isLoopFinished()                  // 模型自然停止（去除步数上限）
stopWhen: [stepCountIs(20), hasToolCall('done')]  // 任一满足即停

// 自定义（基于 token 成本）
const budgetExceeded: StopCondition = ({ steps }) => {
    const cost = steps.reduce((acc, s) => acc + (s.usage?.inputTokens ?? 0) * 0.01 / 1000, 0);
    return cost > 0.50;  // $0.50 预算上限
};
```

仅在"上一步包含工具结果"时才触发 stopWhen 评估。

### 3.3 OpenAI Agents SDK — `tool_use_behavior`

```python
# 四种工具调用后行为
tool_use_behavior="run_llm_again"          # 默认：工具结果喂给 LLM 继续
tool_use_behavior="stop_on_first_tool"     # 首次工具调用结果作为最终输出
tool_use_behavior=StopAtTools(["get_weather"])  # 指定工具调用时停止
tool_use_behavior=custom_fn               # (ctx, tool_results) -> ToolsToFinalOutputResult
```

超出 max_turns 时抛 `MaxTurnsExceeded`，可通过 `RunErrorHandlers` 优雅处理：
```python
handlers = RunErrorHandlers(max_turns=lambda ctx, exc: "已达执行上限，请缩短任务。")
```

### 3.4 OpenAgents 独有特性

```python
# 全维度预算控制
budget = RunBudget(
    max_steps=16,
    max_duration_ms=60_000,
    max_tool_calls=50,
    max_cost_usd=0.10,
    max_validation_retries=3,
    max_resume_attempts=3,
)
```

Anthropic 的 prompt caching 价格也纳入成本计算（`cache_read_input_tokens` 单独计价），是三者中**成本感知最完整**的。

---

## 4. 工具系统

### 4.1 工具接口对比

| 维度 | OpenAgents `ToolPlugin` | Vercel `tool()` | OpenAI `@function_tool` |
|------|------------------------|-----------------|------------------------|
| **Schema 定义** | `schema()` 方法（手写 JSON Schema） | Zod schema | Pydantic + docstring 自动解析 |
| **执行方法** | `invoke(params, context)` | `execute(input, options)` | 函数体直接为 execute |
| **流式工具** | `invoke_stream()` ✓ | `async function*` 作为 execute ✓ | 不支持 |
| **批量执行** | `invoke_batch()` ✓（并发优化） | 无内置 | 无内置 |
| **后台任务** | `invoke_background()` + `poll_job()` + `cancel_job()` ✓ | 无 | 无 |
| **Pre/Post Hook** | `before_invoke()` / `after_invoke()` ✓ | `experimental_onToolCallStart/Finish` | hooks 系统 |
| **需要审批** | `requires_approval()` 方法 ✓ | `needsApproval` 属性 ✓ | `needs_approval=True` ✓ |
| **Fallback** | `fallback(error, params, ctx)` ✓ | 无 | 无 |
| **上下文控制** | 通过 `RunContext` | `toModelOutput`（精确控制 LLM 看到什么） ✓ | 无 |
| **超时控制** | `ToolExecutionSpec.default_timeout_ms` ✓ | `timeout` 参数 | `@function_tool(timeout=30)` ✓ |
| **延迟加载** | 无 | 无 | `defer_loading=True` ✓（`ToolSearchTool`） |

### 4.2 内置工具库

| SDK | 内置工具数量 | 代表性工具 |
|-----|-------------|-----------|
| **OpenAgents** | **30+**（最丰富） | File ops、HTTP、Math、Time、Network、Text、Shell、Tavily Search、MCP bridge、Random |
| **Vercel AI SDK 6** | Provider Tools（非内置） | Anthropic: memory/code_execution；OpenAI: shell/apply_patch；Google: maps/rag；xAI: web_search |
| **OpenAI Agents SDK** | ~8 hosted + 4 local | WebSearch、FileSearch、CodeInterpreter、ImageGen、HostedMCP、Shell、Computer（GUI）、ApplyPatch、Codex（实验） |

**OpenAgents 独有：**
- `invoke_background()` / `poll_job()` / `cancel_job()` — 长运行后台任务框架
- `invoke_batch()` — 批量并发工具调用优化
- MCP bridge 工具（本地集成）

**OpenAI Agents SDK 独有：**
- `ComputerTool` — GUI 自动化（截图、点击、键入）
- `ShellTool` — 安全沙盒 shell 执行
- `ApplyPatchTool` — diff 格式文件修改
- `SandboxAgent`（v0.14.0）— 隔离 workspace 执行（Modal/E2B/Daytona）

**Vercel AI SDK 6 独有：**
- `toModelOutput` — 解耦"用户看到的"和"LLM 看到的"，精确控制上下文消耗

### 4.3 权限与执行策略

| SDK | 机制 |
|-----|------|
| OpenAgents | `ToolExecutorPlugin.evaluate_policy()` 返回 `PolicyDecision(allowed, reason)`；内置 `FilesystemAwareExecutor`（root 目录限制） |
| Vercel AI SDK 6 | 无内置权限系统，通过 needsApproval 中断 |
| OpenAI Agents SDK | 无执行策略层；依赖 Guardrails 过滤；`SandboxRunConfig` 提供沙盒隔离 |

---

## 5. 多 Agent / Handoffs

这是三者**差距最大**的维度。

### 5.1 OpenAgents — 刻意不支持，设计哲学

```
单 Agent 内核 + App 层编排
```

OpenAgents 只负责运行单个 Agent，多 Agent 协作由应用层实现。`parent_run_id` 字段支持追踪父子 run 关系，但无调度原语。

### 5.2 Vercel AI SDK 6 — Subagent-as-Tool

子 Agent 被包装为普通工具，父 Agent 保持控制权：

```typescript
// 子 Agent 作为工具（流式传递进度到 UI）
const researchTool = tool({
    inputSchema: z.object({ task: z.string() }),
    execute: async function* ({ task }, { abortSignal }) {
        const result = await researchSubagent.stream({ prompt: task, abortSignal });
        for await (const message of readUIMessageStream({ stream: result.toUIMessageStream() })) {
            yield message;  // 每次 yield 是累积的 UIMessage，UI 实时更新
        }
    },
    toModelOutput: ({ output: message }) => ({
        type: 'text',
        value: message?.parts.findLast(p => p.type === 'text')?.text ?? '',
        // 主 Agent 只看摘要，不消耗子 Agent 的全部 token
    }),
});
```

**特点：**
- 父 Agent 始终保持控制权（Manager-Orchestrator 模式）
- 子 Agent 上下文完全隔离，不继承父 Agent 历史
- 流式传递中间进度到 UI（`async function*` + `readUIMessageStream`）
- `toModelOutput` 上下文卸载：子 Agent 可消耗数万 token，主 Agent 只看摘要
- **限制：** 子 Agent 内部工具不能使用 `needsApproval`

### 5.3 OpenAI Agents SDK — 一等公民 Handoffs

Handoffs 是控制权的**完全转移**，对比 as_tool 的"工具调用后返回"：

```python
# Handoff = 控制权完全转移给目标 Agent
handoff(
    agent=refund_agent,
    tool_name_override="escalate_to_refunds",  # LLM 看到的 tool name
    on_handoff=async def(ctx, data: EscalationData): alert_team(data.reason),
    input_type=EscalationData,                  # LLM 生成的结构化元数据
    input_filter=handoff_filters.remove_all_tools,  # 过滤传给下一 Agent 的历史
    is_enabled=lambda ctx, agent: ctx.context.tier == "premium",  # 动态启用
)

# as_tool = Manager-Orchestrator，控制权不转移
subagent.as_tool(
    tool_name="research",
    tool_description="Research a topic in depth",
)
```

**Handoff 的底层实现：**
- 表示为 LLM 的 function call（`transfer_to_<agent_name>`）
- `input_type` 设置时，Pydantic schema 暴露为 tool parameters（LLM 生成元数据）
- `input_filter` 接收 `HandoffInputData`，可过滤/修改传给新 Agent 的历史
- `nest_handoff_history` 控制是否将 handoff 前历史以嵌套形式传递
- `RunResult.last_agent` 记录最终执行的 Agent

**Handoff vs as_tool 对比：**

| 维度 | `handoff()` | `agent.as_tool()` |
|------|-------------|-------------------|
| 控制权 | **完全转移**给新 Agent | 父 Agent 保持控制 |
| 历史传递 | 完整对话历史默认传递 | 独立上下文，只返回结果 |
| 返回 | 不回到原 Agent | 结果作为 tool output 返回 |
| 适用场景 | 专业 Agent 路由（客服分流） | 子任务委托（研究、计算） |

---

## 6. 内存系统

### 6.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **架构** | 插件 seam（inject / writeback / retrieve） | 无内置，通过第三方集成 | 无内置，通过 session 持久化历史 |
| **内置实现** | Buffer、WindowBuffer、Chain、Markdown、Mem0 | 无 | 无（Session 管历史，非语义记忆） |
| **Provider-Defined** | 无 | Anthropic memory tool（`/memories` 文件） | 无 |
| **第三方集成** | Mem0（optional extra） | Mem0、Letta、Supermemory | 无直接集成 |
| **跨会话持久化** | Markdown Memory（本地文件）、Mem0（云） | Mem0/Letta/Supermemory | Session backends（SQLite/Redis 等） |
| **语义搜索** | `retrieve(query, context)` ✓ | Mem0/Supermemory 提供 | 无 |

### 6.2 OpenAgents 内存插件接口

```python
class MemoryPlugin:
    async def inject(self, context: RunContext) -> None:
        # 注入 context.memory_view（运行前）
        # Pattern 可将 memory_view 合并进 system prompt

    async def writeback(self, context: RunContext) -> None:
        # 保存本次交互（运行后）

    async def retrieve(self, query: str, context: RunContext) -> list[dict]:
        # 语义搜索相关记忆（可选）

    async def close(self) -> None: ...
```

### 6.3 Vercel AI SDK 6 — Provider-Defined Memory（Anthropic 专属）

```typescript
const memory = anthropic.tools.memory_20250818({
    execute: async (action) => {
        // action.command: 'view' | 'create' | 'str_replace' | 'insert' | 'delete' | 'rename'
        // 映射到文件系统或数据库
        return `操作结果`;
    },
});
// Claude 经过专项训练调用此工具，管理 /memories 目录下的记忆文件
```

**局限：** Provider 锁定，仅 Anthropic 支持。

---

## 7. 安全与 Guardrails

这是 OpenAgents 与 OpenAI Agents SDK 差距**最明显**的维度之一。

### 7.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **内置 Guardrail 系统** | **无**（需自定义） | **无** | **三级内置** ✓ |
| **Input Guardrail** | 无 | 无 | `@input_guardrail`，与 Agent **并行**执行 |
| **Output Guardrail** | 无 | 无 | `@output_guardrail`，Agent 完成后执行 |
| **Tool Guardrail** | `evaluate_policy()` 方法（权限层） | 无 | `@tool_input/output_guardrail` |
| **Tripwire（快速失败）** | 无 | 无 | `tripwire_triggered=True` 立即抛异常 ✓ |
| **并行 vs 阻塞** | N/A | N/A | `run_in_parallel=True/False` 可选 |

### 7.2 OpenAI Agents SDK — 三级 Guardrail 详解

```python
# 1. 输入守卫（与 Agent 并行，最小延迟）
@input_guardrail(run_in_parallel=True)
async def hate_speech_check(ctx, agent, input) -> GuardrailFunctionOutput:
    is_violation = await classify_hate_speech(input)
    return GuardrailFunctionOutput(
        output_info={"score": is_violation},
        tripwire_triggered=is_violation,  # True → InputGuardrailTripwireTriggered 异常
    )

# 2. 输出守卫（Agent 完成后）
@output_guardrail
async def pii_check(ctx, agent, output) -> GuardrailFunctionOutput:
    has_pii = await detect_pii(str(output))
    return GuardrailFunctionOutput(output_info=has_pii, tripwire_triggered=has_pii)

# 3. 工具调用守卫（每次 function_tool 调用前/后）
@tool_input_guardrail
def block_secrets(data) -> ToolGuardrailFunctionOutput:
    if "sk-" in json.dumps(data.context.tool_arguments or {}):
        return ToolGuardrailFunctionOutput.reject_content("移除 secrets 后再调用")
    return ToolGuardrailFunctionOutput.allow()
```

### 7.3 OpenAgents 的对应能力

OpenAgents 通过 `evaluate_policy()` 实现工具级别权限，但：
- 无输入/输出层面的安全校验钩子
- 无 tripwire 快速失败机制
- 无开箱即用的内容安全实现

---

## 8. Human-in-the-Loop

### 8.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **工具审批** | `ToolPlugin.requires_approval()` ✓ | `needsApproval` 属性 ✓ | `needs_approval=True` ✓ |
| **中断状态持久化** | `RunRequest.durable + resume_from_checkpoint` | 无内置持久化 | `RunState` JSON 序列化 ✓ |
| **跨进程恢复** | 通过 checkpoint 机制（v0.4.0，成熟度待验证） | 无 | `RunState.to_json()` / `from_json()` ✓ |
| **长时间暂停** | 不明确支持 | 无 | 支持（序列化为 JSON 存储，数天后恢复） |
| **审批 API** | 无标准 approve/reject API | `addToolApprovalResponse` | `state.approve()` / `state.reject()` ✓ |
| **Always-approve** | 无 | 无 | `approve(interruption, always_approve=True)` ✓ |

### 8.2 OpenAI Agents SDK — RunState 序列化

```python
# 跨进程恢复（可暂停数天等待审批）
result = await Runner.run(agent, "Delete the latest backup file")

# 进程 A：序列化保存
state = result.to_state()
STATE_PATH.write_text(state.to_string())  # 存入 DB / Redis / 文件

# 进程 B：反序列化恢复
state = await RunState.from_string(agent, STATE_PATH.read_text())
for interruption in result.interruptions:
    state.approve(interruption)           # 或 state.reject(interruption, "拒绝原因")
result = await Runner.run(agent, state)   # 继续运行
```

### 8.3 Vercel AI SDK 6 — needsApproval + UI 集成

```typescript
// 后端：声明需要审批
const dangerousTool = tool({
    needsApproval: async ({ path }) => path.startsWith("/system"),
    execute: async ({ path }) => deleteFile(path),
});

// 前端：React 组件处理审批
function ToolView({ invocation, addToolApprovalResponse }) {
    if (invocation.state === 'approval-requested') {
        return <>
            <button onClick={() => addToolApprovalResponse({
                id: invocation.approval.id, approved: true
            })}>批准</button>
            <button onClick={() => addToolApprovalResponse({
                id: invocation.approval.id, approved: false
            })}>拒绝</button>
        </>;
    }
}
```

**优势：** UI 集成优雅，类型安全。  
**劣势：** 无跨进程状态持久化，刷新页面后状态丢失。

---

## 9. 可观测性与 Tracing

### 9.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **事件系统** | 细粒度事件 Bus（tool.called, llm.called 等）✓ | `onStepFinish` 回调 | `hooks` 系统（run/agent/tool 级别） |
| **自动全链路 Tracing** | 无（需手动通过事件 + OTel 拼接） | DevTools（仅本地开发） | **自动全链路**，默认上报 OpenAI ✓ |
| **生产级 Tracing** | OTel bridge（optional extra）+ Langfuse + Phoenix | DevTools（禁止生产） | 26+ 平台 export，开箱即用 ✓ |
| **Trace 粒度** | 事件级别（需自行聚合为 spans） | Step 级别 | Span 级别（9种自动 span）✓ |
| **敏感数据控制** | 无专门 API | 无 | `trace_include_sensitive_data=False` ✓ |
| **成本追踪** | `RunUsage.cost_usd`（含 cache 成本）✓ | `onStepFinish.usage` | 无内置成本计算 |

### 9.2 OpenAgents 事件系统

```python
# 内置事件分类
tool.called / tool.succeeded / tool.failed / tool.cancelled / tool.timeout
llm.called / llm.succeeded / llm.failed
memory.injected / memory.writeback
pattern.started / pattern.step_started / pattern.step_finished
usage.updated
session.acquired / session.released
```

事件通过 `EventBusPlugin` 异步派发，支持：
- `AsyncEventBus`（内存，默认）
- `FileLoggingEventBus`（文件持久化）
- `OtelBridgeEventBus`（OpenTelemetry 导出）
- `RichConsoleEventBus`（彩色控制台，调试用）

### 9.3 OpenAI Agents SDK — 自动全链路 Tracing

```python
# 9 种自动 Span
trace()              # 整个 Runner.run() 调用
agent_span()         # 每个 Agent 执行
generation_span()    # 每次 LLM 生成（含 input/output）
function_span()      # 每次 function_tool 调用（含 input/output）
guardrail_span()     # 每次 guardrail 执行
handoff_span()       # 每次 handoff
transcription_span() # STT（语音转文字）
speech_span()        # TTS（文字转语音）
custom_span()        # 自定义

# 26+ 外部平台（按字母序）
# Agenta, AgentOps, Arize-Phoenix, Asqav, Braintrust, Comet Opik,
# Datadog, Future AGI, Galileo, HoneyHive, Langfuse, LangDB AI,
# LangSmith, Langtrace, Maxim AI, MLflow(OSS), MLflow(Databricks),
# Okahu-Monocle, Portkey AI, PostHog, PromptLayer, Pydantic Logfire,
# Respan, Scorecard, Traccia, Weights & Biases

# 自定义 Processor
from agents import add_trace_processor
add_trace_processor(my_datadog_exporter)  # 追加
set_trace_processors([my_processor])       # 替换

# 关闭 tracing
RunConfig(tracing_disabled=True)
```

---

## 10. 会话与状态管理

### 10.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **内置 Session 后端数** | **3**（InMemory、JsonlFile、SQLite） | 无内置（历史存于 `messages` 数组） | **9+** ✓ |
| **Redis** | 无（需自实现） | 无 | `RedisSession` ✓ |
| **PostgreSQL/SQLAlchemy** | 无 | 无 | `SQLAlchemySession` ✓ |
| **MongoDB** | 无 | 无 | `MongoDBSession` ✓（v0.14.2） |
| **云原生（Dapr）** | 无 | 无 | `DaprSession`（含 TTL）✓ |
| **会话加密** | 无 | 无 | `EncryptedSession`（透明 AES 加密）✓ |
| **会话分支** | 无 | 无 | `AdvancedSQLiteSession.create_branch_from_turn()` ✓ |
| **使用量分析** | `RunUsage`（per run） | `onStepFinish.usage` | `AdvancedSQLiteSession.store_run_usage()` ✓ |
| **会话锁** | 进程级互斥锁（`session_manager.acquire_lock`） | 无 | 无 |
| **Server-Managed 历史** | 无 | 无 | `OpenAIConversationsSession`（OpenAI 服务端管理）✓ |
| **长对话压缩** | `context_assembler`（4种截断策略）✓ | 无内置 | `OpenAIResponsesCompactionSession` ✓ |

### 10.2 OpenAgents Session 的独特优势

**进程级会话锁：** 同一 session_id 的 run 串行化执行，防止并发修改：
```python
# 内部实现
async with session_manager.session(session_id) as state:
    # 持有锁期间，同一 session 的其他请求会排队
    ...
```

**4种上下文压缩策略：**
```python
{"context_assembler": {"type": "truncating"}}          # 简单截断
{"context_assembler": {"type": "head_tail"}}            # 保留开头+结尾
{"context_assembler": {"type": "sliding_window"}}       # 滑动窗口
{"context_assembler": {"type": "importance_weighted"}}  # 权重排序（需自定义打分）
```

---

## 11. LLM Provider 支持

### 11.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **架构** | httpx 直连（Anthropic、OpenAI-compatible）+ LiteLLM 桥 | AI Gateway 统一抽象层 | 直连 OpenAI Responses/Chat API + litellm 桥 |
| **Anthropic** | ✓ 原生（含 prompt caching 精确计价） | ✓ | ✓（通过兼容层，部分特性缺失） |
| **OpenAI** | ✓（openai_compatible）| ✓ | ✓ 原生（最完整） |
| **Google Gemini** | 通过 LiteLLM | ✓ 原生 | 通过兼容层 |
| **xAI Grok** | 通过 LiteLLM | ✓ 原生 | 通过兼容层 |
| **Azure OpenAI** | ✓（openai_compatible + base_url）| ✓ | ✓ |
| **Bedrock/Vertex** | 通过 LiteLLM | ✓ 原生 | 通过 LiteLLM |
| **100+ 其他** | 通过 LiteLLM ✓ | 通过 AI Gateway ✓ | 通过 LiteLLM ✓ |
| **Prompt Caching** | ✓ 完整支持（Anthropic cache 读写分别计价）| ✓ | 无内置成本感知 |
| **Realtime API** | 无 | 无 | ✓（gpt-realtime-1.5 Realtime Agent）|

### 11.2 OpenAgents LLM 客户端键值缓存

```python
# 两层缓存，热重载时的精确失效
Agent 插件缓存键：(session_id, agent_id)    # 包括 pattern/memory/tool 实例
LLM 客户端缓存键：agent_id                  # httpx client 复用

# Runtime.reload() 行为
# - 重解析 config
# - 使 LLM 客户端缓存失效（变更的 agent）
# - 不重加载顶层 runtime/session/events（持久化对象）
```

---

## 12. 结构化输出

### 12.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **Schema 定义** | Pydantic BaseModel（`RunRequest.output_type`）| Zod / Standard JSON Schema | Pydantic BaseModel（`Agent.output_type`）|
| **验证重试** | `RunBudget.max_validation_retries`（内置重试）✓ | 无内置 | 无内置 |
| **API 粒度** | Run 级别（单次 run 输出 schema） | Function level（`generateObject` → `output=Output.object(z.object(...))`）| Agent 级别（所有 run 共享） |
| **部分结构化** | 无 | `Output.choice()` / `Output.json()` / `Output.text()` | 无 |
| **验证错误反馈** | `OutputValidationError` 含 `last_validation_error` | 无 | 无 |

### 12.2 OpenAgents 验证失败重试

```python
# 内部逻辑：输出验证失败后，把错误反馈给 LLM 重试
class OutputValidationError(ExecutionError):
    output_type: type[BaseModel]
    attempts: int              # 已尝试次数
    last_validation_error: str # 最后一次 Pydantic 验证错误（反馈给 LLM）
```

---

## 13. 流式处理

### 13.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **文本流** | 无内置（需通过事件系统组装）| `agent.stream()` + `textStream` ✓ | `Runner.run_streamed()` + `stream_events()` ✓ |
| **事件流** | `EventBusPlugin`（订阅模型）✓ | `onStepFinish`（push 模型） | `stream_events()` ✓ |
| **Stream API** | `docs/stream-api.md` 记录了 `RunStreamChunk` | UI Stream（`createAgentUIStreamResponse`）✓ | RunItemStreamEvent / RawResponsesStreamEvent |
| **Subagent 流** | 无 | `async function*` execute ✓ 实时传递进度到 UI | 无 |
| **中断后流式恢复** | 不明确 | 无 | `Runner.run_streamed(agent, state)` ✓ |

---

## 14. 配置方式

### 14.1 对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **范式** | **JSON config-as-code**（无代码可运行）✓ | TypeScript 编程式 | Python 编程式 |
| **热重载** | `Runtime.reload()`（不重启切换策略）✓ | 无 | 无 |
| **版本控制** | 天然友好（JSON 文件）✓ | 代码即配置 | 代码即配置 |
| **动态配置** | `context_hints`（runtime 提示）+ `deps`（依赖注入）| `prepareStep` 动态调整 | `instructions` async 函数、`is_enabled` 动态启用 handoff |
| **Multi-Agent 配置** | 多个 agent 定义在同一 JSON | 多个 `ToolLoopAgent` 实例 | 多个 `Agent` 实例 + handoffs 连接 |

### 14.2 OpenAgents 配置示例（生产级）

```json
{
  "runtime": { "type": "default" },
  "session": { "type": "sqlite", "config": { "db_path": "data/sessions.db" } },
  "events": { "type": "file_logging", "config": { "log_path": "logs/events.jsonl" } },
  "agents": [{
    "id": "research_agent",
    "llm": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-6",
      "api_key": "${ANTHROPIC_API_KEY}",
      "config": { "rate_per_mtok": { "input": 3.0, "output": 15.0 } }
    },
    "pattern": { "type": "react", "config": { "max_steps": 20 } },
    "memory": { "type": "markdown_memory", "config": { "storage_dir": "data/memory" } },
    "context_assembler": { "type": "head_tail" },
    "tool_executor": { "type": "retry", "config": { "max_retries": 3 } },
    "tools": [
      { "type": "http_request" },
      { "type": "tavily_search", "config": { "api_key": "${TAVILY_API_KEY}" } }
    ]
  }]
}
```

---

## 15. 生态集成

### 15.1 三者对比

| 维度 | OpenAgents | Vercel AI SDK 6 | OpenAI Agents SDK |
|------|-----------|-----------------|-------------------|
| **MCP 支持** | ✓（MCP tool bridge + MCP coordinator runtime）| ✓（稳定版，含 OAuth/Resources/Prompts）| ✓（HostedMCPTool）|
| **前端集成** | 无 | **一等公民**（React hooks、RSC、Next.js）✓ | 无 |
| **Durable Execution** | 部分（v0.4.0 durable=True + checkpoint）| `DurableAgent`（Workflow DevKit）| ✓ Temporal/Restate/DBOS 官方集成 |
| **Realtime/Voice** | 无 | 无 | ✓ Realtime Agent（gpt-realtime-1.5）|
| **GUI 自动化** | 无 | 无 | ✓ `ComputerTool` |
| **Sandbox 执行** | 无 | 无 | ✓ `SandboxAgent`（Modal/E2B/Daytona）v0.14.0 |
| **Langfuse** | ✓（optional extra）| 通过 @ai-sdk/langfuse | ✓（25+ 平台中包含） |
| **OpenTelemetry** | ✓（OtelBridgeEventBus）| 通过 middleware | ✓（原生 span 结构） |
| **LangChain** | 无 | ✓ LangChain Adapter（SDK 6 重写）| 可通过 langchain 工具 |

---

## 16. 差距矩阵

以 **OpenAgents** 为基准，评估其与外部 SDK 的差距（★ = 已支持，◐ = 部分支持，✗ = 缺失）：

| 特性类别 | OpenAgents 现状 | Vercel AI SDK 6 | OpenAI Agents SDK |
|---------|----------------|-----------------|-------------------|
| **单 Agent 执行** | ★ 完整 | ★ 完整 | ★ 完整 |
| **多 Agent Handoffs** | ✗（刻意）| ◐ subagent-as-tool | ★ 完整原语 |
| **Guardrails（三级）** | ✗（无）| ✗（无）| ★ 完整 |
| **Tripwire 快速失败** | ✗ | ✗ | ★ |
| **工具审批** | ◐（requires_approval 方法）| ★（needsApproval）| ★（needs_approval）|
| **HITL 跨进程持久化** | ◐（durable + checkpoint，成熟度待验证）| ✗ | ★（RunState JSON 序列化）|
| **Session 后端丰富度** | ◐（3种：InMemory/JsonlFile/SQLite）| ✗ | ★（9+种）|
| **Redis/PostgreSQL Session** | ✗ | ✗ | ★ |
| **会话加密** | ✗ | ✗ | ★（EncryptedSession）|
| **生产级自动 Tracing** | ◐（事件 + OTel，需自行拼接）| ✗（DevTools 仅开发）| ★（自动 9 种 span）|
| **26+ 平台 Trace Export** | ◐（Langfuse + Phoenix，需 extra）| ◐（部分通过 middleware）| ★ |
| **成本追踪** | ★（最完整，含 cache）| ◐（token 计数）| ✗ |
| **声明式停止条件** | ✗（整数 max_steps）| ★（stopWhen API）| ✗（整数 max_turns）|
| **toModelOutput 上下文控制** | ✗ | ★ | ✗ |
| **流式 Subagent 进度** | ✗ | ★（async function*）| ✗ |
| **前端 UI 集成** | ✗ | ★（useChat / RSC）| ✗ |
| **工具批量执行** | ★（invoke_batch）| ✗ | ✗ |
| **工具后台任务** | ★（invoke_background + poll + cancel）| ✗ | ✗ |
| **ComputerTool（GUI 自动化）** | ✗ | ✗ | ★ |
| **Sandbox 执行** | ✗ | ✗ | ★（v0.14.0）|
| **Realtime/Voice Agent** | ✗ | ✗ | ★ |
| **Durable Execution（正式）** | ◐（v0.4.0 部分）| ◐（Workflow DevKit）| ★（Temporal/Restate/DBOS）|
| **Config-as-Code（JSON）** | ★（独有）| ✗ | ✗ |
| **热重载** | ★（独有）| ✗ | ✗ |
| **预算控制（成本/时间/步数/工具数）** | ★（最完整）| ◐ | ◐ |
| **上下文压缩策略（4种）** | ★（独有）| ✗ | ◐（自动压缩 session）|
| **进程级会话锁** | ★（独有）| ✗ | ✗ |
| **Dynamic Instructions** | ◐（通过 skills/context_hints）| ◐（prepareStep）| ★（async def）|
| **工具 Fallback** | ★（fallback 方法）| ✗ | ✗ |
| **Output 验证重试** | ★（max_validation_retries）| ✗ | ✗ |

---

## 17. 结论与建议

### 17.1 OpenAgents 的核心差异化优势（应持续强化）

1. **Config-as-Code + 热重载**：无代码可运行、热重载切换策略，竞争者均无此能力
2. **精确成本预算**：`RunBudget.max_cost_usd`、Anthropic cache 精确计价，是三者中成本感知最完整的
3. **工具扩展性**：`invoke_background` / `poll_job` / `invoke_batch` / `before_invoke` / `after_invoke` / `fallback` 等工具生命周期钩子，是三者中**工具接口最丰富**的
4. **上下文压缩策略**：4 种 `context_assembler` 实现（truncating/head_tail/sliding_window/importance_weighted），竞争者仅有有限支持
5. **进程级会话锁**：保证同一 session 串行执行，防并发竞争

### 17.2 OpenAgents 的关键差距（按优先级排序）

**P0（生产部署阻塞项）：**

| 差距 | 建议 |
|------|------|
| **Session 后端单薄**（无 Redis/PostgreSQL）| 至少增加 `RedisSession`，生产环境多实例部署必需 |
| **Guardrails 缺失**（无输入/输出安全层）| 增加 `InputGuardrail` / `OutputGuardrail` 框架（不需三级完整版，至少有钩子）|

**P1（高价值功能差距）：**

| 差距 | 建议 |
|------|------|
| **HITL 跨进程持久化成熟度**（v0.4.0 未经充分测试）| 完整实现 `RunState` 序列化/反序列化 + `approve/reject` API |
| **自动 Tracing**（现有事件系统需手动拼接 span）| 增加自动 span 生成，`tool_executor` 和 `pattern` 自动打 span |
| **Tracing 多平台 export**（仅 Langfuse + Phoenix）| 至少增加 Datadog + W&B，复用 OTel exporter |

**P2（体验提升）：**

| 差距 | 建议 |
|------|------|
| **声明式 stopWhen**（现为整数 max_steps）| 增加 `StopCondition` API（`stepCountIs` / `hasToolCall` / 自定义函数）|
| **Dynamic Instructions**（现需 skills/context_hints 绕过）| 支持 `instructions: Callable[[RunContext], str]` |
| **工具审批标准 API**（`requires_approval` 有但无 approve/reject 流程）| 标准化中断-审批-恢复 API |

**刻意不做（设计原则要求保持）：**
- Multi-Agent Handoffs（产品语义不进内核）
- GUI ComputerTool / Realtime Agent（超出 kernel scope）
- 前端 UI 集成（应用层职责）

### 17.3 定位建议

OpenAgents 应在**内核控制性**和**Python 后端可靠性**上继续深耕，而非向 Vercel AI SDK（前端/TypeScript）或 OpenAI Agents SDK（多 Agent 产品）靠拢。补齐的重点是**生产部署能力**（Session 后端、HITL 持久化、Tracing），而非 Multi-Agent 特性。

---

*报告生成时间：2026-04-24*  
*数据来源：官方文档全文 + 本项目源码精读*
