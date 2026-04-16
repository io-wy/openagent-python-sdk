# OpenAgents SDK 0.3.0 — Kernel Completeness 设计

## 目标

把 `openagent-python-sdk` 在 `0.2.0` modernization 打下的类型化 / Pydantic 化底子「做到完整」，不是「扩大表面」。

`0.3.0` 的目标产物：

- 事件级流式输出合约 `Runtime.run_stream()`
- 类型化结构化输出 `RunResult[OutputT]` + 校验失败自动重试
- Provider 声明式价格表、`RunUsage` 成本跟踪、`RunBudget.max_cost_usd` 中心化执行
- `Pattern.finalize()` 钩子与基类默认实现
- Context assembler 重做：诚实命名 + 三种 token 预算策略 + `LLMClient.count_tokens`
- `openagents` CLI：`schema` / `validate` / `list-plugins`

所有改动**严格在现有 seam 内部**或落在 kernel protocol 对象上。**不新增任何 seam。**

## 非目标

本 Spec 明确不做，分别归属后续 Phase：

- **Phase 2**：LLM 驱动的真正 summarizing context assembler、跨 provider prompt caching 统一协议、新增 LLM provider（gemini / bedrock / azure）、向量化 memory backend、retrying / caching tool executor、approval-gate 执行策略。
- **Phase 3**：Test harness（FakeLLM / transcript 断言 / record-replay）、`openagents run / replay / scaffold`、OpenTelemetry 桥、插件 scaffolding 生成器。
- **永远不做**：新 seam、多 agent 编排、graph runtime、middleware stack、把产品语义塞进 kernel 的字段（产品语义仍由 `RunRequest.context_hints / metadata`、`RunContext.state / scratch / assembly_metadata`、`RunArtifact.metadata` 承载）。

## Ground Truth Boundary

本 Spec **不**把 OpenAgents 变成 graph runtime、不引入多 agent 编排、不引入 middleware stack、不引入 DI 容器。SDK 保持 single-agent execution kernel 本色（参考 `docs/seams-and-extension-points.md`）。

## 当前仓库真相（简要）

`0.2.0` 已经完成：Pydantic 化、typed `RunContext[DepsT]`、结构化异常树、explicit plugin loader、budget enforcement 框架。在此基础上还剩六个被简化或未完工的点：

1. `Runtime` 无流式入口；provider 层有 SSE 解析但无 kernel 合约。
2. `RunResult.final_output: Any`；无结构化输出合约。
3. `ModelRetryError` 已定义（`0.2.0` spec 引入）但未接入实际循环。
4. `RunUsage` 只有 token 计数，无 cost、无 cached-token 字段；`RunBudget` 无 cost 预算。
5. `SummarizingContextAssembler` 实际只做 truncation，名不副实；无 token 预算感知。
6. 无 `openagents` CLI；`AppConfig.model_json_schema()` 无官方出口。

## 外部设计信号

- **Pydantic AI**：`RunContext[DepsT]` 已被 `0.2.0` 借鉴；`ModelRetry` 反馈机制继续借鉴进本 Spec。
- **OpenAI Agents SDK**：`RunContextWrapper[TContext]` 的 local-context 原则已被 `0.2.0` 采纳；本 Spec 的结构化输出走更接近 Pydantic AI 的 per-call 声明而非 OpenAI 的 agent-level 声明（经讨论选定 Q4 选项 A）。
- **OpenAI Assistants API**：事件级 stream 合约的灵感来源（本 Spec 的 `RunStreamChunk` 采同类形状）。
- **不借鉴**：LangGraph graph runtime、middleware stack、full DI 容器。

---

## 1. 范围与边界（总览）

### 1.1 Phase 1 纳入项

| # | 项目 | 接触的公共表面 |
| --- | --- | --- |
| 1 | 事件级流式输出 | `Runtime.run_stream()`、`RunStreamChunk`、sync helpers |
| 2 | 类型化结构化输出 | `RunResult[OutputT]`、`RunRequest.output_type` |
| 3 | `ModelRetryError` 接入两条路径 | `Pattern.finalize`、`pattern.call_tool`、`RunBudget.max_validation_retries` |
| 4 | `Pattern.finalize` 钩子 | `PatternPlugin` 基类新方法、三个 builtin pattern 覆盖 |
| 5 | 成本跟踪 | `LLMClient.price_per_mtok_*`、`RunUsage.cost_usd`、`RunBudget.max_cost_usd` |
| 6 | Context assembler 重做 | `TruncatingContextAssembler` 改名 + 三个 builtin 新增 + `LLMClient.count_tokens` |
| 7 | CLI | `openagents schema / validate / list-plugins` + plugin `Config` 约定 |

### 1.2 非目标

见开头「非目标」章节。

### 1.3 破坏性变更姿态

`0.3.0` 是继 `0.2.0` 之后的第二次 breaking cut。包仍处于 pre-1.0，允许 breaking，但必须同时发布迁移文档（见 §7.3）。

---

## 2. 流式输出合约

### 2.1 入口

```python
# openagents/runtime/runtime.py
async def run_stream(
    self,
    request: RunRequest,
) -> AsyncIterator[RunStreamChunk]: ...

# openagents/runtime/sync.py
def stream_agent(request: RunRequest) -> Iterator[RunStreamChunk]: ...
def stream_agent_with_config(path: str, request: RunRequest) -> Iterator[RunStreamChunk]: ...
def stream_agent_with_dict(payload: dict, request: RunRequest) -> Iterator[RunStreamChunk]: ...
```

