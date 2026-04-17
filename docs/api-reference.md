# API 参考

这份文档总结当前最重要的 package exports、runtime surface，以及你真正应该关心的协议对象。

它不是源码替代品。  
它的作用是告诉你：**当前稳定 API 面到底在哪里。**

## 1. package exports

`openagents` 当前导出：

### Core 入口

- `AppConfig`
- `Runtime`
- `load_config`
- `load_config_dict`
- `run_agent`
- `run_agent_detailed`
- `run_agent_detailed_with_config`
- `run_agent_with_config`
- `run_agent_with_dict`

### Decorator

- `tool`
- `memory`
- `pattern`
- `runtime`
- `session`
- `event_bus`
- `tool_executor`
- `context_assembler`

### Registry accessors

- `get_tool`
- `get_memory`
- `get_pattern`
- `get_runtime`
- `get_session`
- `get_event_bus`
- `get_tool_executor`
- `get_context_assembler`

### Registry list helpers

- `list_tools`
- `list_memories`
- `list_patterns`
- `list_runtimes`
- `list_sessions`
- `list_event_buses`
- `list_tool_executors`
- `list_context_assemblers`

> Post 2026-04-18 seam-consolidation：`execution_policy` / `followup_resolver` /
> `response_repair_policy` 三套 decorator / registry 已移除。
> - tool 权限 → `ToolExecutorPlugin.evaluate_policy()`
> - follow-up → `PatternPlugin.resolve_followup()`
> - empty response repair → `PatternPlugin.repair_empty_response()`

## 2. Runtime facade

### `Runtime(config: AppConfig, _skip_plugin_load: bool = False, _config_path: Path | None = None)`

对外的 runtime facade。内部持有：

- app config
- 顶层 runtime / session / events 组件
- 按 session + agent 缓存的插件 bundle

### `Runtime.from_config(config_path: str | Path) -> Runtime`

从磁盘加载 JSON 配置，构造 runtime。

### `Runtime.from_dict(payload: dict[str, Any]) -> Runtime`

直接从 Python dict 构造 runtime。

### `await runtime.run(*, agent_id: str, session_id: str, input_text: str) -> Any`

兼容型入口，返回 `RunResult.final_output`。  
如果 run 失败，会抛异常。

### `await runtime.run_detailed(*, request: RunRequest) -> RunResult`

结构化入口。  
如果你在做更高层的 runtime / framework / product，优先用这个。

### `runtime.run_sync(*, agent_id: str, session_id: str, input_text: str) -> Any`

`run()` 的同步封装。

### `await runtime.reload() -> None`

重新加载最初的 config 文件。  
只更新 future run 会用到的 agent 定义，不热切换顶层组件。

### `await runtime.reload_agent(agent_id: str) -> None`

失效一个 agent 在各个 session 下的缓存 bundle。

### `runtime.get_session_count() -> int`

返回当前活跃 session 数量。

### `await runtime.list_agents() -> list[dict[str, Any]]`

返回最小 agent 信息列表，只含 `id` 和 `name`。

### `await runtime.get_agent_info(agent_id: str) -> dict[str, Any] | None`

返回：

- 该 agent 的 selector 配置
- 当前是否已有已加载的 plugin 实例

### `await runtime.close_session(session_id: str) -> None`

关闭一个 session 的插件 bundle。

### `await runtime.close() -> None`

关闭 runtime 及可关闭的下游资源。

### `runtime.event_bus`

属性，返回当前 event bus 实例。

### `runtime.session_manager`

属性，返回当前 session manager 实例。

## 3. Sync Helper

### `run_agent(config_path, *, agent_id, session_id="default", input_text) -> Any`

从文件路径加载配置并同步运行。

### `run_agent_with_config(config, *, agent_id, session_id="default", input_text) -> Any`

从预加载 config 同步运行。

### `run_agent_detailed(config_path, *, agent_id, session_id="default", input_text) -> RunResult`

