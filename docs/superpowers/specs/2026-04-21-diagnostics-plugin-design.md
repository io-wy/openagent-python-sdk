# DiagnosticsPlugin — 可观测性增强设计

**日期**: 2026-04-21  
**范围**: 新增 `diagnostics` seam，覆盖错误诊断、LLM 指标采集、外部平台导出  
**优先级**: 开发调试体验（本地错误根因定位）+ AI 可观测性（LLM 质量指标）

---

## 背景与动机

项目现有可观测性基础扎实（事件总线 × 4 实现、29 个事件类型、完整日志系统、OTel Bridge），但存在以下明确差距：

| 差距 | 现状 | 目标 |
|------|------|------|
| 错误根因定位 | 仅 `RunResult.exception`，无调用链快照 | 失败时自动捕获完整现场 |
| LLM 延迟指标 | 无 TTFT、无 latency 分位数 | HTTP 层精确采集，填入 RunUsage |
| 外部平台导出 | 仅 OTLP（平铺 span，无层级） | Langfuse / Phoenix 原生集成 |
| OTel span 层级 | 一次性 span，无父子关系 | Phoenix 适配器修复为完整 trace tree |

---

## 架构概览

新增 `diagnostics` seam，与 `memory`、`execution_policy` 等现有 seam 平级。

```
DiagnosticsPlugin（接口）
├── ErrorDiagnostics   — 失败时捕获完整现场快照
├── MetricsCollector   — LLM HTTP 层计时 + 指标聚合
└── MetricsExporter    — 导出到 Langfuse / Phoenix（可配置）

内置实现（openagents/plugins/builtin/diagnostics/）
├── null_plugin.py        — NullDiagnosticsPlugin（默认，全 no-op）
├── rich_plugin.py        — 本地调试 Rich 面板
├── langfuse_plugin.py    — Langfuse 云平台导出（可选 extra）
└── phoenix_plugin.py     — Arize Phoenix 导出（可选 extra）
```

### 集成点（最小侵入）

| 文件 | 改动内容 |
|------|---------|
| `openagents/interfaces/diagnostics.py` | 新增接口 + 数据类 |
| `openagents/plugins/loader.py` | 注册 `diagnostics` seam 加载逻辑 |
| `openagents/llm/providers/_http_base.py` | 注入 timing，将 `LLMCallMetrics` 附加到事件 payload |
| `openagents/plugins/builtin/runtime/default_runtime.py` | 在失败和 run 结束时调用 DiagnosticsPlugin |
| `openagents/interfaces/runtime.py` — `RunUsage` | 新增 `ttft_ms`、`llm_latency_p50_ms`、`llm_latency_p95_ms`、`llm_retry_count` |

---

## 接口定义

```python
# openagents/interfaces/diagnostics.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class LLMCallMetrics:
    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    ttft_ms: float | None = None    # 流式响应才有值；非流式为 None
    attempt: int = 1                # 重试第几次（1 = 首次）
    error: str | None = None        # 如果失败，异常消息

@dataclass
class ErrorSnapshot:
    run_id: str
    agent_id: str
    session_id: str
    error_type: str                 # 异常类名
    error_message: str
    traceback: str
    tool_call_chain: list[dict]     # 按时序：每次 tool.called 的 {tool_id, params, call_id}
    last_transcript: list[dict]     # 最后 N 条 transcript 条目
    usage_at_failure: dict          # RunUsage.model_dump() 快照
    state_snapshot: dict            # RunContext.state 深拷贝（已脱敏）
    captured_at: str                # ISO 8601 UTC

class DiagnosticsPlugin:
    """diagnostics seam 基类，所有方法默认 no-op。"""

    DIAG_METRICS = "diagnostics.metrics"
    DIAG_ERROR   = "diagnostics.error"
    DIAG_EXPORT  = "diagnostics.export"

    def record_llm_call(self, metrics: LLMCallMetrics) -> None:
        """收到一次 LLM 调用的计时数据。"""

    def capture_error_snapshot(
        self, ctx: RunContext, exc: BaseException
    ) -> ErrorSnapshot:
        """在失败时构造并返回 ErrorSnapshot。"""

    def on_run_complete(
        self, result: RunResult, snapshot: ErrorSnapshot | None
    ) -> None:
        """run 结束时调用（成功或失败）。负责回填 RunUsage 并触发导出。"""

    def get_session_metrics(self) -> dict[str, Any]:
        """返回本次 session 累积的指标摘要（供调试查询）。"""
        return {}
```