sync 版本用 `asyncio.run()` + `queue.Queue` 桥接，不暴露任何 async 概念给同步调用方。

### 2.2 `RunStreamChunk` 联合模型

放在 `openagents/interfaces/runtime.py`，与其它 kernel protocol 对象同层：

```python
class RunStreamChunkKind(str, Enum):
    RUN_STARTED       = "run.started"
    LLM_DELTA         = "llm.delta"
    LLM_FINISHED      = "llm.finished"
    TOOL_STARTED      = "tool.started"
    TOOL_DELTA        = "tool.delta"
    TOOL_FINISHED     = "tool.finished"
    ARTIFACT          = "artifact"
    VALIDATION_RETRY  = "validation.retry"
    RUN_FINISHED      = "run.finished"


class RunStreamChunk(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: RunStreamChunkKind
    run_id: str
    session_id: str
    agent_id: str
    sequence: int                      # 单调递增，消费方据此断连续性
    timestamp_ms: int
    payload: dict[str, Any] = Field(default_factory=dict)
    result: "RunResult[Any] | None" = None   # 仅 RUN_FINISHED 非 None
```

**`payload` 字段公约**（写死在 kernel、不开放扩展；新 chunk kind 必须在本 Spec 的后续修订中增补）：

- `run.started`：`{}`
- `llm.delta`：`{"text": "...", "model": "..."}`
- `llm.finished`：`{"text": "...", "usage": {...}}`
- `tool.started`：`{"tool_id": "...", "params": {...}}`
- `tool.delta`：`{"tool_id": "...", "chunk": {...}}`（透传 `tool.invoke_stream` 产物）
- `tool.finished`：`{"tool_id": "...", "success": bool, "data": ..., "error": "..."}`
- `artifact`：`{"name": "...", "kind": "...", "metadata": {...}}`（完整 artifact 仍在 `RunResult.artifacts`）
- `validation.retry`：`{"attempt": N, "error": "..."}`
- `run.finished`：`{}`；真实结果在 `result`

### 2.3 事件总线投影

`run_stream()` **不是**新事件源，而是现有 `EventBusPlugin` 的投影。实现：

1. `Runtime.run_stream(request)` 起内部 `asyncio.Queue[RunStreamChunk]`。
2. 临时订阅 agent event bus，映射内部事件名到 `RunStreamChunkKind`。映射表写在 `openagents/runtime/stream_projection.py`，不开放扩展。
3. 起 task 跑 `runtime.run_detailed(request)`；task 完成时把 `RunResult` 包入 `RUN_FINISHED` chunk 推入 queue，然后 close queue。
4. `run_stream()` `async for` queue，`yield` chunk。task 异常 → projection 兜底 `RUN_FINISHED(result=RunResult(stop_reason=FAILED, exception=...))`。

现有 pattern / memory / tool 的 emit 逻辑零改动；stream 消费方看到与事件总线订阅方完全一致的事件序列。

### 2.4 LLM delta 来源

- `LLMClient.generate(...)` 签名保留。
- 新增 `LLMClient.stream(...)` 合约（provider 支持时）：yield `{"type": "text.delta", "text": "..."}` / `{"type": "text.finished", "text": "..."}` / `{"type": "usage", "usage": {...}}`。
- `Pattern.call_llm(...)` 基类：若 `context.scratch["__runtime_streaming__"] is True`，走 `llm_client.stream()`，每个 delta 经 `event_bus.emit("llm.delta", ...)`；否则走 `generate()`。
- Provider 不支持 stream 时，`stream()` 默认实现把 `generate()` 结果一次性 yield（单条 `text.delta` + `text.finished`），保证消费方永远拿到 `llm.delta + llm.finished` 序列（只是无真实 delta 粒度）。

### 2.5 Tool delta 来源

`ToolExecutionSpec` 新增：

```python
class ToolExecutionSpec(BaseModel):
    ...
    supports_streaming: bool = False
```

`pattern.call_tool()` 改动：

- `context.scratch["__runtime_streaming__"] is True` 且 `tool.execution_spec().supports_streaming` → 走 `invoke_stream()`，每个产物 emit `tool.delta`，最终 emit `tool.finished`。
- 否则走 `invoke()`，只产 `tool.started` + `tool.finished`。

显式 opt-in，避免现有 tool 被默默迁入流式路径。

### 2.6 取消与断流

- 消费方 `break` 出 `async for` → 触发生成器退出 → `CancelledError` → 内部 task cancel → `DefaultRuntime.run()` 的 try/finally 释放 session lock。
- `RunResult.stop_reason` 在被动取消时填 `CANCELLED`。

### 2.7 对非流式路径的影响

- `Runtime.run_detailed(request)` 签名与行为保持不变（非 breaking）。
- `run_stream` 是**并列**新入口，不替换 `run_detailed`。
- 内部共享 `_execute_run(request, streaming: bool)` 核心路径，streaming 分支额外做 event projection。

### 2.8 测试策略

