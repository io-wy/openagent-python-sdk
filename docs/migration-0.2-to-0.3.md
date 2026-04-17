# 0.2.0 → 0.3.0 迁移指南

`0.3.0` 是继 `0.2.0` 模型现代化之后的第二次破坏性切换（pre-1.0 包，允许 breaking）。
本次只深化**现有契约**，不新增 seam。全部改动都在现有 seam 内部或落在
kernel protocol 对象上。

- 对应 spec：`docs/superpowers/specs/2026-04-16-openagents-sdk-kernel-completeness-design.md`
- 对应实施计划：`docs/superpowers/plans/2026-04-16-openagents-sdk-kernel-completeness-implementation-plan.md`
- 变更清单：[CHANGELOG.md](../CHANGELOG.md)

## 按使用场景分类

### 场景 A：我只调用 `Runtime.run_detailed`，没有自定义 pattern

**零改动。**`RunResult` 变成了 `RunResult[Any]`，`final_output: Any` 等价于旧行为。

### 场景 B：我有自定义 pattern 但不需要 `output_type`

**零改动。**`PatternPlugin.finalize()` 有基类默认实现，`output_type=None` 时直接返回
原始输出。运行时的校验重试循环只在 `output_type` 显式设置时才参与。

### 场景 C：我的配置里用了 `context_assembler: summarizing`

**必须改。**0.3.0 把它改名为 `truncating`，因为老实现并没有真的做 summarization。

```diff
- "context_assembler": {"type": "summarizing"}
+ "context_assembler": {"type": "truncating"}
```

或选用新引入的三种 token 预算策略：

- `"head_tail"` — 保留开头 N 条 + 尾部按预算
- `"sliding_window"` — FIFO 丢前面直到满足预算
- `"importance_weighted"` — system/最近 user/最近 tool 优先保留

加载旧名时运行时会立刻抛 `PluginLoadError` 并包含迁移提示。

### 场景 D：我想要类型化结构化输出

```python
from pydantic import BaseModel
from openagents.interfaces.runtime import RunRequest, RunBudget
from openagents.runtime.runtime import Runtime

class UserProfile(BaseModel):
    name: str
    age: int

runtime = Runtime.from_dict(config)
result = await runtime.run_detailed(
    request=RunRequest(
        agent_id="assistant",
        session_id="s",
        input_text="give me a user profile",
        output_type=UserProfile,
        budget=RunBudget(max_validation_retries=3),
    )
)
# 成功：result.final_output 是 UserProfile 实例
# 超出重试预算：result.stop_reason == FAILED
#                result.exception 是 OutputValidationError
```

校验失败时 runtime 自动：

1. 把错误写入 `context.scratch["last_validation_error"]`
2. 发 `validation.retry` 事件
3. 重入 `pattern.execute()`（内置三种 pattern 在 `execute()` 开头自动读取 scratch 并注入一条 `role=system` 的纠正消息到 transcript）

若自定义 pattern 需要支持校验循环，在 `execute()` 开头加一行：

```python
class MyPattern(PatternPlugin):
    async def execute(self):
        self._inject_validation_correction()
        # ... 你原来的逻辑
```

### 场景 E：我想看见成本 / 想限制成本

```python
from openagents.interfaces.runtime import RunBudget

result = await runtime.run_detailed(
    request=RunRequest(
        agent_id="assistant", session_id="s", input_text="...",
        budget=RunBudget(max_cost_usd=0.50),
    )
)
print(result.usage.cost_usd)        # 累计 USD 成本
print(result.usage.cost_breakdown)  # {"input": ..., "output": ..., ...}
```

在配置里自定义某个 agent 的单价：

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "pricing": {
      "input": 3.0,
      "output": 15.0
    }
  }
}
```

Provider 内置了常见模型的默认价格表（Anthropic：opus/sonnet/haiku 4.x；
OpenAI：gpt-4o / gpt-4o-mini / o1）。若模型未命中内置表，成本字段保持 `None`，
`max_cost_usd` 也会静默跳过并发一次性 `budget.cost_skipped` 事件。

### 场景 F：我自定义了一个 LLM provider

基类新增以下可选属性/方法：

```python
class MyProvider(LLMClient):
    price_per_mtok_input: float | None = None
    price_per_mtok_output: float | None = None
    price_per_mtok_cached_read: float | None = None
    price_per_mtok_cached_write: float | None = None

    def count_tokens(self, text: str) -> int:
        # 可选覆盖；默认基类用 len(text)//4 并 WARN 一次。
        ...
```

provider 的 `generate()` 实现在构造 `LLMResponse` 后建议调用
`self._compute_cost_for(usage=normalized_usage, overrides=self._pricing_overrides)`
来填充 `usage.metadata["cost_usd"]` 和 `cost_breakdown`。Pattern 层的累计
依赖这些元数据字段。

### 场景 G：我想要流式

```python
from openagents.interfaces.runtime import RunStreamChunkKind

