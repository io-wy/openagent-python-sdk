# Error Handling Tier 1: Codes, Retryable Attribute, Structured ErrorDetails

> Status: Draft · Owner: runtime kernel · Target: 0.4.0 breaking · Linked plans: TBD

## 摘要

完善 OpenAgent SDK 的错误处理系统第一阶段（Tier 1）：给每个 `OpenAgentsError` 子类挂稳定的 dotted `code` 和 `retryable` 类属性、给 rate-limit 类加 `retry_after_ms`、引入 `ErrorDetails` 序列化模型替代 `RunResult.error` / `RunResult.exception` 两个字段、让 `RetryToolExecutor` 和 durable resume 改读属性而不是硬编码列表、补齐 `docs/errors.md` 错误手册和 `docs/migration-0.3-to-0.4.md` 迁移指南。

本文档只覆盖 Tier 1。Tier 2（新增缺失的 typed errors、silent-swallow 审计、stream error bucket 扩展、结构化校验重试回灌）和 Tier 3（circuit breaker、ExceptionGroup、错误模式聚合）各自独立 spec。

## 动机

当前 SDK 的错误处理有不错的骨架（完整异常树、HTTP 传输层重试、diagnostics snapshot），但对比 Anthropic Python SDK、OpenAI Agents SDK、Pydantic AI、LangChain 后发现以下缺口：

1. 没有稳定 error code：消费者只能 `isinstance` 检查；跨进程序列化丢失类型信息。
2. 可重试分类两套机制各自为政：`RetryToolExecutor.retry_on: list[str]`（按类名字符串）、`DefaultRuntime.RETRYABLE_RUN_ERRORS`（硬编码 tuple）。用户无法让自定义异常类参与重试/resume。
3. `RunResult.error` 只是 `str(exc)`，丢失 `hint` / `docs_url` / 上下文字段；`RunResult.exception` 是运行时对象，不适合 HTTP API / SSE 客户端序列化。
4. `RetryToolExecutor` 纯指数退避无 jitter；`ToolRateLimitError` / `LLMRateLimitError` 没有 `retry_after_ms` 字段，即使 HTTP 层已解析 `Retry-After` 头。
5. 没有 `docs/errors.md` 错误手册。

## 非目标

- 不新增 seam（不做 error_policy / retry_policy 公共模块）。Tier 3 做 circuit breaker 时再评估。
- 不做错误模式聚合 / 失败归因 / 审计等可观测性升级（Tier 2/3）。
- 不补齐缺失的 typed errors（Tier 2）。
- 不扩展 `LLMChunk.error_type` bucket（Tier 2）。
- 不做 `BatchError(ExceptionGroup)`（Tier 3）。

## 方案

### § 1 架构：异常类 + ErrorDetails

#### 1.1 `OpenAgentsError` 根类扩展

`openagents/errors/exceptions.py`：

```python
class OpenAgentsError(Exception):
    code: ClassVar[str] = "openagents.error"
    retryable: ClassVar[bool] = False
    # 现有实例字段保持：hint / docs_url / agent_id / session_id / run_id / tool_id / step_number

    def to_dict(self) -> dict[str, Any]: ...
    # 返回 code / message / hint / docs_url / retryable / context
    # 注意：to_dict() 本身不序列化 __cause__。cause chain 的构造是
    # ErrorDetails.from_exception 的职责（§3.1），避免两处重复递归。
```

`message` 由 `str(self).splitlines()[0]` 产出 —— 去掉 `hint:` / `docs:` 格式化尾行。

`context` 聚合 5 个现有标识字段。rate-limit 子类把 `retry_after_ms` 也塞进 `context`（§1.3），让序列化消费者能统一从 `error_details.context["retry_after_ms"]` 读。

#### 1.2 子类 code + retryable 表