- `tests/unit/test_runtime_stream.py`：chunk 顺序、sequence 单调、`RUN_FINISHED` 携带 `RunResult`、异常路径兜底。
- `tests/unit/test_stream_projection.py`：映射表覆盖所有现有 emit 点（防止新 emit 点被忘记映射，通过遍历源码 `emit(` 调用的正则辅助断言）。
- `tests/integration/test_run_stream_end_to_end.py`：mock provider 完整流程，与同配置下 `run_detailed` 结果做 diff 断言。
- `tests/integration/test_run_stream_cancel.py`：消费方提前 break 的取消路径，断言 session lock 释放、`RunResult.stop_reason == CANCELLED`。

---

## 3. 类型化结构化输出 + 校验重试

### 3.1 核心模型变化

```python
# openagents/interfaces/runtime.py
OutputT = TypeVar("OutputT")

class RunResult(BaseModel, Generic[OutputT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    final_output: OutputT | None = None
    stop_reason: StopReason = StopReason.COMPLETED
    usage: RunUsage = Field(default_factory=RunUsage)
    artifacts: list[RunArtifact] = Field(default_factory=list)
    error: str | None = None
    exception: OpenAgentsError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    ...
    output_type: type[BaseModel] | None = None


class RunBudget(BaseModel):
    ...
    max_validation_retries: int | None = 3   # None 无上限；0 禁用重试
    max_cost_usd: float | None = None        # 见 §4
```

- `output_type` **仅接受 `BaseModel` 子类**（经讨论锁定，不采用 TypeAdapter）。
- 未声明 `output_type` 的调用方拿 `RunResult[Any]`，`final_output: Any` 等价旧行为。
- 不提供 agent config 级别的 default `output_type`（避免两处配置分散）。

### 3.2 `Pattern.finalize` 钩子

```python
# openagents/interfaces/pattern.py
class PatternPlugin(BasePlugin):
    ...

    async def finalize(
        self,
        raw: Any,
        output_type: type[BaseModel] | None,
    ) -> Any:
        """Coerce and validate the pattern's raw output.

        Default behavior:
          - output_type is None → return raw unchanged.
          - output_type present → call output_type.model_validate(raw).

        Subclasses may pre-process raw (strip code fences, pull last JSON
        block, merge multiple tool outputs) before delegating to super().
        """
        if output_type is None:
            return raw
        try:
            return output_type.model_validate(raw)
        except ValidationError as exc:
            raise ModelRetryError(
                message=self._format_validation_error(exc),
                validation_error=exc,
            )

    def _format_validation_error(self, exc: ValidationError) -> str:
        """Format a Pydantic ValidationError for model-facing correction."""
        ...
```

`_format_validation_error` 把 Pydantic 错误树格式化为「给模型看的可读文本」（field path + expected + actual），基类实现，子类通常不改。

### 3.3 `ModelRetryError` 的两条路径

**路径 A — 最终输出校验失败（runtime 层驱动）：**

```
pattern.execute() → raw
runtime: validated = await pattern.finalize(raw, output_type)
  ├── 成功 → final_output = validated, stop_reason = COMPLETED, 返回 RunResult
  └── raise ModelRetryError:
        ├── attempts += 1
        ├── if attempts > max_validation_retries:
        │     → RunResult(stop_reason=FAILED,
        │                 exception=OutputValidationError(...))
        └── else:
              context.scratch["last_validation_error"] = {
                "attempt": attempts,
                "message": exc.message,
                "expected_schema": output_type.model_json_schema(),
              }
              event_bus.emit("validation.retry", attempt=attempts, error=...)
              → 重入 pattern.execute()（不重跑 setup / memory.inject）
```

重入 `pattern.execute()` 要求 pattern 自己在 `execute` 开头检查 `scratch["last_validation_error"]`，把错误作为「上一轮输出不合规、请修正」追加到 transcript 的 `role=system` 消息。内置三个 pattern（`react` / `plan_execute` / `reflexion`）**都要加**这个读取逻辑，并共享一个基类 helper：

```python
class PatternPlugin(BasePlugin):
    def _inject_validation_correction(self) -> None:
        err = self.context.scratch.pop("last_validation_error", None)
        if err is None:
            return
        self.context.transcript.append({
            "role": "system",
            "content": (
                f"Your previous final output failed validation "
                f"(attempt {err['attempt']}): {err['message']}\n"
                f"Expected schema: {json.dumps(err['expected_schema'], indent=2)}\n"
                f"Please produce a corrected final output."
            ),
        })
```

**路径 B — tool 参数校验失败（pattern 层驱动）：**

Tool 可在 `validate_params()` 或 `invoke()` 主动 `raise ModelRetryError(...)`。该路径不走 runtime 级重试循环：

- `pattern.call_tool()` 基类捕获 `ModelRetryError`。
- emit `tool.retry_requested` 事件，把 `validation_error.message` 塞进一条 `role=system` 的 tool_error 响应，让下一轮 LLM call 自然读到。
- **不占用 runtime 级重试预算**（路径 A 使用的 `max_validation_retries` 只对最终输出校验生效）；tool 循环纠错的总体上限由 `max_steps` 约束。
- **但对同一 `tool_id` 的连续 retry 保留上限**：`pattern.call_tool` 维护一个 `ctx.scratch["__tool_retry_counts__"]: dict[tool_id, int]`，每次同一 tool 连续 raise `ModelRetryError` 自增；超过 `max_validation_retries` 后 `call_tool` 把它升级成 `PermanentToolError` 抛出，pattern 决定如何失败。不同 tool 之间的计数独立；模型在一次纠错后切换到另一个 tool 会重置前一个的计数。

