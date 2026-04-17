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

### 主要 plugin 类型

| 类型 | 必需 capability | 必需方法 |
| --- | --- | --- |
| pattern | `pattern.execute` | `execute()` |
| tool | `tool.invoke` | `invoke()` |
| runtime | `runtime.run` | `run()` |
| session | `session.manage` | `session()` |
| events | `event.emit` | `emit()`，并要求 `subscribe()` |

### memory

memory 稍微特殊一点：

- 如果声明了 `memory.inject`，就必须实现 `inject()`
- 如果声明了 `memory.writeback`，就必须实现 `writeback()`

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

from openagents.interfaces.capabilities import TOOL_INVOKE
from openagents.interfaces.run_context import RunContext
from openagents.interfaces.tool import ToolPlugin


class EchoTool(ToolPlugin):
    name = "echo_tool"
    description = "Echo text with a prefix."

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {}, capabilities={TOOL_INVOKE})
        self._prefix = self.config.get("prefix", "echo")

    async def invoke(self, params: dict[str, Any], context: RunContext[Any] | None) -> Any:
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

from openagents.interfaces.capabilities import PATTERN_EXECUTE, PATTERN_REACT
from openagents.interfaces.run_context import RunContext


class CustomPattern:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.capabilities = {PATTERN_EXECUTE, PATTERN_REACT}
        self.context: RunContext[Any] | None = None

    async def setup(
        self,
        agent_id: str,
        session_id: str,
        input_text: str,
        state: dict[str, Any],
        tools: dict[str, Any],
        llm_client: Any,
        llm_options: Any,
        event_bus: Any,
        **kwargs: Any,
    ) -> None:
        self.context = RunContext[Any](
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
        assert self.context is not None
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

当问题是“tool 应该怎么执行”时，用 `tool_executor`。

常见场景：

- 统一 timeout
- 参数校验
- stream 适配
- 错误规范化

最小契约：

- `execute(request) -> ToolExecutionResult`
- `execute_stream(request)`

## 11. 自定义 Execution Policy

当问题是“tool 能不能执行”时，用 `execution_policy`。

常见场景：

- file root 限制
- allow / deny
- 动态权限判断
- 产品自己的 policy metadata

最小契约：

- `evaluate(request) -> PolicyDecision`

## 12. 自定义 Context Assembler

当问题是“run 应该吃进什么上下文”时，用 `context_assembler`。

常见场景：

- transcript trimming
- artifact trimming
- retrieval packaging
- task packet assembly
- summary metadata

最小契约：

- `assemble(request, session_state, session_manager) -> ContextAssemblyResult`
- `finalize(request, session_state, session_manager, result) -> result`

这也是承载 app-defined context protocol 的最佳 seam 之一。

## 13. 自定义 Follow-up / Repair

### `followup_resolver`

适合本地语义兜底：

- 上一轮做了什么
- 用了哪些工具
- 读了哪些文件

最小契约：

- `resolve(context=...) -> FollowupResolution | None`

推荐状态：

- `resolved`
- `abstain`
- `error`

### `response_repair_policy`

适合 provider / runtime 的 bad response 降级：

- empty response
- malformed response
- 停止但没内容
- 明确诊断信息

最小契约：

- `repair_empty_response(...) -> ResponseRepairDecision | None`

推荐状态：

- `repaired`
- `abstain`
- `error`

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
- `execution_policy`
- `context_assembler`
- `followup_resolver`
- `response_repair_policy`

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

## 17. 如何测试 plugin

最实用的测试路径是：

1. 构造一个 config dict
2. `load_config_dict()`
3. `Runtime(config)`
4. 运行目标 agent
5. 断言输出、session state、事件或 artifacts

示例：

```python
import pytest

from openagents.config.loader import load_config_dict
from openagents.runtime.runtime import Runtime


@pytest.mark.asyncio
async def test_custom_tool_plugin():
    config = load_config_dict(
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
                    ]
                }
            ]
        }
    )
    runtime = Runtime(config)
    result = await runtime.run(agent_id="test", session_id="s1", input_text="hello")
    assert result
```

仓库里的好参考：

- `tests/unit/test_plugin_loader.py`
- `tests/unit/test_runtime_orchestration.py`
- `tests/fixtures/custom_plugins.py`
- `tests/fixtures/runtime_plugins.py`
- `examples/production_coding_agent/`

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

## 20. 继续阅读

- [开发者指南](developer-guide.md)
- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [API 参考](api-reference.md)
- [示例说明](examples.md)