从文件路径做同步 detailed run。

### `run_agent_detailed_with_config(config, *, agent_id, session_id="default", input_text) -> RunResult`

从预加载 config 做同步 detailed run。

### `run_agent_with_dict(payload, *, agent_id, session_id="default", input_text) -> Any`

直接从 Python dict 做同步运行。

## 4. 配置对象

### `AppConfig`

主要字段：

- `version: str`
- `agents: list[AgentDefinition]`
- `runtime: RuntimeRef`
- `session: SessionRef`
- `events: EventBusRef`

### `AgentDefinition`

主要字段：

- `id: str`
- `name: str`
- `memory: MemoryRef`
- `pattern: PatternRef`
- `llm: LLMOptions | None`
- `tool_executor: ToolExecutorRef | None`
- `context_assembler: ContextAssemblerRef | None`
- `tools: list[ToolRef]`
- `runtime: RuntimeOptions`

> `execution_policy` / `followup_resolver` / `response_repair_policy` 三个字段在
> 2026-04-18 seam 合并中移除；strict schema 会拒绝这些旧 key。

### `RuntimeOptions`

字段：

- `max_steps`
- `step_timeout_ms`
- `session_queue_size`
- `event_queue_size`

### `LLMOptions`

字段：

- `provider`
- `model`
- `api_base`
- `api_key_env`
- `temperature`
- `max_tokens`
- `timeout_ms`
- `stream_endpoint`
- `extra`

## 5. Runtime protocol

### `RunBudget`

单次 run 的可选限制：

- `max_steps`
- `max_duration_ms`
- `max_tool_calls`

### `RunArtifact`

run 产物：

- `name`
- `kind`
- `payload`
- `metadata`

### `RunUsage`

run 的 usage 聚合：

- `llm_calls`
- `tool_calls`
- `input_tokens`
- `output_tokens`
- `total_tokens`

### `RunRequest`

结构化输入：

- `agent_id`
- `session_id`
- `input_text`
- `run_id`
- `parent_run_id`
- `metadata`
- `context_hints`
- `budget`
- `deps`

### `RunResult`

结构化输出：

- `run_id`
- `final_output`
- `stop_reason`
- `usage`
- `artifacts`
- `error`
- `exception`
- `metadata`

### `StopReason`

取值：

- `completed`
- `failed`
- `cancelled`
- `timeout`
- `max_steps`
- `budget_exhausted`

## 6. RunContext

`RunContext` 是 pattern 和 tool 真正消费的运行态对象。

主要字段：

- `agent_id`
- `session_id`
- `run_id`
- `input_text`
- `deps`
- `state`
- `tools`
- `llm_client`
- `llm_options`
- `event_bus`
- `memory_view`
- `tool_results`
- `scratch`
- `system_prompt_fragments`
- `transcript`
- `session_artifacts`
- `assembly_metadata`
- `run_request`
- `tool_executor`
- `usage`
- `artifacts`

> `execution_policy` / `followup_resolver` / `response_repair_policy` 属性在
> 2026-04-18 seam 合并中移除 —— 权限判断由 `tool_executor.evaluate_policy()` 负责，
> follow-up / empty-response 走 `PatternPlugin` 上的方法覆写。

这是 app-defined middle protocol 最重要的 carrier。

## 7. Tool execution protocol

### `ToolExecutionSpec`

执行元信息：

- `concurrency_safe`
- `interrupt_behavior`
- `side_effects`
- `approval_mode`
- `default_timeout_ms`
- `reads_files`
- `writes_files`

### `PolicyDecision`

policy 输出：

- `allowed`
- `reason`
- `metadata`

### `ToolExecutionRequest`

结构化 tool 执行输入：

- `tool_id`
- `tool`
- `params`
- `context`
- `execution_spec`
- `metadata`

### `ToolExecutionResult`

结构化 tool 执行输出：

- `tool_id`
- `success`
- `data`
- `error`
- `exception`
- `metadata`