| 类 | code | retryable |
|---|---|---|
| `ConfigError` | `config.error` | False |
| `ConfigLoadError` | `config.load` | False |
| `ConfigValidationError` | `config.validation` | False |
| `PluginError` | `plugin.error` | False |
| `PluginLoadError` | `plugin.load` | False |
| `PluginCapabilityError` | `plugin.capability` | False |
| `PluginConfigError` | `plugin.config` | False |
| `ExecutionError` | `execution.error` | False |
| `MaxStepsExceeded` | `execution.max_steps` | False |
| `BudgetExhausted` | `execution.budget_exhausted` | False |
| `OutputValidationError` | `execution.output_validation` | False |
| `SessionError` | `session.error` | False |
| `PatternError` | `pattern.error` | False |
| `ToolError` | `tool.error` | False |
| `RetryableToolError` | `tool.retryable` | **True** |
| `PermanentToolError` | `tool.permanent` | False |
| `ToolTimeoutError` | `tool.timeout` | **True** |
| `ToolNotFoundError` | `tool.not_found` | False |
| `ToolValidationError` | `tool.validation` | False |
| `ToolAuthError` | `tool.auth` | False |
| `ToolRateLimitError` | `tool.rate_limit` | **True** |
| `ToolUnavailableError` | `tool.unavailable` | **True** |
| `ToolCancelledError` | `tool.cancelled` | False（cancel 是终态：由 `cancel_event` 或外部 CancelledError 触发，重试只会再次命中同一 cancel 信号） |
| `LLMError` | `llm.error` | False |
| `LLMConnectionError` | `llm.connection` | **True** |
| `LLMRateLimitError` | `llm.rate_limit` | **True** |
| `LLMResponseError` | `llm.response` | False |
| `ModelRetryError` | `llm.model_retry` | False（由 runtime validation loop 消费） |
| `UserError` | `user.error` | False |
| `InvalidInputError` | `user.invalid_input` | False |
| `AgentNotFoundError` | `user.agent_not_found` | False |

`ModelRetryError.retryable=False` 是有意的：RetryToolExecutor 不应捕获它，让它冒泡到 `DefaultRuntime` 的 finalize 校验重试循环（`default_runtime.py:844-879`）处理。

#### 1.3 Rate-limit 类新增 `retry_after_ms`

```python
class ToolRateLimitError(RetryableToolError):
    code = "tool.rate_limit"
    retry_after_ms: int | None

    def __init__(self, message, tool_name="", *, retry_after_ms=None, hint=None, docs_url=None):
        super().__init__(message, tool_name=tool_name, hint=hint, docs_url=docs_url)
        self.retry_after_ms = retry_after_ms

class LLMRateLimitError(LLMError):
    code = "llm.rate_limit"
    retryable = True
    retry_after_ms: int | None
    # 同样签名
```

`to_dict()` 把 `retry_after_ms` 放进 `context` dict（统一入口，不破 ErrorDetails schema）。

**运行时 vs 序列化消费者的读取路径**：
- `RetryToolExecutor._delay_for` 和 HTTP 层重试读 **属性** `exc.retry_after_ms`（in-process，避免绕一圈 dict）
- 序列化消费者（HTTP API / SSE 客户端 / trace exporter）读 `error_details.context["retry_after_ms"]`

两条路径读同一个源（实例字段），不是两份状态。

#### 1.4 `ErrorDetails` 序列化模型

`openagents/interfaces/runtime.py`：

```python
class ErrorDetails(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: str
    message: str
    hint: str | None = None
    docs_url: str | None = None
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)
    cause: "ErrorDetails | None" = None

    @classmethod
    def from_exception(cls, exc: BaseException, *, _depth: int = 0) -> "ErrorDetails":
        ...

ErrorDetails.model_rebuild()
```

`from_exception` 伪码（见 § 3）。

`RunResult` 字段变更（breaking）：

```python
class RunResult(BaseModel, Generic[OutputT]):
    run_id: str
    final_output: OutputT | None = None
    stop_reason: StopReason = StopReason.COMPLETED
    usage: RunUsage = Field(default_factory=RunUsage)
    artifacts: list[RunArtifact] = Field(default_factory=list)
    error_details: ErrorDetails | None = None   # 新增，替换 error + exception
    metadata: dict[str, Any] = Field(default_factory=dict)
```

