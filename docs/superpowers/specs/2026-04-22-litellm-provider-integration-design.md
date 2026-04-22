# LiteLLM 作为可选 LLM Provider 的集成设计

- **Date**: 2026-04-22
- **Status**: Proposed
- **Author**: brainstorming session
- **Scope**: `openagents/llm/providers/`, `openagents/llm/registry.py`, `openagents/config/schema.py`, `pyproject.toml`, docs

## 1. 动机与定位

本 SDK 当前支持 `anthropic`、`openai_compatible`、`mock` 三个 LLM provider。其中 `openai_compatible` 已能覆盖任何遵循 OpenAI ChatCompletion 协议的后端(Groq、Together、Fireworks、DeepSeek、MiniMax、Ollama、vLLM 等)。

LiteLLM 的**独占价值**在于 OpenAI 协议无法覆盖的后端:**AWS Bedrock、Google Vertex AI、Gemini 原生 API、Cohere、Azure OpenAI deployment 语义**。这些后端使用各自的 SDK(boto3、vertexai、google-generativeai),无法通过 `openai_compatible` 的 HTTP 层对接。

因此本次变更新增 `litellm` 作为可选 provider,通过 LiteLLM Python SDK 直连这些后端。

### 非目标

- **不**暴露 LiteLLM 的 `Router` / fallback / budget manager / 内置缓存 —— 这些属于 app 层产品语义,与 CLAUDE.md "don't push product semantics into the kernel" 原则冲突。
- **不**集成 LiteLLM Proxy 模式 —— 那条路用户可直接用现有 `openai_compatible` + proxy 的 `api_base` 对接。
- **不**将 `litellm` 加入默认依赖,仅作为 `[litellm]` extra。

## 2. 架构

### 2.1 模块布局

```
openagents/llm/providers/
├── _http_base.py          (已有)
├── anthropic.py           (已有)
├── openai_compatible.py   (已有)
├── mock.py                (已有)
└── litellm_client.py      (新增,~300-400 行,规模参考 anthropic.py)
```

新模块 `litellm_client.py` 定义 `LiteLLMClient(LLMClient)`,仅依赖 `litellm.acompletion` / `litellm.token_counter`,**不**复用 `_http_base.py`(LiteLLM 有自己的传输层)。

### 2.2 职责边界

| 做 | 不做 |
| --- | --- |
| `generate()` / `complete_stream()` | `Router` / fallback / 多后端负载均衡 |
| 工具调用反序列化(`LLMToolCall`) | LiteLLM 内置缓存 / budget manager |
| 错误分类(4 档 `LLMChunkErrorType`) | 自建 `_RetryPolicy` 重试(传导给 LiteLLM) |
| `count_tokens`(调 `litellm.token_counter`) | 自动注入默认依赖 |
| 禁用 LiteLLM telemetry / callbacks | 新增 example 目录 |
| 沿用本 SDK `_compute_cost_for` pricing 路径 | 调 `litellm.completion_cost`(保持 provider 对称) |

### 2.3 重试策略单向传导

本 SDK 的 `_RetryPolicy` 与 LiteLLM 内置重试互斥,避免乘法爆炸。约定:
- `LLMRetryOptions.max_attempts - 1` → 透传为 LiteLLM `num_retries` kwarg
- `retry_on_connection_errors=True` → 透传为 LiteLLM `retry_policy`(连接错误走指数退避)
- 本 SDK 层**不再**对 LiteLLM 调用做二次重试包装

## 3. 配置

### 3.1 Schema 变更(最小 diff)

`openagents/config/schema.py::LLMOptions._validate_llm_rules`:
```python
allowed = {"anthropic", "mock", "openai_compatible", "litellm"}
```

**不新增字段**。LiteLLM 的 provider 专属 kwarg(`aws_region_name`、`vertex_project`、`azure_deployment` 等)通过 `LLMOptions.model_config = ConfigDict(extra="allow")` 承载 —— 这是现有机制,无需改动。

### 3.2 字段语义

| 字段 | 行为 |
| --- | --- |
| `model` | 必填,**必须带 LiteLLM 前缀**,例如 `"bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"`、`"vertex_ai/gemini-1.5-pro"`、`"gemini/gemini-1.5-pro"` |
| `api_base` | 可选,透传给 `litellm.acompletion(api_base=...)`(Azure / Ollama / 私有部署需要) |
| `api_key_env` | 可选。给了就读 env 传为 `api_key` kwarg。**未给时**,SDK 不干预,LiteLLM 自行从 `AWS_*` / `VERTEXAI_PROJECT` / `GEMINI_API_KEY` 等标准 env 读取凭证链 |
| `extra_headers` | 透传 |
| `retry` | 按 §2.3 单向映射 |
| `pricing` | 参与本 SDK `_compute_cost_for`,不走 `litellm.completion_cost` |

