# 插件开发

这份文档讲三件事：

1. plugin loader 怎么找和实例化插件
2. 每类 plugin / seam 最小契约是什么
3. 什么时候该写插件，什么时候该留在 app-defined protocol

## 1. Loader 模型

loader 的规则很简单：

1. 如果配置里有 `impl`，优先 import
2. 否则如果有 `type`，去 builtin registry 或 decorator registry 查
3. 实例化符号
4. 校验 capability 和方法

实例化时会依次尝试：

- `factory(config=config)`
- `factory(config)`
- `factory()`

所以 class-based plugin 是最稳定的写法。

## 2. Plugin 来源

一个 plugin 当前可以来自三类位置：

- builtin registry
- decorator registry
- 配置中的 `impl` dotted path

注意：

- builtin 和 decorator 都通过 registry 查
- decorator registry 是进程内生效的
- 如果声明 decorator 的模块没有被 import，注册名就不会存在

## 3. 推荐写法

优先写 class-based plugin，并显式提供：

- `config`
- `capabilities`
- 所需的方法实现

你不一定非要继承 `BasePlugin`，但继承通常更省事，也更一致。

## 4. Capability 与方法校验

loader 会检查两件事：

- 必需 capability 是否存在
- 声明过的 capability 是否真的有对应方法

> **踩坑提醒**：如果继承 `PatternPlugin` 或 `ToolPlugin` 基类，**核心 capabilities 已自动注入**，不需要手动声明。但如果用 duck-typed（不继承基类）或 Protocol 方式，**必须显式设置 `self.capabilities`**，否则 loader 会抛 `CapabilityError`。
>
> ```
> CapabilityError: pattern plugin is missing required capabilities: ['pattern.execute']
> CapabilityError: tool plugin 'add_source' is missing required capabilities: ['tool.invoke']
> ```

### 主要 plugin 类型

| 类型 | 必需 capability | 必需方法 | 基类自动注入 |
| --- | --- | --- | --- |
| `pattern` | `pattern.execute` | `execute()` | ✅ `PatternPlugin` 自动注入 `pattern.execute` + `pattern.react` |
| `tool` | `tool.invoke` | `invoke()`, `schema()` | ✅ `ToolPlugin` 自动注入 `tool.invoke` |
| `memory` | `memory.inject` | `inject()` | ✅ `MemoryPlugin` 自动注入 `memory.inject` + `memory.writeback` |
| `runtime` | `runtime.run` | `run()` | ❌ |
| `session` | `session.manage` | `session()` | ❌ |
| `events` | `event.emit` | `emit()`, `subscribe()` | ❌ |
| `tool_executor` | — | `execute()`, `execute_stream()` | — |
| `context_assembler` | — | `assemble()`, `finalize()` | — |
| `skills` | — | plugin-defined（`local` builtin 实现发现/预热/注入） | — |

### memory

memory 稍微特殊一点：

- 如果声明了 `memory.inject`，就必须实现 `inject()`
- 如果声明了 `memory.writeback`，就必须实现 `writeback()`

### 可选覆写

以下方法不是 capability 检查的一部分，但 builtin runtime 会在存在时调用：

| 类型 | 可选方法 | 说明 |
| --- | --- | --- |
| `pattern` | `resolve_followup()` | 本地短路 follow-up（返回 `None` = abstain） |
| `pattern` | `repair_empty_response()` | 空响应降级（返回 `None` = abstain） |
| `tool_executor` | `evaluate_policy()` | 权限判断（默认 allow-all） |

## 5. 最重要的判断

在写插件前，先判断这个需求到底属于哪一类：

- plugin category
- 已有 seam
- app-defined protocol

经验规则：

- 如果它改变的是 runtime 的可复用行为，用 plugin / seam
- 如果它表达的是你的产品语义，优先放 app 层

通常应该留在 app 层的东西：

- coding-task envelope
- review contract
- workflow state machine
- 产品自己的 action summary
- UI 状态语义