async for chunk in runtime.run_stream(request=request):
    if chunk.kind is RunStreamChunkKind.LLM_DELTA:
        print(chunk.payload.get("text"), end="", flush=True)
    elif chunk.kind is RunStreamChunkKind.RUN_FINISHED:
        print("\n[DONE]", chunk.result.final_output)
```

同步入口：`stream_agent_with_dict(config_dict, request=...)`、
`stream_agent_with_config(path, request=...)`。

流式实现是对现有 event bus 的投影 —— 所有已有事件（`run.started`,
`tool.called`, `tool.succeeded`, `validation.retry`, …）会自动映射成
`RunStreamChunk`。consumer 靠 `sequence` 字段断连续性。

## CLI 入口

```bash
openagents schema                           # dump AppConfig JSON Schema
openagents schema --seam context_assembler  # dump 某一 seam 下所有插件 config schema
openagents schema --plugin truncating       # dump 单个插件
openagents validate path/to/agent.json      # 不运行，只校验
openagents validate path/to/agent.json --strict  # 额外校验所有 type 能解析
openagents list-plugins                     # 列所有已注册插件
openagents list-plugins --format json       # 机读格式
```

也可以用 `python -m openagents <subcommand>`。

YAML 输出 (`--format yaml`) 需要 optional 依赖：`pip install io-openagent-sdk[yaml]`。

## 已知限制

- 价格表会随时间过期，生产部署请在 `llm.pricing` 里覆盖。
- `max_validation_retries` 不能防止总成本膨胀；同时设 `max_cost_usd`。
- Streaming 下每次校验重试会产生一段新的 delta 序列；消费方用 `validation.retry`
  chunk 的 `attempt` 字段区分不同尝试。
- `LLMClient.count_tokens` 在 Anthropic 上当前走 `len//4` fallback（Phase 2 再接
  provider-native tokenizer）。OpenAI-compatible 在装了 `tiktoken` 时走原生。

## 0.3.x cleanup pass: plugin loader API & event payload changes

- `openagents.plugins.loader._load_plugin` → `load_plugin` (public).
  下划线别名仍然可用但会发 `DeprecationWarning`。自定义 combinator plugin
  请切到公开导入。

- `tool.succeeded` 事件 payload 现在多一个 `executor_metadata` 字段，
  携带 `RetryToolExecutor` 的 `retry_attempts`、`SafeToolExecutor` 的
  `timeout_ms`、`CompositeExecutionPolicy` 的 `decided_by` 等执行器侧元数据。
  只读 `tool_id` 和 `result` 的订阅方不受影响。

- `_BoundTool.invoke()`（kernel-internal）现在返回 `ToolExecutionResult`
  而非 `result.data`。如果你的自定义 pattern 绕过了基类 `call_tool` 并直接
  调用 `tool.invoke()`，请用从 `openagents.interfaces.pattern` 导出的
  公开 helper `unwrap_tool_result(result)` 兼容 bound 与 raw 两种返回形态。

- 内置插件现在通过 `TypedConfigPluginMixin` 校验 `self.config`。未知键
  不再静默丢弃，而是发一条 `received unknown config keys` 警告。审计你
  的 `agent.json` 时检查进程日志。下一个 major 版本会变成错误。

## 0.3.x hardening pass: error hints, event taxonomy, concurrency

- 所有 `OpenAgentsError` 子类现在支持可选的 `hint=` 和 `docs_url=` 两个
  关键字参数，内置错误位点已经按需启用了它们。`str(exc)` 在带 hint /
  docs_url 时会输出多行（首行仍然是原 message，hint / docs 各占一行带
  缩进）。如果你解析错误文本，请直接读 `exc.hint` / `exc.docs_url`。

- 事件分类现在记录在 `docs/event-taxonomy.md`，源数据在
  `openagents/interfaces/event_taxonomy.py:EVENT_SCHEMAS`。`AsyncEventBus.emit`
  在已声明的事件缺少必需 payload key 时会 `logger.warning`（从不 raise）。
  自定义未声明事件不会被校验。`DefaultRuntime` 新增 8 个 lifecycle 事件：
  `session.run.started/completed`、`context.assemble.started/completed`、
  `memory.inject.started/completed`、`memory.writeback.started/completed`。
  原有事件名 / payload 不变。订阅 `*` 通配符的处理器每次 run 会多收到约 8
  条事件 - 建议改用具名订阅。

- `JsonlFileSessionManager` / `FileLoggingEventBus` / `RetryToolExecutor` /
  `ChainMemory` / `Runtime.run` 都通过了 7 项并发 / IO 失败的 stress 测试，
  无需新增锁或重试包装；相应测试以回归门的形式入库。

- 全部 ~40 个内置插件类的 docstring 现已统一为三段式 Google-style
  （`What:` / `Usage:` / `Depends on:`）。新增的
  `tests/unit/test_builtin_docstrings_are_three_section.py` 守住这一约束。

- 覆盖率门槛从 90% 提升到 92%。配置 `pyproject.toml` 的
  `[tool.coverage.report].fail_under`。