---

## 错误诊断

### 触发机制

**触发点 1 — `tool.failed` 事件订阅（实时）**：`DiagnosticsPlugin` 初始化时订阅该事件。工具最终失败时立即记录当前 tool_call_chain 快照，RunContext 仍然有效。

**触发点 2 — `default_runtime.py` 顶层 except 块（运行结束）**：捕获到异常时调用 `capture_error_snapshot(ctx, exc)`，将 `ErrorSnapshot` 附加到 `RunResult.metadata["error_snapshot"]`。

### tool_call_chain 维护

`DiagnosticsPlugin` 内部订阅 `tool.called` 事件，按时序累积本次 run 的调用序列（`{tool_id, params, call_id}`）。`on_run_complete()` 结束后清空，下次 run 重新开始。不侵入 `RunContext`。

### 数据安全

`state_snapshot` 对 `RunContext.state` 先 `copy.deepcopy`，再调用 `openagents/observability/redact.py` 中已有的 `redact()` 函数脱敏（复用 `redact_keys` 配置）。

### 暴露方式

- `RunResult.metadata["error_snapshot"]`：序列化为 dict，调用方可直接取用
- 作为参数传入 `on_run_complete()`：导出器使用
- `RichDiagnosticsPlugin`：在 stderr 渲染完整错误面板

---

## LLM 指标采集

### 采集位置

`openagents/llm/providers/_http_base.py`，在已有 HTTP 请求封装层包裹 timing，不改动各 provider 业务逻辑。

### 采集逻辑

```python
t_start = time.monotonic()
first_chunk_time: float | None = None

# 流式：在 yield 第一个 chunk 前记录 TTFT
if streaming and first_chunk_time is None:
    first_chunk_time = time.monotonic()

t_end = time.monotonic()

metrics = LLMCallMetrics(
    model=model_id,
    ttft_ms=(first_chunk_time - t_start) * 1000 if first_chunk_time else None,
    latency_ms=(t_end - t_start) * 1000,
    input_tokens=usage.input_tokens,
    output_tokens=usage.output_tokens,
    cached_tokens=usage.input_tokens_cached,
    attempt=attempt_number,
)
```

### 解耦方式

`_http_base.py` 不持有 `DiagnosticsPlugin` 引用（避免循环依赖）。改为将 `LLMCallMetrics` 附加到 `llm.succeeded` / `llm.failed` 事件 payload 的 `_metrics` 字段。`DiagnosticsPlugin` 订阅这两个事件，从 payload 取出后调用 `record_llm_call()`。

### RunUsage 扩展

```python
class RunUsage(BaseModel):
    # 现有字段不变
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_cached: int = 0
    input_tokens_cache_creation: int = 0
    cost_usd: float | None = None
    cost_breakdown: dict[str, float] = {}
    # 新增字段
    ttft_ms: float | None = None             # 本次 run 首个 LLM 调用的 TTFT
    llm_latency_p50_ms: float | None = None  # 所有 LLM 调用延迟中位数
    llm_latency_p95_ms: float | None = None  # 95 分位延迟
    llm_retry_count: int = 0                 # 重试总次数（attempt > 1 的调用数）
```

`DiagnosticsPlugin` 在 `on_run_complete()` 时对本次 run 所有 `LLMCallMetrics.latency_ms` 排序计算 p50/p95，回填到 `RunResult.usage`。

---

## 导出适配器

### NullDiagnosticsPlugin（默认）

全部方法 no-op，无任何外部依赖，零运行时开销。

### RichDiagnosticsPlugin（本地调试）

- 依赖：`rich`（已有 optional extra）
- **成功时**：在 stderr 渲染紧凑 usage 面板（token 数、latency p50/p95、重试次数、cost）
- **失败时**：渲染完整错误面板（异常类型 + message + traceback + tool 调用链 + 最后 N 条 transcript）
- 不向任何外部系统发送数据