## 6. 自定义 Tool

当你要给 pattern 一个可调用的命名能力时，写 Tool。

```python
from __future__ import annotations

from typing import Any

from openagents.interfaces.tool import ToolPlugin


class EchoTool(ToolPlugin):
    """Echo text with a prefix."""

    name = "echo_tool"
    description = "Echo text with a prefix."

    def __init__(self, config: dict[str, Any] | None = None):
        # ToolPlugin 基类自动注入 tool.invoke capability，无需手动声明
        super().__init__(config=config or {})
        self._prefix = self.config.get("prefix", "echo")

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        text = str(params.get("text", "")).strip()
        return {"output": f"{self._prefix}: {text}"}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to echo"}
            },
            "required": ["text"],
        }
```

配置方式：

```json
{
  "tools": [
    {
      "id": "echo",
      "impl": "myapp.plugins.EchoTool",
      "config": {"prefix": "custom"}
    }
  ]
}
```

### TypedConfigPluginMixin

推荐使用 `TypedConfigPluginMixin` 让 `self.config`（raw dict）自动验证为强类型的
`self.cfg`（Pydantic model）：

```python
from pydantic import BaseModel
from openagents.interfaces.typed_config import TypedConfigPluginMixin

class EchoTool(TypedConfigPluginMixin, ToolPlugin):
    class Config(BaseModel):
        prefix: str = "echo"
        max_length: int = 500

    def __init__(self, config=None):
        # ToolPlugin 基类自动注入 tool.invoke，无需 capabilities={TOOL_INVOKE}
        super().__init__(config=config or {})
        self._init_typed_config()
        # self.cfg 是经过验证的 Config 实例
        self._prefix = self.cfg.prefix
        self._max_length = self.cfg.max_length

    async def invoke(self, params, context):
        text = str(params.get("text", "")).strip()[: self.cfg.max_length]
        return {"output": f"{self._prefix}: {text}"}
```

要点：

- `Config` 是嵌套的 `pydantic.BaseModel`
- `_init_typed_config()` 必须在 `super().__init__()` 之后显式调用
- Mixin 必须放在 plugin ABC **前面**，否则 `super().__init__` 无法解析到 ABC
- 未知 config 键只发 warning（0.3.x 迁移安全），未来版本可能切换为 `extra='forbid'`
- 配置验证失败时抛 `PluginConfigError` 并附带 schema hint

### 可选：`preflight()` 预启动检查

`ToolPlugin` 提供一个可选的 `preflight(context)` 钩子，由 `DefaultRuntime` 在每个 session 第一个 agent turn 之前调用一次。对外部依赖型工具（MCP 服务器、子进程型工具、需要 API key 校验的工具）非常有用 —— 它允许你在 LLM 实际选中工具之前就把"未安装 extra / 命令不在 PATH / URL 无效"这类配置错误暴露出来。

```python
from openagents.errors.exceptions import PermanentToolError

class MyExternalTool(ToolPlugin):
    async def preflight(self, context):
        try:
            import my_heavy_dep  # noqa: F401
        except ImportError as e:
            raise PermanentToolError(
                f"[tool:{self.tool_name}] my_heavy_dep not installed",
                tool_name=self.tool_name,
                hint="uv add my-heavy-dep",
            ) from e
```

- 默认实现是 no-op，**不要求**所有工具重写。
- 失败时必须抛 `PermanentToolError`（不是通用 `Exception`）。运行时会把它翻译成 `stop_reason=failed` 的 `RunResult`，并在错误消息里注入失败工具的 id，agent 循环不会启动。
- preflight 不应该做重量级副作用（例如真正启动 MCP 子进程）；需要探活的话请把探活行为作为 opt-in 配置项（MCP 内置工具的 `probe_on_preflight` 就是这个模式）。
- 参见 `McpTool.preflight()` 作为参考实现；具体见 `docs/builtin-tools.md` 的 MCP 段落。