### 3.4 `OutputValidationError`

新增在 `openagents/errors/exceptions.py`，挂在 `ExecutionError` 下：

```python
class OutputValidationError(ExecutionError):
    """Final output failed validation after max retries."""

    def __init__(
        self,
        message: str,
        *,
        output_type: type[BaseModel] | None = None,
        attempts: int = 0,
        last_validation_error: ValidationError | None = None,
    ): ...
```

挂在 `ExecutionError` 子树（与 `MaxStepsExceeded` / `BudgetExhausted` 同层），语义是「执行未能产出合规产物」。

### 3.5 `ModelRetryError` 导出

`ModelRetryError` 沿用 `LLMError` 子树（`0.2.0` spec 引入位置），本期在 `openagents/__init__.py` 显式导出，方便 tool 作者从 tool 代码里抛。

### 3.6 对 `run_stream()` 的联动

- 每次 `validation.retry` 事件 → 一条 `RunStreamChunkKind.VALIDATION_RETRY` chunk，payload `{"attempt": N, "error": "..."}`。
- `RUN_FINISHED` chunk 的 `result` 已经是 `RunResult[OutputT]`，`final_output` 是已校验对象或 `None`（失败时）。
- **已知限制**：streaming 下一次 run 内可能出现多段 `llm.delta` 序列（每次重试都会有）。消费方靠 `sequence` 单调递增 + `validation.retry` chunk 的 `attempt` 字段区分。见 §7.5.6。

### 3.7 测试策略

- `tests/unit/test_run_result_generic.py`：`RunResult[MyModel]` 泛型行为、`model_dump` / `model_validate` 正确。
- `tests/unit/test_pattern_finalize.py`：基类 `finalize` 对 None / 合规 / 不合规三种输入的行为。
- `tests/unit/test_validation_retry_loop.py`：mock provider 构造「前两次不合规，第三次合规」与「永远不合规」两种场景。
- `tests/unit/test_tool_model_retry.py`：tool 抛 `ModelRetryError` 的路径，断言 transcript 含 correction message、步数计入 `max_steps`、连续超限升级为 `PermanentToolError`。
- `tests/integration/test_structured_output_e2e.py`：`RunRequest(output_type=UserProfile)` 完整 end-to-end。

---

## 4. 成本跟踪与预算

### 4.1 Provider 层价格声明

`openagents/llm/base.py` 的 `LLMClient` 基类新增四个可选属性（USD per million tokens）：

```python
class LLMClient:
    price_per_mtok_input: float | None = None
    price_per_mtok_output: float | None = None
    price_per_mtok_cached_read: float | None = None   # 读 cache 的折扣价
    price_per_mtok_cached_write: float | None = None  # 首次缓存写入的加价（Anthropic）
```

Provider 子类内置静态价格表：

```python
# openagents/llm/providers/anthropic.py
_PRICE_TABLE: dict[str, dict[str, float]] = {
    "claude-opus-4-6":    {"in": 15.00, "out": 75.00, "cached_read": 1.50, "cached_write": 18.75},
    "claude-sonnet-4-6":  {"in":  3.00, "out": 15.00, "cached_read": 0.30, "cached_write":  3.75},
    "claude-haiku-4-5":   {"in":  0.80, "out":  4.00, "cached_read": 0.08, "cached_write":  1.00},
}
```

Provider `__init__` 按当前 `model_id` 查表；未命中保持 `None`。表允许过期（§7.5 风险 1）。

### 4.2 配置覆盖

`LLMOptions` 新增：

```python
class LLMPricing(BaseModel):
    input: float | None = None
    output: float | None = None
    cached_read: float | None = None
    cached_write: float | None = None


class LLMOptions(BaseModel):
    ...
    pricing: LLMPricing | None = None
```

合并规则（在共享 helper 实现，三个 provider 复用）：

1. 按 model id 查 provider 内置表 → 得 defaults。
2. 若 `options.pricing.<field>` 非 `None`，逐字段覆盖 defaults。
3. 最终仍为 `None` 的字段保持 `None`，成本计算跳过。

**不做 half-merge**：只支持「完全默认」或「per-field 显式覆盖」。

### 4.3 `RunUsage` 新字段

```python
class RunUsage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_cached: int = 0             # 从 cache 读到的 input token（Anthropic: cache_read_input_tokens；OpenAI: prompt_tokens_details.cached_tokens）
    input_tokens_cache_creation: int = 0     # 首次缓存写入的 input token（Anthropic: cache_creation_input_tokens）
    cost_usd: float | None = None            # 全 run 累计；任一次无法算时为 None
    cost_breakdown: dict[str, float] = Field(default_factory=dict)
```

`cost_usd` **保守累计语义**：只要有一次 LLM call 的 cost 为 `None`（缺价格表），整个 `RunUsage.cost_usd` 置 `None` 并 emit 一次性 `cost.unavailable` warning 事件。避免调用方误读部分累计值为「总成本」。

### 4.4 单次 LLM call 的成本计算

共享 helper：