### 3.3 Kwargs 白名单

`litellm_client.py` 顶层常量 `_FORWARDABLE_KWARGS`:
```python
_FORWARDABLE_KWARGS = frozenset({
    "aws_region_name", "aws_access_key_id", "aws_secret_access_key",
    "aws_session_token", "aws_profile_name",
    "vertex_project", "vertex_location", "vertex_credentials",
    "azure_deployment", "api_version",
    "seed", "top_p", "parallel_tool_calls", "response_format",
})
```

`registry.py` 的 `_extract_litellm_kwargs` helper 遍历 `LLMOptions` 的 extras,**只**摘白名单内的字段。未知 key 触发 `logger.warning("Unknown litellm kwarg '%s' in LLMOptions; ignored. Add to whitelist in litellm_client.py if needed.")`。

**永不透传**(即使用户通过 extra 强注入):`callbacks`、`success_callback`、`failure_callback`、`metadata`(覆盖语义)、`fallbacks`、`num_retries`(由 retry 映射独占)、`proxy_config`。

### 3.4 配置示例

```json
{
  "llm": {
    "provider": "litellm",
    "model": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    "aws_region_name": "us-east-1",
    "max_tokens": 4096,
    "pricing": {"input": 3.0, "output": 15.0}
  }
}
```

## 4. 协议翻译

### 4.1 请求映射

LiteLLM 输入即 OpenAI ChatCompletion 格式,`messages` / `tools` / `tool_choice` / `response_format` / `temperature` / `max_tokens` / `extra_headers` 全部透传。另外:
- `model` → `model`(必须带前缀,否则 `ConfigError`)
- `api_base` → `api_base`(若配置)
- `api_key_env` 解析后 → `api_key`(若能解析)
- `retry.max_attempts - 1` → `num_retries`
- `retry.retry_on_connection_errors` → `retry_policy`
- 白名单 extras → 同名 kwarg

### 4.2 非流式响应 → `LLMResponse`

| LiteLLM 字段 | `LLMResponse` 字段 |
| --- | --- |
| `choices[0].message.content` | `output_text` + `content=[{"type":"text","text":...}]` |
| `choices[0].message.tool_calls` | `tool_calls: list[LLMToolCall]`,`arguments` 经 `json.loads`;非法 JSON 保留 `raw_arguments` |
| `choices[0].finish_reason` | `stop_reason`(原样:`"stop"` / `"tool_calls"` / `"length"`) |
| `usage.prompt_tokens` | `usage.input_tokens` |
| `usage.completion_tokens` | `usage.output_tokens` |
| `usage.total_tokens` | `usage.total_tokens` |
| `usage.prompt_tokens_details.cached_tokens` **或** `usage.cache_read_input_tokens` | `usage.metadata["cache_read_input_tokens"]`(两种风格都读) |
| `id` | `response_id` |
| `model` | `model` |
| — | `provider = "litellm:<底层 provider>"`(动态,见 §4.4) |
| `dict(response)` | `raw` |

`response_format` 为 `json` 类型时,`base._parse_structured_output` 解析 `output_text` → `structured_output`。

最后走 `self._compute_cost_for(usage=..., overrides=pricing)`,metadata 注入 `cost_usd` / `cost_breakdown`。

### 4.3 流式响应 → `LLMChunk`

- `delta.content` 片段 → `LLMChunk(type="content_block_delta", delta=text)`
- `delta.tool_calls[*]` 增量 → `LLMChunk(type="content_block_delta", delta={"tool_use": {...}})`(结构与 `openai_compatible.py` 一致,arguments 片段累加)
- 流末尾从 `stream_options={"include_usage": True}` 带回的 usage chunk → `LLMChunk(type="message_stop", usage=LLMUsage(...))`
- 异常:按 §4.5 映射为 `LLMChunk(type="error", ...)`,yield 后终止

### 4.4 动态 `provider_name`

类默认 `provider_name = "litellm"`。`__init__` 解析 `model` 前缀:
- `"bedrock/..."` → `"litellm:bedrock"`
- `"vertex_ai/..."` → `"litellm:vertex_ai"`
- `"gemini/..."` → `"litellm:gemini"`
- `"azure/..."` → `"litellm:azure"`
- 无前缀 → `"litellm"`

用途:event / transcript 能看出真实后端,便于观测与问题定位。

### 4.5 错误映射