### 可选：`durable_idempotent` 属性（0.4.x 新增）

Durable run（`RunRequest.durable=True`）在捕获 retryable 错误后会从最近 checkpoint 恢复并重新执行 pattern。如果你的工具有**外部可见的副作用**（写文件、发 HTTP POST、启动子进程、修改环境变量等），resume 后它可能再跑一次 —— 对外部状态是非幂等的。

在类体上声明 `durable_idempotent = False` 让 runtime 在 durable run 中首次调用该工具时发出一次性 `run.durable_idempotency_warning` 事件（仅提示、不阻断）：

```python
class MyWriteTool(ToolPlugin):
    durable_idempotent = False  # 默认 True；只读工具可省略
```

内建工具中 `WriteFileTool` / `DeleteFileTool` / `HttpRequestTool` / `ShellExecTool` / `ExecuteCommandTool` / `SetEnvTool` 已默认标为 `False`。读文件、查询类工具保留默认 `True`。

## 7. 自定义 Memory

当你要控制 inject / writeback 行为时，写 Memory。

```python
from __future__ import annotations

from typing import Any

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_WRITEBACK
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.run_context import RunContext


class CustomMemory(MemoryPlugin):
    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={MEMORY_INJECT, MEMORY_WRITEBACK})
        self._state_key = self.config.get("state_key", "custom_history")

    async def inject(self, context: RunContext[Any]) -> None:
        history = context.state.get(self._state_key, [])
        context.memory_view["history"] = list(history)

    async def writeback(self, context: RunContext[Any]) -> None:
        history = list(context.state.get(self._state_key, []))
        history.append(
            {
                "input": context.input_text,
                "output": context.state.get("_runtime_last_output", ""),
            }
        )
        context.state[self._state_key] = history
```

## 8. 自定义 Pattern

当你要控制 agent loop 本身时，写 Pattern。

通常写法是：

- `setup()` 接收 runtime 注入的数据
- 把 `RunContext` 放到 `self.context`
- 在 `execute()` 里编排工具和模型调用

```python
from __future__ import annotations

from typing import Any

from openagents.interfaces.pattern import PatternPlugin


class CustomPattern(PatternPlugin):
    """PatternPlugin 基类自动注入 pattern.execute + pattern.react capability。"""

    async def react(self) -> dict[str, Any]:
        assert self.context is not None
        return {"type": "final", "content": self.context.input_text}

    async def execute(self) -> Any:
        action = await self.react()
        self.context.state["_runtime_last_output"] = action["content"]
        return action["content"]
```

如果你用 duck-typed（不继承基类），**必须**手动设置 capabilities，否则 loader 会抛 `CapabilityError`：

```python
from __future__ import annotations

from typing import Any

from openagents.interfaces.capabilities import PATTERN_EXECUTE, PATTERN_REACT


class CustomPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        # 不继承基类时，capabilities 必须显式声明！
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context = None

    async def setup(self, agent_id, session_id, input_text, state, tools, llm_client, llm_options, event_bus, **kwargs):
        from openagents.interfaces.run_context import RunContext
        self.context = RunContext(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
            state=state,
            tools=tools,
            llm_client=llm_client,
            llm_options=llm_options,
            event_bus=event_bus,
        )

    async def react(self) -> dict[str, Any]:
        return {"type": "final", "content": self.context.input_text}

    async def execute(self) -> Any:
        action = await self.react()
        self.context.state["_runtime_last_output"] = action["content"]
        return action["content"]
```

## 9. 自定义 Skill

Skill 适合做 runtime augmentation，不适合接管整个 agent loop。

如果你要做 Codex / Claude Code 风格的 host-level skill package，不要把它塞进 runtime plugin seam。
那类能力应该交给顶层 `skills` 组件去发现、预热、导入和执行。

## 10. 自定义 Tool Executor

当问题是”tool 应该怎么执行”时，用 `tool_executor`。

常见场景：

- 统一 timeout
- 参数校验
- stream 适配
- 错误规范化