移除：`RunResult.error: str | None`、`RunResult.exception: OpenAgentsError | None`。

### § 2 RetryToolExecutor + durable resume 改造

#### 2.1 `RetryToolExecutor`（`openagents/plugins/builtin/tool_executor/retry.py`）

```python
class Config(BaseModel):
    max_attempts: int = 3
    initial_delay_ms: int = 200
    backoff_multiplier: float = 2.0
    max_delay_ms: int = 5_000
    jitter: Literal["none", "full", "equal"] = "equal"  # 新
    # 删除：retry_on / retry_on_timeout

def _should_retry(self, exc: Exception | None) -> bool:
    return getattr(exc, "retryable", False) is True

def _delay_for(self, attempt: int, exc: Exception | None) -> int:
    base_ms = int(min(
        self._initial_delay_ms * (self._backoff ** attempt),
        self._max_delay_ms,
    ))
    floor_ms = int(getattr(exc, "retry_after_ms", 0) or 0)
    delay_ms = max(base_ms, floor_ms)
    if self._jitter == "full":
        return random.randint(0, delay_ms)
    if self._jitter == "equal":
        return delay_ms // 2 + random.randint(0, delay_ms // 2)
    return delay_ms  # "none"
```

Jitter 默认 `equal`（AWS 标准方案）。

#### 2.2 `DefaultRuntime`（`openagents/plugins/builtin/runtime/default_runtime.py`）

删除 line 78-83 的 `RETRYABLE_RUN_ERRORS` 常量。

line 882 改为：

```python
except OpenAgentsError as exc:
    if not request.durable or not exc.retryable:
        raise
    # ... 原 resume 逻辑不变
```

这让用户自定义的 `class MyRetryableError(OpenAgentsError): retryable = True` 自动参与 durable resume，不需要 monkey-patch。

#### 2.3 HTTP 传输层 `retry_after_ms` 透传（`openagents/llm/providers/_http_base.py`）

`_make_error_for_status` 接收 `retry_after_ms` 参数；`_request` / `_open_stream` 在"重试预算耗尽"分支把 `_parse_retry_after_seconds(...) * 1000` 传进去。

调用点改动示例：

```python
raise _make_error_for_status(
    url=url,
    status=last_status,
    body_excerpt=body_excerpt,
    retryable_status=retryable,
    retry_after_ms=int(retry_after * 1000) if retry_after is not None else None,
)
```

`LiteLLMClient._map_litellm_exception`：LiteLLM 的 `RateLimitError` 有时带 `retry_after`（version dependent），best-effort 读取并填充 `retry_after_ms`。

### § 3 数据流 + Runtime 装配

```
tool.invoke / llm.generate 抛 OpenAgentsError(code, retryable, retry_after_ms)
   │
   ├─ RetryToolExecutor._should_retry 读 exc.retryable
   │    │ yes
   │    ▼
   │  _delay_for 读 exc.retry_after_ms 作 sleep 下限
   │
   ├─ DefaultRuntime durable resume 读 exc.retryable
   │
   └─ DefaultRuntime.run 最终失败分支：
        │
        ├─ 非 OpenAgentsError → 包装成 PatternError（原 exc 保留为 __cause__）
        ├─ run_result.error_details = ErrorDetails.from_exception(wrapped_exc)
        ├─ 事件 payload 注入 error_details（dict）
        └─ DiagnosticsPlugin.capture_error_snapshot 新增 error_code 字段
```

#### 3.1 `ErrorDetails.from_exception` 伪码