| LiteLLM 异常 | 非流式抛 | 流式 `error_type` |
| --- | --- | --- |
| `litellm.exceptions.RateLimitError` | `LLMRateLimitError` | `"rate_limit"` |
| `litellm.exceptions.APIConnectionError` | `LLMConnectionError` | `"connection"` |
| `litellm.exceptions.Timeout` | `LLMConnectionError` | `"connection"` |
| `litellm.exceptions.APIError`(含所有子类) | `LLMResponseError` | `"response"` |
| 未分类 | `LLMResponseError` | `"unknown"` |

### 4.6 count_tokens

`litellm.token_counter(model=self.model_id, text=text)` 优先;异常时降级到基类 `len // 4` 并 WARN 一次(基类已实现 `_count_tokens_warned` 幂等逻辑)。

### 4.7 aclose

LiteLLM 自管 httpx pool。实现调用 `await litellm.aclient_session.aclose()`(若属性存在),带 `try/except` 保护,保证幂等。

## 5. 注册与懒加载

### 5.1 `openagents/llm/registry.py` 新分支

```python
if provider == "litellm":
    from openagents.llm.providers.litellm_client import LiteLLMClient
    return LiteLLMClient(
        model=llm.model,
        api_base=llm.api_base,
        api_key_env=llm.api_key_env,
        timeout_ms=llm.timeout_ms,
        default_temperature=llm.temperature,
        max_tokens=llm.max_tokens or 1024,
        pricing=llm.pricing,
        retry_options=llm.retry,
        extra_headers=extra_headers,
        extra_kwargs=_extract_litellm_kwargs(llm),
    )
```

`_extract_litellm_kwargs(llm)` 遍历 `LLMOptions` 的 extras,按 `_FORWARDABLE_KWARGS` 白名单过滤,对未知 key 发 WARN。

### 5.2 模块级懒加载

`litellm_client.py` 顶部:
```python
try:
    import litellm  # type: ignore
except ImportError:  # pragma: no cover
    litellm = None
```

`LiteLLMClient.__init__` 首行检查:
```python
if litellm is None:
    raise ConfigError(
        "provider 'litellm' requires: pip install 'io-openagent-sdk[litellm]'"
    )
```

### 5.3 Telemetry 禁用(进程级幂等副作用)

`__init__` 中:
```python
litellm.telemetry = False
litellm.success_callback = []
litellm.failure_callback = []
litellm.drop_params = True  # 未知 kwarg 丢弃而非抛错,提升健壮性
```

类 docstring 明示:实例化 `LiteLLMClient` 会在进程级禁用 LiteLLM telemetry 与 callbacks。

## 6. pyproject & coverage

### 6.1 `pyproject.toml` diff

```toml
[project.optional-dependencies]
litellm = [
    "litellm>=1.50.0",
]
dev = [
    # ... 已有 ...
    "litellm>=1.50.0",  # 确保测试可跑
]
all = [
    "io-openagent-sdk[cli,mcp,mem0,openai,otel,rich,sqlite,dev,tokenizers,yaml,pptx,langfuse,phoenix,litellm]",
]

[tool.coverage.report]
omit = [
    # ... 已有 ...
    "openagents/llm/providers/litellm_client.py",
]
```

### 6.2 Coverage 权衡

- 与 `mem0_memory.py` / `mcp_tool.py` / `otel_bridge.py` 同规格 omit,保证 92% 底线在无 litellm 环境仍达标。
- 日常开发 CI `uv sync --extra dev` 会装 `litellm`,16 条单测全跑,只是不计入覆盖率门槛。

## 7. Docs 改动面

- `docs/configuration.md` / `configuration.en.md`:新增 "LiteLLM Provider(可选)" 小节,含 Bedrock / Vertex / Gemini 各一段 JSON 示例、白名单 kwargs 清单、禁用特性(router/fallback)说明。
- `docs/developer-guide.md` / `.en.md`:provider 列表加一行。
- `README.md` / `README.zh-CN.md`:provider 支持表加一列 + 一句 release note。
- **不**新增 example 目录(CLAUDE.md 约束:只维护 `quickstart` 和 `production_coding_agent`)。

## 8. 测试策略

### 8.1 测试文件

`tests/unit/test_litellm_provider.py`。

### 8.2 Stub 策略

- 不用 respx(LiteLLM 对 Bedrock/Vertex 走非 HTTP 的 SDK client)。
- **`monkeypatch.setattr("openagents.llm.providers.litellm_client.litellm.acompletion", fake)`** 与 `litellm.token_counter` 打桩。
- `fake_acompletion` 返回 `types.SimpleNamespace` 构造的响应对象,属性链对齐 OpenAI ChatCompletion。
- 聚焦**翻译层**而非 LiteLLM 内部;不发真实网络请求。

### 8.3 最小覆盖清单(16 条)