最小契约：

- `execute(request) -> ToolExecutionResult`
- `execute_stream(request)`（async generator）

## 11. 自定义 Tool Policy（覆写 `evaluate_policy()`）

当问题是”tool 能不能执行”时，写一个 `ToolExecutorPlugin` 子类并覆写
`evaluate_policy()`。原先独立的 `execution_policy` seam 在 2026-04-18 合并中
已并入 `tool_executor`。

常见场景：

- file root 限制
- allow / deny
- 动态权限判断
- 产品自己的 policy metadata

最小契约：

- `evaluate_policy(request) -> PolicyDecision`（默认 allow-all）

示例（继承 `SafeToolExecutor` 并覆写 `evaluate_policy`）：

```python
from openagents.interfaces.tool import ToolExecutionRequest, PolicyDecision
from openagents.plugins.builtin.tool_executor.safe import SafeToolExecutor


class MyRestrictedExecutor(SafeToolExecutor):
    ALLOWED_TOOLS = {“read_file”, “http_request”}

    async def evaluate_policy(self, request: ToolExecutionRequest) -> PolicyDecision:
        if request.tool_id not in self.ALLOWED_TOOLS:
            return PolicyDecision(
                allowed=False,
                reason=f”tool '{request.tool_id}' not in allowlist”,
            )
        return PolicyDecision(allowed=True)
```

配置方式：

```json
{
  “tool_executor”: {
    “impl”: “myapp.executor.MyRestrictedExecutor”
  }
}
```

参考：

- builtin `filesystem_aware` 是最简单例子（只包一个 `FilesystemExecutionPolicy`）
- `examples/research_analyst/app/executor.py` 展示如何用 `CompositePolicy` 组合多个 policy helper

## 12. 自定义 Context Assembler

当问题是”run 应该吃进什么上下文”时，用 `context_assembler`。

常见场景：

- transcript trimming
- artifact trimming
- retrieval packaging
- task packet assembly
- summary metadata

最小契约：

- `assemble(request, session_state, session_manager) -> ContextAssemblyResult`
- `finalize(request, session_state, session_manager, result) -> result`

推荐继承 `BaseContextAssembler`（来自
`openagents.plugins.builtin.context.base`），它提供了 token-budget 截断的
helper 方法，使策略实现只需关注排序逻辑：

```python
from openagents.plugins.builtin.context.base import TokenBudgetContextAssembler
from openagents.interfaces.context import ContextAssemblyResult


class MyContextAssembler(TokenBudgetContextAssembler):
    “””Assembles context with custom retrieval injection.”””

    async def assemble(self, request, session_state, session_manager):
        # 1. 构造消息列表
        messages = list(session_state.get(“transcript”, []))

        # 2. 注入 app-defined 内容（例如 retrieval 结果）
        retrieval = request.context_hints.get(“retrieval_results”, [])
        if retrieval:
            messages.append({
                “role”: “system”,
                “content”: “Relevant context:\n” + “\n”.join(retrieval),
            })

        return ContextAssemblyResult(
            messages=messages,
            metadata={“retrieval_count”: len(retrieval)},
        )

    async def finalize(self, request, session_state, session_manager, result):
        # 可选：run 结束后更新 session state
        return result
```

配置方式：

```json
{
  “context_assembler”: {
    “impl”: “myapp.context.MyContextAssembler”,
    “config”: {
      “max_input_tokens”: 16000,
      “reserve_for_response”: 4000
    }
  }
}
```

这也是承载 app-defined context protocol 的最佳 seam 之一。

## 13. 自定义 PatternPlugin（resolve_followup + repair_empty_response）

旧版本独立的 `followup_resolver` / `response_repair_policy` 两个 seam 在 2026-04-18 合并中
已并入 `PatternPlugin`。改为在自己的 pattern 子类上覆写两个可选方法：

### `PatternPlugin.resolve_followup()`

适合本地语义兜底：

- 上一轮做了什么
- 用了哪些工具
- 读了哪些文件