```python
@classmethod
def from_exception(cls, exc: BaseException, *, _depth: int = 0) -> "ErrorDetails":
    MAX_DEPTH = 3
    if isinstance(exc, OpenAgentsError):
        data = exc.to_dict()
        result = cls(
            code=data["code"],
            message=data["message"],
            hint=data["hint"],
            docs_url=data["docs_url"],
            retryable=data["retryable"],
            context=dict(data["context"]),
        )
    else:
        msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        result = cls(code="error.unknown", message=msg)

    cause = exc.__cause__
    if (
        cause is not None
        and cause is not exc
        and _depth < MAX_DEPTH
    ):
        result.cause = cls.from_exception(cause, _depth=_depth + 1)
    return result
```

#### 3.2 事件 payload 变更

| 事件 | 当前字段 | 新增 | 保留（schema 向下兼容） |
|---|---|---|---|
| `run.failed` | `error: str` | `error_details: dict` | `error` |
| `tool.failed` | `tool_id, error` | `error_details: dict` | `error` |
| `llm.failed` | `model, error` | `error_details: dict` | `error` |
| `memory.inject.failed` | `error` | `error_details: dict` | `error` |
| `memory.writeback.failed` | `error` | `error_details: dict` | `error` |
| `run.checkpoint_failed` | `error, error_type` | `error_details: dict` | `error`, `error_type` |
| `run.resume_attempted` | `error_type: str` | `error_code: str` | `error_type` |
| `run.resume_exhausted` | `error_type: str` | `error_code: str` | `error_type` |

字段通过 `event_taxonomy.EVENT_SCHEMAS` 追加声明；消费者可继续读旧字段，新订阅者应读 `error_details`。旧字段在 Tier 2 评估是否废弃。

**为何事件保留旧字段而 `RunResult` 做硬切换**：
- `RunResult` 是进程内对象，`AttributeError` 立即抛出，用户迁移反馈强；且消费点集中（runtime / tests），数量可控。
- 事件订阅者可能是外部 SSE 客户端、OTel exporter、日志聚合器等；事件 taxonomy 是持久化 wire 格式，硬切换的 blast radius 远大于进程内对象。Tier 1 以新增字段为主，在 Tier 2 单独评估废弃旧事件字段的节奏（给订阅者一个 release 周期警告）。

#### 3.3 DiagnosticsPlugin snapshot

`ErrorSnapshot` dataclass 新增：

```python
@dataclass
class ErrorSnapshot:
    ...
    error_type: str         # 保留：type(exc).__name__
    error_code: str | None  # 新增：exc.code 或 "error.unknown"
```

`capture_error_snapshot` 生成 `error_code`：

```python
error_code = getattr(exc, "code", None)
if error_code is None:
    error_code = "error.unknown"
```

Phoenix / Langfuse / Rich diagnostics 三个 plugin 同步读 `snapshot.error_code` 进 traces（新一条属性，不替换）。

### § 4 文档

#### 4.1 `docs/errors.md`（中文主）+ `docs/errors.en.md`（英文镜像）

结构：

```
# 错误参考
## 总览表（code / 类 / retryable / docs_url）
## config.*
  ### config.load
  ### config.validation
## plugin.*
## execution.*
## session.*
## pattern.*
## tool.*（含 retry_after_ms 说明）
## llm.*（含 retry_after_ms 说明）
## user.*
## 自定义错误类（如何声明 code + retryable）
```

每个条目格式：
- 触发场景
- `retryable`
- 典型 hint
- 推荐处理
- 相关事件

#### 4.2 `docs/migration-0.3-to-0.4.md`

列出 breaking 点与映射：

- `RunResult.error` → `RunResult.error_details.message`
- `RunResult.exception` → 从事件或 diagnostics snapshot 读取
- `RetryToolExecutor.Config.retry_on` / `retry_on_timeout` → 删除（改读 `exc.retryable`）
- `DefaultRuntime.RETRYABLE_RUN_ERRORS` 常量 → 删除

代码迁移片段示例（before/after），至少 3 个典型场景。

### § 5 测试策略