```python
# openagents/llm/base.py
@dataclass
class LLMCostBreakdown:
    input: float = 0.0
    output: float = 0.0
    cached_read: float = 0.0
    cached_write: float = 0.0

    @property
    def total(self) -> float:
        return self.input + self.output + self.cached_read + self.cached_write


def compute_cost(
    *,
    input_tokens_non_cached: int,
    output_tokens: int,
    cached_read_tokens: int,
    cached_write_tokens: int,
    rates: LLMPricing,
) -> LLMCostBreakdown | None:
    ...
```

Provider 在产出 `LLMResponse` 时抽取 cached token 数：

- **Anthropic**：`usage.cache_read_input_tokens` / `cache_creation_input_tokens`。
- **OpenAI-compatible**：`usage.prompt_tokens_details.cached_tokens`。
- **Gemini 等未来 provider**：暂无；按 0 填。

任一 token 数对应的 rate 为 `None` → `compute_cost` 返回 `None`（不做 best-effort 部分成本）。

### 4.5 Pattern → RunUsage 的累计

`Pattern.call_llm()` 基类：

- 在现有 token 累计旁边加 cost 累计。
- 若本次 `response.usage.cost_usd is None`，`ctx.usage.cost_usd = None` 并 set `ctx.scratch["__cost_unavailable__"] = True`，后续不再累计 cost。

### 4.6 `max_cost_usd` 中心化执行

接入 `0.2.0` 的 budget enforcement 四层（tool_calls / duration / steps / cost）。

**检查点：**

1. **pre-call**（每次 `Pattern.call_llm()` 之前）：估算下限 = input token 数 × `price_per_mtok_input`（不预估 output，不预估 cache write）。若 `ctx.usage.cost_usd + 下限 > max_cost_usd` → `raise BudgetExhausted(kind="cost", ...)`。
2. **post-call**（每次 `call_llm` 之后）：若实际累计 > 上限 → 同样 raise。
3. **`cost_usd is None` 时** `max_cost_usd` 静默跳过，emit 一次性 `budget.cost_skipped` 事件（不当错误）。

**`BudgetExhausted` 扩展：**

```python
class BudgetExhausted(ExecutionError):
    kind: Literal["tool_calls", "duration", "steps", "cost"]
    current: float | int
    limit: float | int
```

`RunResult.stop_reason = BUDGET_EXHAUSTED`，`exception = BudgetExhausted(kind="cost", ...)`。

### 4.7 对 `run_stream()` 的联动

- 每次 `Pattern.call_llm()` 完成 → emit `usage.updated`，payload 为 `RunUsage` 快照。
- 映射到 `RunStreamChunkKind.LLM_FINISHED` 的 payload（已含 usage），不新增 chunk kind。

### 4.8 测试策略

- `tests/unit/test_llm_cost_compute.py`：`compute_cost` 的 None-传播语义。
- `tests/unit/test_run_usage_aggregation.py`：多次 `call_llm` 的累计；中途一次 `None` 后续 None-sticky。
- `tests/unit/test_cost_budget_enforcement.py`：pre-call / post-call 两条路径；`cost_usd is None` 时跳过检查。
- `tests/unit/test_anthropic_cached_tokens.py`：mock Anthropic 响应含 `cache_read_input_tokens` / `cache_creation_input_tokens`，断言被抽取。
- `tests/unit/test_openai_cached_tokens.py`：类似，针对 `prompt_tokens_details.cached_tokens`。
- `tests/unit/test_pricing_config_override.py`：配置覆盖的 per-field 合并规则。

---

## 5. Context Assembler 重做

### 5.1 改名

`openagents/plugins/builtin/context/summarizing.py` → `truncating.py`；类名 `SummarizingContextAssembler` → `TruncatingContextAssembler`。

### 5.2 Token 预算基础设施

`openagents/llm/base.py` 新增：

```python
class LLMClient:
    ...

    def count_tokens(self, text: str) -> int:
        """Count tokens using provider-native tokenizer.

        Default: len(text) // 4 with a one-time WARN per client instance.
        Providers override with real tokenizer.
        """
        if not getattr(self, "_count_tokens_warned", False):
            logger.warning(
                "LLMClient.count_tokens fallback (len//4) active for %s/%s; "
                "token budgets will be approximate.",
                self.provider_name, self.model_id,
            )
            self._count_tokens_warned = True
        return max(1, len(text) // 4)
```

- **OpenAI-compatible provider**：tiktoken 可用时走原生，否则 fallback。
- **Anthropic provider**：**Phase 1 统一 fallback + warn**（不强引入 `anthropic` SDK 依赖，真原生 tokenizer 留给 Phase 2）。
- **Mock provider**：保持 `len // 4`，测试稳定。

tiktoken 加入 optional 组：

```toml
[project.optional-dependencies]
tokenizers = [
    "tiktoken>=0.7.0",
]
```

### 5.3 共享基类与三个策略

```python
# openagents/plugins/builtin/context/base.py
class TokenBudgetContextAssembler(ContextAssemblerPlugin):
    """Shared base for token-aware truncating assemblers."""

    def __init__(self, config):
        super().__init__(...)
        self._max_input_tokens   = int(config.get("max_input_tokens", 8000))
        self._max_artifacts      = int(config.get("max_artifacts", 10))
        self._reserve_for_response = int(config.get("reserve_for_response", 2000))

    def _measure(self, llm_client, msg: dict) -> int:
        return llm_client.count_tokens(msg.get("content", "") or "")

    def _trim_by_budget(self, llm_client, msgs, budget) -> tuple[list, int]:
        """Return (kept_msgs, omitted_count). Strategy-specific override."""
        raise NotImplementedError
```