## 8. Context assembly protocol

### `ContextAssemblyResult`

结构化 pre-run context：

- `transcript`
- `session_artifacts`
- `metadata`

## 9. Follow-up / response repair protocol

### `FollowupResolution`

字段：

- `status`
- `output`
- `reason`
- `metadata`

当前推荐状态：

- `resolved`
- `abstain`
- `error`

### `ResponseRepairDecision`

字段：

- `status`
- `output`
- `reason`
- `metadata`

当前推荐状态：

- `repaired`
- `abstain`
- `error`

## 10. Session protocol

### `SessionArtifact`

字段：

- `name`
- `kind`
- `payload`
- `metadata`

### `SessionCheckpoint`

字段：

- `checkpoint_id`
- `state`
- `transcript_length`
- `artifact_count`
- `created_at`

## 11. Plugin contract

### `ToolPlugin`

主要方法：

- `async invoke(params, context) -> Any`
- `async invoke_stream(params, context)`
- `execution_spec() -> ToolExecutionSpec`
- `schema() -> dict`
- `describe() -> dict`
- `validate_params(params) -> tuple[bool, str | None]`
- `get_dependencies() -> list[str]`
- `async fallback(error, params, context) -> Any`

### `ToolExecutorPlugin`

主要方法：

- `async execute(request) -> ToolExecutionResult`
- `async execute_stream(request)`

### `ExecutionPolicyPlugin`

主要方法：

- `async evaluate(request) -> PolicyDecision`

### `MemoryPlugin`

主要方法：

- `async inject(context) -> None`
- `async writeback(context) -> None`
- `async retrieve(query, context) -> list[dict[str, Any]]`
- `async close() -> None`

### `PatternPlugin`

主要方法：

- `async setup(...) -> None`
- `async execute() -> Any`
- `async react() -> dict[str, Any]`
- `async emit(event_name, **payload) -> None`
- `async call_tool(tool_id, params=None) -> Any`
- `async call_llm(...) -> str`
- `async compress_context() -> None`
- `add_artifact(...) -> None`

### `SkillsPlugin`

主要方法：

- `prepare_session(session_id, session_manager) -> dict[str, SessionSkillSummary]`
- `load_references(session_id, skill_name, session_manager) -> list[dict[str, str]]`
- `run_skill(session_id, skill_name, payload, session_manager) -> dict[str, Any]`

### `ContextAssemblerPlugin`

主要方法：

- `async assemble(request, session_state, session_manager) -> ContextAssemblyResult`
- `async finalize(request, session_state, session_manager, result) -> result`

### `FollowupResolverPlugin`

主要方法：

- `async resolve(context=...) -> FollowupResolution | None`

### `ResponseRepairPolicyPlugin`

主要方法：

- `async repair_empty_response(...) -> ResponseRepairDecision | None`

### `RuntimePlugin`

主要方法：

- `async initialize() -> None`
- `async validate() -> None`
- `async health_check() -> bool`
- `async run(...) -> RunResult`
- `async pause() -> None`
- `async resume() -> None`
- `async close() -> None`

### `SessionManagerPlugin`

主要方法：

- `async with session(session_id)`
- `async get_state(session_id) -> dict[str, Any]`
- `async set_state(session_id, state) -> None`
- `async delete_session(session_id) -> None`
- `async list_sessions() -> list[str]`
- `async append_message(session_id, message) -> None`
- `async load_messages(session_id) -> list[dict[str, Any]]`
- `async save_artifact(session_id, artifact) -> None`
- `async list_artifacts(session_id) -> list[SessionArtifact]`
- `async create_checkpoint(session_id, checkpoint_id) -> SessionCheckpoint`
- `async load_checkpoint(session_id, checkpoint_id) -> SessionCheckpoint | None`
- `async close() -> None`

### `EventBusPlugin`

主要方法：