契约：

```python
class MyPattern(ReActPattern):
    async def resolve_followup(self, *, context) -> FollowupResolution | None:
        ...  # 默认返回 None（abstain）
```

builtin `ReActPattern.execute()` 会先调用它；返回 `status="resolved"` 时短路 LLM。
推荐状态：`resolved` / `abstain` / `error`（返回 `None` 等同 abstain）。

完整示例（匹配特殊关键字时本地短路）：

```python
from openagents.plugins.builtin.pattern.react import ReActPattern
from openagents.interfaces.followup import FollowupResolution
from openagents.interfaces.response_repair import ResponseRepairDecision


class SmartReActPattern(ReActPattern):
    async def resolve_followup(self, *, context):
        # 返回 None 表示 abstain（交给 LLM 处理）
        # 返回 FollowupResolution(status="resolved", output=...) 短路 LLM
        if context.input_text.lower() == "status":
            return FollowupResolution(
                status="resolved",
                output="Running.",
            )
        return None  # abstain

    async def repair_empty_response(
        self, *, context, messages, assistant_content, stop_reason, retries
    ):
        # 返回 None 表示 abstain
        # 返回 ResponseRepairDecision(status="repaired", output=...) 恢复
        return None  # abstain
```

### `PatternPlugin.repair_empty_response()`

适合 provider / runtime 的 bad response 降级：

- empty response
- malformed response
- 停止但没内容
- 明确诊断信息

契约：

```python
class MyPattern(ReActPattern):
    async def repair_empty_response(
        self, *, context, messages, assistant_content, stop_reason, retries
    ) -> ResponseRepairDecision | None:
        ...  # 默认返回 None（abstain）
```

builtin pattern 在 provider 返回空串时会调用一次。
推荐状态：`repaired` / `abstain` / `error`（返回 `None` 等同 abstain）。

## 14. App-Defined Middle Protocol

这是高级应用最关键的一层。

很多团队以为自己需要新 seam，实际上更需要的是“把协议放在对的 carrier 上”。

推荐用这些 carrier：

- caller hint -> `RunRequest.context_hints`
- 外部追踪信息 -> `RunRequest.metadata`
- durable per-session state -> `RunContext.state`
- per-run 临时状态 -> `RunContext.scratch`
- assembled context protocol -> `RunContext.assembly_metadata`
- 持久化输出 -> `RunArtifact`

这才是高设计密度 agent 真正该生长的地方。

## 15. Decorator 注册

当前这些类别都支持 decorator registry：

- `tool`
- `memory`
- `pattern`
- `runtime`
- `session`
- `event_bus`
- `tool_executor`
- `context_assembler`

示例：

```python
from openagents import context_assembler


@context_assembler(name="trimmed_context")
class TrimmedContextAssembler:
    ...
```

然后在配置里：

```json
{
  "context_assembler": {
    "type": "trimmed_context"
  }
}
```

注意：

- decorator 注册是进程内的
- 对应模块必须先 import

## 16. 什么时候不要写 plugin

下面这些情况，通常不该上 plugin：

- 只属于你 app 的任务语义
- 用结构化数据就能表达
- 不需要 selector 和复用边界

如果只有一个产品会用，先在 app 层做协议，不要急着进 SDK。

## 17. 插件测试模式

### 推荐测试路径

1. 用 `Runtime.from_dict({...})` 配合 `provider: "mock"` 构造最小 runtime
2. 调用 `runtime.run()` 或 `runtime.run_detailed()`
3. 断言输出、session state、事件或 artifacts

使用 `Runtime.from_dict` 而非 `load_config_dict` + `Runtime(config)` 可以减少一步，
并在配置解析失败时提供更清晰的错误：