**`TruncatingContextAssembler`**（5.1 改名后的 holder）  
按消息条数截尾。不依赖 `count_tokens`。给「不想管 token 预算、只想快」的用户。

**`HeadTailContextAssembler`**  
保留前 N 条（通常 system + 早期 user task）+ 结尾按预算保留若干条。中间切断处塞 `role=system` 的 `"Summary: omitted K message(s), ~T tokens"`（纯 token 统计，非 LLM 总结）。

**`SlidingWindowContextAssembler`**  
FIFO 丢前面直到满足预算。不保留开头；适合对话类（其中 system prompt 由 pattern 通过 `compose_system_prompt` 另外注入）。

**`ImportanceWeightedContextAssembler`**  
按预算保留，优先级：
1. 第一条 `role=system`（基线指令）
2. 最近一次 `role=user`
3. 最近一次 `role=tool`（tool 结果最相关）
4. 剩余预算给其它最近消息

算完选中集合后按原始顺序重新排列。

三个实现都只读 `session_manager.load_messages()` 的现有数据，不调 LLM、不做 embedding，Phase 1 零新依赖。

### 5.4 Artifact 裁剪

三个策略共享 artifact 裁剪（保留最近 `max_artifacts` 个）—— 沿用现有 truncation 逻辑。Artifact 不做 token 预算，按数量截；消费者是 pattern/tool 而非 prompt。

### 5.5 Config 兼容错误

```python
# config/schema.py 的 PluginRef 校验层
if ref.type == "summarizing" and ref.kind == "context_assembler":
    raise ConfigValidationError(
        "context_assembler type 'summarizing' was renamed to 'truncating' in 0.3.0 "
        "because the old implementation only truncated without summarizing. "
        "Rename to 'truncating', 'head_tail', 'sliding_window', "
        "or 'importance_weighted'; or set impl= to your own LLM-based summarizer."
    )
```

**不做别名静默转换。** 用户必须改 config，让变更可见。

### 5.6 `ContextAssemblyResult.metadata` 补全

```python
{
    "assembler": "sliding_window",
    "strategy": "sliding_window",
    "budget_input_tokens": 8000,
    "kept_tokens": 7821,
    "omitted_messages": 42,
    "omitted_tokens": 15234,
    "omitted_artifacts": 3,
    "token_counter": "tiktoken" | "anthropic" | "fallback_len//4",
}
```

`token_counter` 字段使下游（pattern、观测）能判断当前 context 是真 budget 还是 fallback 估算。

### 5.7 测试策略

- `tests/unit/test_truncating_assembler.py`：现有行为回归。
- `tests/unit/test_head_tail_assembler.py`：边界（预算 > total、预算只容一条、两端重叠）。
- `tests/unit/test_sliding_window_assembler.py`：FIFO 顺序、预算边界。
- `tests/unit/test_importance_weighted_assembler.py`：多种消息组合，断言关键消息被保留。
- `tests/unit/test_llm_count_tokens.py`：三个 provider 的 `count_tokens`；fallback 仅 warn 一次。
- `tests/unit/test_config_summarizing_rename_error.py`：旧 `type: "summarizing"` 的迁移指引错误。
- `tests/integration/test_context_assembly_token_budget.py`：mock provider + 预制 transcript 跑三种策略，断言 metadata。

---

## 6. CLI

### 6.1 入口与包结构

```
openagents/
├── __main__.py           # 新：python -m openagents → cli.main()
└── cli/
    ├── __init__.py
    ├── main.py           # argparse 派发
    ├── schema_cmd.py
    ├── validate_cmd.py
    └── list_plugins_cmd.py
```

```toml
[project.scripts]
openagents = "openagents.cli.main:main"
```

`main.py` 用标准库 `argparse`，**不引入 click / typer**。退出码：0 成功 / 1 用户错误 / 2 配置错误 / 3 系统错误。

### 6.2 `openagents schema`

```
openagents schema [--plugin NAME] [--seam SEAM] [--format json|yaml] [--out PATH]
```

- 无参数：dump `AppConfig.model_json_schema()` 到 stdout（JSON, indent=2）。
- `--plugin NAME`：dump 该插件的 config schema（见 §6.5）；未声明则 stderr 报错、退出 2。
- `--seam SEAM`（取值：`tool / memory / pattern / runtime / session / events / skills / tool_executor / execution_policy / context_assembler / followup_resolver / response_repair_policy`）：dump 该 seam 下所有已注册插件 config schema 的集合。
- `--format yaml`：PyYAML 可用时 dump YAML；否则报错指向 `pip install io-openagent-sdk[yaml]`。
- `--out PATH`：写文件而非 stdout。

**实现**：合并 `registry.list_registered(seam)` + `decorators.list_*()`；不实例化，只走 class-level 反射。

### 6.3 `openagents validate`

```
openagents validate PATH [--strict] [--show-resolved]
```

- 走 `load_config(path)` 完整 pipeline：JSON 解析 → Pydantic 校验 → `validator.validate_config()`。
- 成功：`OK: <path> is valid (N agents, M seams configured)`，exit 0。
- 失败：
  - `ConfigLoadError`：stderr 打印原错误 + 行号提示，exit 2。
  - `ConfigValidationError`：stderr 打印 Pydantic 错误树，exit 2。