### LangfuseExporter（可选 extra `langfuse`）

- 依赖：`langfuse>=2.0`
- `on_run_complete()` 创建一条 Langfuse **trace**：
  - root span：`session.run`（含 input_text、stop_reason、usage）
  - 子 span：每个 LLM 调用（model、latency、token、TTFT）
  - 子 span：每个 tool 调用（tool_id、params、result / error）
  - 若有 `ErrorSnapshot`：附加为 trace 的 `metadata.error_snapshot`
- 配置参数：`public_key`、`secret_key`、`host`（支持自部署）

### PhoenixExporter（可选 extra `phoenix`）

- 依赖：`arize-phoenix-otel>=0.6`
- 复用 OTel 协议，构建正确 span 层级（修复现有 `OtelEventBusBridge` 的平铺问题）
- `on_run_complete()` 发送完整 trace tree 到 Phoenix collector
- 配置参数：`endpoint`（默认 `http://localhost:6006`）

---

## 配置

```yaml
# openagent_config.yaml

diagnostics:
  type: rich                   # null | rich | langfuse | phoenix
  error_snapshot_last_n: 10   # ErrorSnapshot 截取的 transcript 条数
  redact_keys:                 # 脱敏字段（叠加到全局 redact_keys）
    - api_key
    - token

# Langfuse 示例
diagnostics:
  type: langfuse
  public_key: "pk-lf-..."
  secret_key: "sk-lf-..."
  host: "https://cloud.langfuse.com"
  error_snapshot_last_n: 15

# Phoenix 示例
diagnostics:
  type: phoenix
  endpoint: "http://localhost:6006"
  error_snapshot_last_n: 10
```

---

## 测试策略

### 单元测试

```
tests/unit/interfaces/test_diagnostics.py
  — LLMCallMetrics / ErrorSnapshot 数据类构造与序列化

tests/unit/plugins/builtin/diagnostics/
  test_null_plugin.py      — 全部方法 no-op，get_session_metrics() 返回空字典
  test_rich_plugin.py      — 渲染不报错，stderr 有输出（mock Console）
  test_langfuse_plugin.py  — mock langfuse client，验证 trace 结构和 span 层级
  test_phoenix_plugin.py   — mock OTLP exporter，验证 span tree

tests/unit/observability/test_llm_metrics.py
  — TTFT 计算、p50/p95 聚合逻辑、retry_count 累计、RunUsage 回填
```

### 集成测试

```
tests/integration/test_diagnostics_integration.py
  — mock LLM + mock DiagnosticsPlugin，验证：
    1. tool.failed 触发 capture_error_snapshot
    2. ErrorSnapshot 正确附加到 RunResult.metadata["error_snapshot"]
    3. RunUsage 包含 llm_latency_p95_ms
    4. on_run_complete 被调用且参数类型正确
    5. NullDiagnosticsPlugin 不破坏现有 run 流程
```

### 覆盖规则

- `langfuse_plugin.py`、`phoenix_plugin.py` 加入 `pyproject.toml` 的 `omit` 列表（同 `otel_bridge.py`）
- `null_plugin.py`、`rich_plugin.py` 必须覆盖
- `test_runtime_orchestration.py` 补充 `diagnostics: null` 配置验证

---

## 与成熟框架的差距对照（实现后）

| 能力 | 实现前 | 实现后 |
|------|--------|--------|
| 错误现场快照 | ❌ | ✅ ErrorSnapshot + RunResult.metadata |
| TTFT 采集 | ❌ | ✅ HTTP 层精确计时 |
| LLM 延迟分位数 | ❌ | ✅ p50/p95 回填到 RunUsage |
| 重试率 | ❌ | ✅ llm_retry_count |
| Langfuse 集成 | ❌ | ✅ 原生 trace + span 层级 |
| Phoenix / OTel 层级 | ⚠️ 平铺 | ✅ 完整 trace tree |
| 本地调试面板 | ⚠️ 仅事件日志 | ✅ Rich 错误面板 |
| 外部依赖可选 | ✅ | ✅ 保持（optional extras） |