```python
import pytest

from openagents.runtime.runtime import Runtime


@pytest.mark.asyncio
async def test_custom_tool_plugin():
    runtime = Runtime.from_dict(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "test",
                    "name": "test",
                    "memory": {"type": "buffer"},
                    "pattern": {"impl": "tests.fixtures.custom_plugins.CustomPattern"},
                    "llm": {"provider": "mock"},
                    "tools": [
                        {"id": "custom_tool", "impl": "tests.fixtures.custom_plugins.CustomTool"}
                    ],
                }
            ],
        }
    )
    result = await runtime.run(agent_id="test", session_id="s1", input_text="hello")
    assert result
```

### 测试 ToolExecutor

```python
@pytest.mark.asyncio
async def test_restricted_executor_blocks_unknown_tool():
    runtime = Runtime.from_dict(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "agent",
                    "name": "agent",
                    "memory": {"type": "buffer"},
                    "pattern": {"type": "react"},
                    "llm": {"provider": "mock"},
                    "tool_executor": {
                        "impl": "myapp.executor.MyRestrictedExecutor",
                    },
                    "tools": [
                        {"id": "dangerous_tool", "impl": "tests.fixtures.custom_plugins.DangerousTool"},
                    ],
                }
            ],
        }
    )
    result = await runtime.run(agent_id="agent", session_id="s1", input_text="run dangerous_tool")
    # MyRestrictedExecutor should have blocked the tool
    assert "not in allowlist" in str(result)
```

### 测试事件

```python
@pytest.mark.asyncio
async def test_events_emitted():
    events_received = []

    runtime = Runtime.from_dict({
        "version": "1.0",
        "events": {"type": "async"},
        "agents": [...],
    })
    runtime.event_bus.subscribe("tool.*", lambda e: events_received.append(e))
    await runtime.run(agent_id="agent", session_id="s1", input_text="hello")
    assert any(e.name.startswith("tool.") for e in events_received)
```

仓库里的好参考：

- `tests/unit/test_plugin_loader.py` — plugin 加载和 capability 校验
- `tests/unit/test_runtime_orchestration.py` — 端到端 runtime 流程
- `tests/fixtures/custom_plugins.py` — 各类 plugin 的最小实现模板
- `tests/fixtures/runtime_plugins.py` — 自定义 runtime/session plugin 示例
- `examples/production_coding_agent/` — 完整的 production 级插件组合

## 18. Typed Config

新版插件推荐用 `TypedConfigPluginMixin` 为 `self.config` 生成强类型的 `self.cfg`。

写法（必须把 mixin 放在 plugin ABC 之前，让 `super().__init__` 还能解析到 ABC）：

```python
from pydantic import BaseModel, Field

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_WRITEBACK
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class BufferMemory(TypedConfigPluginMixin, MemoryPlugin):
    class Config(BaseModel):
        state_key: str = "memory_buffer"
        view_key: str = "history"
        max_items: int | None = Field(default=None, gt=0)

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={MEMORY_INJECT, MEMORY_WRITEBACK},
        )
        self._init_typed_config()

    async def inject(self, context):
        # Read typed fields off self.cfg
        view_key = self.cfg.view_key
        ...
```

要点：

- `Config` 是嵌套 `pydantic.BaseModel`
- `_init_typed_config()` 必须在 `super().__init__()` 之后显式调用
- 未知 config 键不会报错，只会经 `openagents.interfaces.typed_config` 的 logger 发一条 warning，便于平滑迁移
- 未来 0.4.x 可能会切到 `extra='forbid'` 严格模式

## 19. Composing plugins

写 combinator 类型 plugin（嵌套加载其它 plugin）的时候，用公开的 `load_plugin`：

```python
from openagents.config.schema import ToolExecutorRef
from openagents.plugins.loader import load_plugin


class MyRetryExecutor:
    def __init__(self, config: dict[str, Any] | None = None):
        ...
        inner_ref = ToolExecutorRef(**config["inner"])
        self._inner = load_plugin(
            "tool_executor",
            inner_ref,
            required_methods=("execute", "execute_stream"),
        )
```