- `--strict`：额外 dry-run loader class 查找（不实例化）。未注册插件 → `unresolved plugin: <name>`，exit 2。
- `--show-resolved`：校验通过后打印 `AppConfig.model_dump()`。

**不做**：LLM provider 连接、session 创建、skills 包加载。

### 6.4 `openagents list-plugins`

```
openagents list-plugins [--seam SEAM] [--source builtin|decorator|all] [--format table|json]
```

遍历所有 seam 的 registry（builtin + decorator），输出每行 `{seam, name, source, impl_path, has_config_schema}`。`table` 为默认人读格式，无 ANSI 色；`json` 为机读。

### 6.5 插件 Config schema 约定

```python
class MyTool(ToolPlugin):
    class Config(BaseModel):
        timeout_ms: int = 30000
        allow_domains: list[str] = []

    def __init__(self, config=None):
        cfg = self.Config.model_validate(config or {})
        super().__init__(config=cfg.model_dump(), capabilities=set())
        self._cfg = cfg
```

- 有 `Config` 内部类（或类属性 `Config: type[BaseModel]`）的插件 → `schema --plugin` / `--seam` 能 dump。
- 没有的插件 → `list-plugins` 显示 `(no schema declared)`；不报错（不强求所有插件都加）。

**Phase 1 范围内要求加 `Config` 的 builtin 插件**：

- `SafeToolExecutor`
- `FilesystemPolicy`
- `TruncatingContextAssembler` / `HeadTailContextAssembler` / `SlidingWindowContextAssembler` / `ImportanceWeightedContextAssembler`
- `BufferMemory` / `WindowBufferMemory` / `ChainMemory`
- `BasicFollowupResolver` / `BasicResponseRepairPolicy`
- `AsyncEventBus`
- `InMemorySession`
- `LocalSkillsManager`
- `DefaultRuntime`
- `ReactPattern` / `PlanExecutePattern` / `ReflexionPattern`
- `HttpOpsTool` / `FileOpsTool` / `SystemOpsTool`（高安全边界 tool）

其它 builtin tool（math / text / datetime / random / network）本期不强求。

### 6.6 测试策略

- `tests/unit/test_cli_schema.py`：各参数组合；退出码；`--plugin` 不存在；`--format yaml` 未装 PyYAML 的错误提示。
- `tests/unit/test_cli_validate.py`：合法 / JSON 语法错 / Pydantic 错 / `--strict` 未注册插件。`tmp_path` 写 fixture JSON。
- `tests/unit/test_cli_list_plugins.py`：默认 / 过滤 / json 格式。
- `tests/unit/test_plugin_config_schemas.py`：遍历所有 §6.5 要求的 builtin，断言 `.Config` 存在且能 `model_json_schema()`。
- `tests/integration/test_cli_smoke.py`：`subprocess.run([sys.executable, "-m", "openagents", ...])` 端到端；断言 stdout + exit code。

### 6.7 非目标

- `openagents run` / `replay` / `scaffold` → Phase 3。
- shell completion → Phase 3 或永不做。
- 任何可写入仓库的命令 → Phase 3。
- 国际化 → 不做，CLI 输出全英文。

---

## 7. 迁移、依赖、发布节奏、风险

### 7.1 依赖变更

**核心依赖不变：**

```toml
dependencies = [
    "httpx[http2]>=0.28.1",
    "pydantic>=2.0",
]
```

**新增 optional 组：**

```toml
[project.optional-dependencies]
tokenizers = [
    "tiktoken>=0.7.0",
]
yaml = [
    "pyyaml>=6.0",
]
all = [
    "io-openagent-sdk[mcp,mem0,openai,dev,tokenizers,yaml]",
]
```

### 7.2 Breaking change 清单

**Kernel protocol**：

- `RunResult` → `RunResult[OutputT]`（泛型；旧调用方等价 `RunResult[Any]`）。
- `RunRequest` 新增 `output_type: type[BaseModel] | None = None`（非 breaking 字段）。
- `RunBudget` 新增 `max_validation_retries` / `max_cost_usd`（非 breaking 字段）。
- `RunUsage` 新增 `input_tokens_cached` / `input_tokens_cache_creation` / `cost_usd` / `cost_breakdown`（非 breaking 字段）。
- `PatternPlugin` 新增 `async def finalize(self, raw, output_type)`（基类带默认；自定义 pattern 要支持 output_type 时需覆盖）。

**插件合约**：

- `LLMClient` 新增 `price_per_mtok_*` 四属性 + `count_tokens(text)`（基类带默认，非 breaking）。
- `ToolExecutionSpec` 新增 `supports_streaming: bool = False`（非 breaking）。

**配置（breaking）**：

- `context_assembler.type = "summarizing"` 拒绝加载，必须改为 `"truncating"` / `"head_tail"` / `"sliding_window"` / `"importance_weighted"`。

**事件总线（新增、非 breaking）**：

- 新事件名：`llm.delta`、`usage.updated`、`validation.retry`、`budget.cost_skipped`、`tool.retry_requested`。
- 现有事件名保持不变。

**Python API（新增）**：

- `openagents.Runtime.run_stream(request)`；sync 对等 `stream_agent*`。
- `openagents.RunStreamChunk`、`RunStreamChunkKind`。
- `openagents.OutputValidationError`、`openagents.ModelRetryError`（后者首次显式导出）。
- `openagents.cli.main`（内部入口，不承诺稳定）。