#### 5.1 新增测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/unit/errors/test_codes.py` | 每个子类 `code` 非空、dotted、全局唯一；`retryable` 与表对齐 |
| `tests/unit/errors/test_to_dict.py` | `to_dict()` 字段齐全；cause 递归 1/2/3 层；循环引用安全；非 OpenAgentsError 回退 `error.unknown` |
| `tests/unit/errors/test_retry_after.py` | `ToolRateLimitError(retry_after_ms=5000)` 字段；`LLMRateLimitError` 同步 |
| `tests/unit/interfaces/test_run_result.py` | `RunResult.error_details` 字段；`ErrorDetails.from_exception` round-trip；`error` / `exception` 已移除 |
| `tests/unit/runtime/test_error_details_emission.py` | 运行时失败路径 → `run.failed` payload 含 `error_details`；snapshot `error_code` 与 `result.error_details.code` 一致 |
| `tests/unit/runtime/test_durable_resume_retryable_attribute.py` | 自定义 retryable 子类参与 resume；非 retryable 直接 raise |
| `tests/unit/llm/providers/test_retry_after_propagation.py` | httpx mock 返回 429 + `Retry-After: 5`，最终 `LLMRateLimitError.retry_after_ms == 5000` |
| `tests/unit/docs/test_errors_md_coverage.py` | 解析 `docs/errors.md` **和** `docs/errors.en.md`，分别断言每个 OpenAgentsError 子类的 code 都至少出现一次（防中英文档独立漂移） |

#### 5.2 修改已有测试

- `tests/unit/errors/test_exceptions.py`：扩展 `test_new_error_types_are_importable_from_package_surface` 等测试，加 `code` / `retryable` 断言
- `tests/unit/plugins/builtin/tool/test_error_taxonomy.py`：每个子类 code 检查
- `tests/unit/plugins/builtin/tool_executor/test_retry.py`：jitter 三种模式（monkeypatch `random`）；retry_after_ms 作为 sleep 下限；无 `retry_on` 配置时属性驱动
- 所有读 `result.error` / `result.exception` 的测试：改读 `result.error_details`

#### 5.3 覆盖率

- pyproject `fail_under = 90` 维持
- `openagents/errors/`、`openagents/plugins/builtin/tool_executor/retry.py`、`openagents/llm/providers/_http_base.py` 三热点 ≥95%
- `docs/errors.md` 对照测试作为"文档不漂移"闸门

## 影响面清单

| 文件 | 变动类型 | 摘要 |
|---|---|---|
| `openagents/errors/exceptions.py` | 修改 | 每类加 `code` / `retryable` ClassVar；`OpenAgentsError.to_dict()`；rate-limit 加 `retry_after_ms` |
| `openagents/errors/__init__.py` | 修改 | 导出 `ErrorDetails`（或在 `interfaces` 导出 —— 倾向 interfaces） |
| `openagents/interfaces/runtime.py` | 修改 | 新增 `ErrorDetails`；`RunResult` 字段 breaking 替换 |
| `openagents/interfaces/diagnostics.py` | 修改 | `ErrorSnapshot` 新增 `error_code` 字段；`capture_error_snapshot` 生成 |
| `openagents/interfaces/event_taxonomy.py` | 修改 | 声明 error_details / error_code 事件字段 |
| `openagents/plugins/builtin/runtime/default_runtime.py` | 修改 | 删除 `RETRYABLE_RUN_ERRORS`；改读 `exc.retryable`；失败路径写 `error_details`；事件 payload 注入 `error_details` |
| `openagents/plugins/builtin/tool_executor/retry.py` | 修改 | 删 retry_on/retry_on_timeout；加 jitter；读 `exc.retryable` / `exc.retry_after_ms` |
| `openagents/plugins/builtin/diagnostics/phoenix_plugin.py` | 修改 | 写入 `error_code` trace 属性 |
| `openagents/plugins/builtin/diagnostics/langfuse_plugin.py` | 修改 | 同上 |
| `openagents/plugins/builtin/diagnostics/rich_plugin.py` | 修改 | 同上 |
| `openagents/llm/providers/_http_base.py` | 修改 | `_make_error_for_status` 接收 `retry_after_ms` 并透传 |
| `openagents/llm/providers/litellm_client.py` | 修改 | `_map_litellm_exception` best-effort 读 `retry_after` |
| `docs/errors.md` | 新增 | 错误手册（中文） |
| `docs/errors.en.md` | 新增 | 错误手册（英文） |
| `docs/migration-0.3-to-0.4.md` | 新增 | 迁移指南 |
| `docs/developer-guide.md` / `.en.md` | 修改 | 链向错误手册 + 迁移指南 |
| `tests/unit/errors/test_codes.py` | 新增 | |
| `tests/unit/errors/test_to_dict.py` | 新增 | |
| `tests/unit/errors/test_retry_after.py` | 新增 | |
| `tests/unit/interfaces/test_run_result.py` | 新增/扩展 | |
| `tests/unit/runtime/test_error_details_emission.py` | 新增 | |
| `tests/unit/runtime/test_durable_resume_retryable_attribute.py` | 新增 | |
| `tests/unit/llm/providers/test_retry_after_propagation.py` | 新增 | |
| `tests/unit/docs/test_errors_md_coverage.py` | 新增 | |
| `tests/unit/errors/test_exceptions.py` | 修改 | code / retryable 断言 |
| `tests/unit/plugins/builtin/tool/test_error_taxonomy.py` | 修改 | 每子类 code 检查 |
| `tests/unit/plugins/builtin/tool_executor/test_retry.py` | 修改 | jitter + retry_after_ms |
| 所有读 `result.error` / `result.exception` 的已有测试 | 修改 | 改读 `error_details` |