| # | 测试点 |
| --- | --- |
| 1 | `generate` 纯文本 → `LLMResponse.output_text` / `content[0]` / `usage` |
| 2 | `generate` 带 tools → `LLMToolCall(name, arguments=dict, raw_arguments=str)`;非法 JSON arguments 保留 raw |
| 3 | `generate` usage 含 `prompt_tokens_details.cached_tokens` 和 `cache_read_input_tokens` 两风格 → 都落进 `metadata["cache_read_input_tokens"]` |
| 4 | `generate` 带 `response_format={"type":"json_object"}` → `structured_output` 被解析 |
| 5 | `generate` 按 `model` 前缀设 `provider_name` 为 `"litellm:bedrock"` / `"litellm:vertex_ai"` / `"litellm:gemini"` / `"litellm"` |
| 6 | cost:pricing 给定时 `metadata["cost_usd"]` 非 None;缺失时 `cost_usd=None` |
| 7 | `complete_stream` yield 序列:N × `content_block_delta` + 1 × `message_stop(usage=...)` |
| 8 | `complete_stream` tool_call 增量:多 chunk arguments 片段拼接成合法 JSON |
| 9 | 非流式 4 种异常映射:`RateLimitError` / `APIConnectionError` / `Timeout` / `APIError` → `LLMRateLimitError` / `LLMConnectionError` / `LLMConnectionError` / `LLMResponseError` |
| 10 | 流式下同样 4 种异常 → `LLMChunk(error_type="rate_limit" / "connection" / "connection" / "response")` |
| 11 | `count_tokens` 调 `litellm.token_counter`;抛异常时降级 `len // 4` + 一次 WARN |
| 12 | `retry_options` 映射:`max_attempts=3` → `num_retries=2`;`retry_on_connection_errors=True` → `retry_policy=...` |
| 13 | extras 白名单:`aws_region_name` 透传;`fallbacks` / `callbacks` 即使注入也不透传 + WARN |
| 14 | `api_key_env` 指向的 env 缺失 → 不传 `api_key` kwarg(交给 LiteLLM 凭证链) |
| 15 | `__init__` 副作用:`litellm.telemetry == False`,两 callback 列表清空,`drop_params == True` |
| 16 | `aclose` 幂等:连调两次不抛 |

### 8.4 集成/回归层

- `tests/unit/test_llm_factory.py`(或等价文件):`provider="litellm"` 路径正常返回 `LiteLLMClient`。
- Schema 测试:`_validate_llm_rules` 接受 `"litellm"`,拒绝 `"litellmm"` 错字。
- ImportError 路径:`monkeypatch.setattr("openagents.llm.providers.litellm_client.litellm", None)` 后 `create_llm_client(...)` 抛 `ConfigError`,消息含 `"pip install"`。

### 8.5 不测的内容(避免 scope creep)

- 不测 LiteLLM 自身的 provider 行为(Bedrock / Vertex 真调用)。
- 不跑任何真实网络。

## 9. 变更分类(现有 / 已有未消费 / 真新增)

| 类别 | 内容 |
| --- | --- |
| **现有** | 插件注册机制、错误层级、`_RetryPolicy`、`LLMPricing` / `_compute_cost_for`、extras 模式 |
| **已有未消费** | `LLMOptions.model_config = extra="allow"`(承载 LiteLLM 专属 kwarg)、`_parse_structured_output`(结构化输出解析) |
| **真新增** | `LiteLLMClient` 类、`_extract_litellm_kwargs` helper、`registry.py` 新分支、`_validate_llm_rules` 白名单加项、`pyproject.toml` litellm / dev / all / coverage omit、docs 更新 |

## 10. 风险与权衡

| 风险 | 缓解 |
| --- | --- |
| LiteLLM 版本升级时字段名/异常类重命名 | `>=1.50.0` pin;`litellm_client.py` 集中所有 LiteLLM 字段访问,升级时只改一处 |
| Telemetry 进程级副作用影响其他进程内 LiteLLM 使用者 | docstring 明示;本 SDK 不期望用户在本进程混用原生 LiteLLM |
| 白名单漏掉常用 kwarg 导致用户无法用某后端 | WARN 提示"Add to whitelist in litellm_client.py if needed",PR 补 1 行即可 |
| LiteLLM 包体积大(拉 100+ SDK 依赖) | 仅 `[litellm]` extra 安装;默认用户不受影响 |
| Cost 由本 SDK 算,对 Bedrock / Vertex 小众模型需用户显式配 `pricing` | 一致性优先;文档示例配齐 Bedrock / Vertex 常用模型的 pricing |

## 11. 开放问题

无(所有决策点已在 brainstorming 中对齐)。