- `subscribe(event_name, handler) -> None`
- `async emit(event_name, **payload) -> RuntimeEvent`
- `async get_history(event_name=None, limit=None) -> list[RuntimeEvent]`
- `async clear_history() -> None`
- `async close() -> None`

## 12. Registry helper

`get_*` helper 返回的是 decorator registry 里的类。  
`list_*` helper 返回的是 decorator registry 里的名称。

它们不是 builtin registry 的完整替代品。

## 13. Plugin authoring helpers

供自定义 combinator 与 pattern 作者使用的公开 helper。

| Symbol | Module | Purpose |
| --- | --- | --- |
| `load_plugin(kind, ref, *, required_methods=())` | `openagents.plugins.loader` | 公开的子插件加载入口，combinator (`memory.chain`, `tool_executor.retry`, `execution_policy.composite`, `events.file_logging`) 内部都走它 |
| `unwrap_tool_result(result) -> tuple[data, metadata \| None]` | `openagents.interfaces.pattern` | 把 `_BoundTool.invoke()` 返回的 `ToolExecutionResult` 解包成 `(data, executor_metadata)`；对 raw `ToolPlugin.invoke()` 返回值则直接 passthrough，metadata 为 `None` |
| `TypedConfigPluginMixin` | `openagents.interfaces.typed_config` | Mixin，提供基于嵌套 `Config(BaseModel)` 的 `self.cfg` 校验；未知键发 warning 而非报错 |

`openagents.plugins.loader._load_plugin` 仍保留为 deprecated 别名，
会发 `DeprecationWarning`。

## 14. 错误与诊断 helper（Spec B WP1 / WP2）

| Symbol | Module | Purpose |
| --- | --- | --- |
| `OpenAgentsError(message, *, hint=None, docs_url=None, ...)` | `openagents.errors.exceptions` | 基类异常；新增可选 `hint` / `docs_url`。`str(exc)` 在被设置时会多输出 `  hint: ...` / `  docs: ...` 行，首行保持原 message 不变 |
| `near_match(needle, candidates, *, cutoff=0.6)` | `openagents.errors.suggestions` | 轻量 "did you mean?" 包装，基于 `difflib.get_close_matches`；返回最近匹配或 `None` |
| `EVENT_SCHEMAS` | `openagents.interfaces.event_taxonomy` | 已声明事件名 → `EventSchema(name, required_payload, optional_payload, description)` 的字典。`AsyncEventBus.emit` 在缺少必需 key 时 `logger.warning`，从不 raise |
| `EventSchema` | `openagents.interfaces.event_taxonomy` | 单个事件 schema 的 frozen dataclass |
| `gen_event_doc.render_doc()` / `write_doc(target)` / `main(argv)` | `openagents.tools.gen_event_doc` | 从 `EVENT_SCHEMAS` 重新生成 `docs/event-taxonomy.md` 的 helper |

## 15. Optional builtin index（Spec C）

These builtins ship under `openagents/plugins/builtin/` but require an
optional extra to construct. Module import always succeeds; instantiation
without the extra raises `PluginLoadError` with an install hint.

| Class | Seam / type key | Module | Extra |
| --- | --- | --- | --- |
| `Mem0Memory` | `memory` / `mem0` | `openagents.plugins.builtin.memory.mem0_memory` | `mem0` |
| `McpTool` | `tool` / `mcp` | `openagents.plugins.builtin.tool.mcp_tool` | `mcp` |
| `SqliteSessionManager` | `session` / `sqlite` | `openagents.plugins.builtin.session.sqlite_backed` | `sqlite` |
| `OtelEventBusBridge` | `events` / `otel_bridge` | `openagents.plugins.builtin.events.otel_bridge` | `otel` |

Install with `uv sync --extra <name>` (or `uv sync --extra all`). Each
module is also added to `[tool.coverage.report] omit` in `pyproject.toml`
so the 92% coverage floor stays intact when the extra is not installed.

## 16. 继续阅读

- [开发者指南](developer-guide.md)
- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [示例说明](examples.md)