估算：源码 ~450 行增/改，测试 ~650 行增/改，文档 ~350 行新写。

## 风险 & 缓解

| 风险 | 缓解 |
|---|---|
| Breaking 点被下游用户忽略 | migration 指南明确 before/after 示例；`RunResult.error` / `exception` 字段的 pydantic 移除会让 `result.error` 访问直接 `AttributeError`，不是静默 None —— 失败响直接 |
| jitter 随机性导致单测不稳定 | Config `jitter="none"` 用于单测；或 monkeypatch `random.randint` |
| docs/errors.md 漂移 | `test_errors_md_coverage.py` 作为 CI 闸门 |
| `ModelRetryError.retryable=False` 被误解为"永远不能重试" | `docs/errors.md` 在 `llm.model_retry` 条目明确说明：由 `DefaultRuntime` finalize 校验循环消费，不经过 tool executor retry；RetryToolExecutor `retryable=False` 是为了避免双重重试 |
| cause chain 递归到超长或循环 | 深度上限 3 + `cause is not exc` 循环保护 + non-OpenAgentsError 回退 |
| LiteLLM `retry_after` 字段跨版本兼容 | best-effort `getattr(exc, "retry_after", None)`；读不到就 None，不 break |

## 验收标准

1. 所有 OpenAgentsError 子类声明了唯一的 dotted code 和正确的 retryable 属性
2. `OpenAgentsError.to_dict()` 单测全覆盖；`ErrorDetails.from_exception` 支持 3 层 cause chain
3. `RetryToolExecutor` 不再读 `retry_on` 字符串列表；用户自定义 `retryable=True` 子类被自动捕获
4. `DefaultRuntime.RETRYABLE_RUN_ERRORS` 常量删除；durable resume 读 `exc.retryable`
5. `LLMRateLimitError` 从 429 + Retry-After 响应构造时 `retry_after_ms` 字段正确
6. `RunResult.error_details` 字段在成功 run 为 None、失败 run 为 `ErrorDetails` 实例
7. `run.failed` / `tool.failed` / `llm.failed` 事件 payload 含 `error_details` dict
8. `docs/errors.md` / `docs/errors.en.md` / `docs/migration-0.3-to-0.4.md` 完整
9. `test_errors_md_coverage.py` 通过
10. 总体测试覆盖率 ≥90%，修改热点 ≥95%
11. 所有现有测试读 `result.error` / `result.exception` 的地方已迁移到 `error_details`