### 7.3 迁移指南交付物

与 `0.3.0` 同步交付：

- **`docs/migration-0.2-to-0.3.md`**（中英双语），覆盖：
  - 「只调 `run_detailed`、无自定义 pattern」→ 0 改动。
  - 「有自定义 pattern 但无 output_type 需求」→ 0 改动。
  - 「配置里有 `summarizing` context_assembler」→ 必须改名或换策略。
  - 「想加结构化输出」→ 示例。
  - 「想看 cost」→ 示例配置 + 读 `RunResult.usage.cost_usd`。
  - 「自定义 LLM provider」→ 是否声明价格 / count_tokens。
- **`CHANGELOG.md`** `0.3.0` 条目（breaking / added / deprecated 三段）。
- **`README.md` / `README_EN.md` / `README_CN.md`** 的 "Key public contracts" 节：增补 `RunResult[OutputT]` / `RunStreamChunk` / `RunUsage.cost_usd` / `output_type`。
- **`docs/developer-guide.md`** 的「一次 run 的主流程」步骤列表：插入 `pattern.finalize → 校验 → 若失败且预算未尽则重入 execute` 和「每次 llm call 后汇总 cost、检查预算」。
- **`docs/seams-and-extension-points.md`**：不改（不新增 seam）。
- **`docs/configuration.md`**：新字段文档。
- **`docs/api-reference.md`**：新 API 文档。
- **`docs/plugin-development.md`**：`Config: type[BaseModel]` 约定、`count_tokens` 覆盖、`pattern.finalize` 覆盖示例。
- **`examples/quickstart/run_demo.py`** 升级演示结构化输出。
- **`examples/production_coding_agent/`** 保持原样 + 新增 `run_stream_demo.py`。

### 7.4 发布节奏（PR 拆分）

每个 PR 独立可合并、独立过测试、独立保 coverage ≥ 90%。顺序：

1. **基础设施**：`RunStreamChunk` 模型、`OutputValidationError`、`RunUsage` / `RunBudget` 新字段、`LLMClient.count_tokens` 基类、`price_per_mtok_*` 基类属性。纯加字段、无行为变更。
2. **成本计算**：`compute_cost` helper、三个 provider 的价格表与 cached-token 抽取、`Pattern.call_llm` 累计、`max_cost_usd` 检查点、`budget.cost_skipped` 事件。
3. **结构化输出**：`RunResult` 泛型化、`RunRequest.output_type`、`Pattern.finalize` 基类 + 三个 builtin pattern 覆盖、runtime 重试循环、`OutputValidationError`、`max_validation_retries` 执行。
4. **Tool 校验重试**：`pattern.call_tool` 捕获 `ModelRetryError`、`tool.retry_requested` 事件。
5. **流式输出**：`stream_projection.py` 映射表、`Runtime.run_stream`、sync `stream_agent*`、`Pattern.call_llm/call_tool` 的 streaming 分支、`ToolExecutionSpec.supports_streaming`。
6. **Context assembler 重做**：改名 + 拒绝 `summarizing` + 三个新策略 + `count_tokens` 各 provider 实现。
7. **CLI**：`openagents/cli/*`、`__main__.py`、`pyproject.toml` scripts、指定 builtin 插件的 `Config: type[BaseModel]`。
8. **文档 + 示例 + CHANGELOG + 版本号 → `0.3.0`**。

### 7.5 已知风险与限制

1. **价格表会过期**：Provider 内置 `_PRICE_TABLE` 需 SDK 维护者定期随模型价格调整。Spec 显式声明「价格表仅为 demo 方便，生产务必用 `llm.pricing` 覆盖」。
2. **`max_cost_usd` 的 pre-call 下限估算可能低估**：cache write 成本在 pre-call 时无法预知。接受该偏差、文档化。
3. **`max_validation_retries` 不保证总成本不超预算**：重试会继续累计 cost。文档显式推荐同时设 `max_cost_usd` + `max_validation_retries`。
4. **fallback token counter (`len//4`) 估算粗糙**：`ContextAssembler` fallback 下预算 vs 实际偏差典型 ±40%。`ContextAssemblyResult.metadata.token_counter` 必须暴露供下游判断。
5. **`run_stream()` 不重播历史事件**：消费方必须在 run 开始前订阅；中途连接不保证拿到之前的 chunk。
6. **Streaming 路径下 `ModelRetryError` 校验重试会产生重复的 `llm.delta` chunk 序列**：第 N 次 delta 序列完成 → finalize 失败 → 第 N+1 次序列开始。消费方靠 `sequence` 单调递增 + `validation.retry` chunk 的 `attempt` 字段区分。

### 7.6 SDK 非目标（再次强调）

- 任何新 seam。
- 任何静默 fallback（延续 `0.2.0` 的 no-silent-failure 原则）。
- 多 agent 编排、graph runtime、middleware stack。
- 任何把产品语义塞进 kernel 的字段（产品语义仍由现有 carrier 承载）。

---

## 8. 版本

- 版本号：`0.2.0` → `0.3.0`。
- 发布姿态：coherent cut，不做渐进兼容层（与 `0.2.0` 一致）。
- 预计覆盖：单元 + 集成测试覆盖率维持 ≥ 90%；所有新代码与测试共演（`AGENTS.md`）。