`openagents.plugins.loader._load_plugin` 仍然可用，但会发 `DeprecationWarning`，
计划在后续版本移除。所有 in-tree combinator（`memory.chain`, `tool_executor.retry`,
`execution_policy.composite`, `events.file_logging`）都已迁到公开 API。

## 20. 三段式 Docstring（Spec B WP4）

所有内置插件类必须在类 docstring 里包含三个段落：

```python
class MyMemory(MemoryPlugin):
    """One-line summary ending with a period.

    What:
        2-4 sentences describing what this plugin does and why
        (the user-facing behavior).

    Usage:
        Configuration shape and a 1-2 line example:
        ``{"type": "my_memory", "config": {"key": "value"}}``

    Depends on:
        - ``RunContext.state`` for X
        - sibling plugin ``baz``
        - external resource Y
    """
```

`tests/unit/test_builtin_docstrings_are_three_section.py` 强制这一格式。
工具类的 Usage / Depends on 段落可以一行带过；非工具类建议写完整。

## 21. 错误 hint / docs_url（Spec B WP1）

`OpenAgentsError`（含子类）支持可选 `hint=` / `docs_url=` 关键字参数，
建议在用户可能因为典型错误（拼写、缺配置、找不到 ID）触发的位置带上：

```python
from openagents.errors.exceptions import PluginLoadError
from openagents.errors.suggestions import near_match

available = sorted(known_plugins.keys())
guess = near_match(requested, available)
hint_text = (
    f"Did you mean '{guess}'?" if guess else f"Available: {available}"
)
raise PluginLoadError(
    f"Unknown plugin: '{requested}'",
    hint=hint_text,
)
```

`str(exc)` 会自动多出一行 `hint: ...`；首行保持原 message 不变以保护
日志聚合。

## 22. 事件分类（Spec B WP2）

发射的事件名建议在 `openagents/interfaces/event_taxonomy.py:EVENT_SCHEMAS`
登记，并在 `docs/event-taxonomy.md` 同步描述（运行
`uv run python -m openagents.tools.gen_event_doc` 生成）。`AsyncEventBus.emit`
会对已登记事件做 advisory 校验：缺少必需 payload key 会 warning，从不
raise。未登记的事件名直接放行。

## 23. Optional extras（Spec C）

如果你的插件依赖一个 heavy / 可选的 PyPI 包（例如 `aiosqlite`、
`opentelemetry-api`、`mem0ai`、`mcp`），不要把它放进 `[project]
dependencies`，而是声明成一个 optional extra：

```toml
[project.optional-dependencies]
sqlite = ["aiosqlite>=0.20.0"]
otel = ["opentelemetry-api>=1.25.0"]
```

模块顶层用 fail-soft import 守住缺失：

```python
try:
    import aiosqlite
    _HAS_AIOSQLITE = True
except ImportError:
    aiosqlite = None  # type: ignore[assignment]
    _HAS_AIOSQLITE = False
```

`__init__` 里在用户尝试构造时报 `PluginLoadError` 并带上安装提示：

```python
from openagents.errors.exceptions import PluginLoadError

class MyOptionalPlugin(...):
    def __init__(self, config=None):
        if not _HAS_AIOSQLITE:
            raise PluginLoadError(
                "session 'sqlite' requires the 'aiosqlite' package",
                hint="Install the 'sqlite' extra: uv sync --extra sqlite",
            )
        ...
```

这样 `openagents.plugins.registry` 即使在 extras 没装时也能 import
（`_BUILTIN_REGISTRY` 注册的是类符号本身，不会去构造）。
对应的测试用 `pytest.importorskip("aiosqlite")` 在文件顶部 skip
掉，默认 `uv sync` 仍然全绿；CI 单独装 extra 跑一次即可。

把新文件加进 `[tool.coverage.report] omit`，避免可选依赖没装时拖
垮覆盖率门槛。

## 24. 继续阅读

- [开发者指南](developer-guide.md)
- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [API 参考](api-reference.md)
- [示例说明](examples.md)
