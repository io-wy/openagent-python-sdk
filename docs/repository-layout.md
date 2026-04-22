# Repository Layout

这份文档只回答一个问题：当前仓库里每一层目录到底负责什么。

## Top Level

```text
openagent-py-sdk/
  README.md
  README_EN.md
  README_CN.md
  pyproject.toml
  uv.lock
  openagents/
  docs/
  examples/
  skills/
  tests/
```

## Directory Responsibilities

### `openagents/`

SDK 主源码。

主要包含：

- `config/` — config loader / schema / validator（`AppConfig` Pydantic 模型）
- `runtime/` — Runtime facade 和 DefaultRuntime；`stream_projection.py` 负责 event → `RunStreamChunk` 映射
- `plugins/` — builtin plugin registry 和 loader（`plugins/loader.py`）
- `plugins/builtin/` — 按 seam 分组的内置插件：`runtime/`, `session/`, `events/`, `skills/`, `memory/`, `pattern/`, `tool/`, `tool_executor/`, `execution_policy/`, `context/`, `followup/`, `response_repair/`
- `llm/providers/` — `anthropic`、`openai_compatible`、`mock` 三个 LLM client，共享 `_http_base.py`
- `interfaces/` — 稳定的 kernel protocol dataclass（`RunRequest`、`RunResult`、`RunContext`…）；`typed_config.py` 提供 `TypedConfigPluginMixin`；`event_taxonomy.py` 声明所有事件的 schema
- `observability/` — 结构化日志子系统：`LoggingConfig`、`configure()`、filters（`filters.py`）、rich 渲染器（`_rich.py`）、loguru 多 sink 后端（`_loguru.py`，可选 extra）、脱敏（`redact.py`）、错误格式化（`errors.py`）
- `cli/` — CLI 子命令实现：`schema_cmd.py`、`validate_cmd.py`、`list_plugins_cmd.py`；入口在 `__main__.py` / `main.py`
- `errors/` — 错误层级 + "did you mean?" 提示助手（`exceptions.py`、`suggestions.py`）
- `utils/` — `hotreload.py`（`Runtime.reload()` 的支撑），以及其他通用工具

### `docs/`

唯一的开发者文档树。

推荐入口：

- [docs/README.md](README.md)
- [docs/developer-guide.md](developer-guide.md)
- [docs/seams-and-extension-points.md](seams-and-extension-points.md)
- [docs/examples.md](examples.md)

其他关键文档：

- [docs/configuration.md](configuration.md) — JSON 配置参考
- [docs/plugin-development.md](plugin-development.md) — 插件开发指南
- [docs/api-reference.md](api-reference.md) — Python API 参考
- [docs/builtin-tools.md](builtin-tools.md) — 内置工具目录
- [docs/stream-api.md](stream-api.md) — 流式 API（`run_stream`）参考
- [docs/cli-reference.md](cli-reference.md) — CLI 命令参考（`openagents schema/validate/list-plugins`）
- [docs/observability.md](observability.md) — 结构化日志与可观测性
- [docs/event-taxonomy.md](event-taxonomy.md) — 事件分类表
- [docs/migration-0.2-to-0.3.md](migration-0.2-to-0.3.md) — 迁移指南

!!! note
    `docs/superpowers/` 是内部设计文档（spec / plan），不发布。

### `examples/`

当前仓库里维护中的可运行示例。

目前只保留两组：

- `quickstart/`
  - 最小 builtin-only kernel 运行入口
- `production_coding_agent/`
  - 高设计密度、app-defined protocol 风格示例

`examples/README.md` 负责例子导航，`docs/examples.md` 负责更完整的学习顺序和定位说明。

### `skills/`

App-layer skill 目录。当前包含：

- `skills/openagent-agent-builder/` — agent 构建辅助 skill；详见 [docs/openagent-agent-builder.md](openagent-agent-builder.md)

### `tests/`

验证当前 repo truth，而不是历史遗留结构。覆盖率门槛：**92%**（`pyproject.toml` `[tool.coverage.report].fail_under`）。

- `tests/unit/`
  - loader、runtime、provider、repo structure 等单元验证
  - `tests/unit/test_builtin_docstrings_are_three_section.py` — 回归守护：所有内置插件 docstring 必须是三段式 Google-style
- `tests/integration/`
  - config/example 级集成验证
- `tests/fixtures/`
  - 自定义插件样例（`custom_plugins.py`、`runtime_plugins.py`），同时也是插件开发参考

## Documentation Topology

为避免重复和漂移，当前文档分工固定为：

- `README.md`
  - 包入口、最短上手路径、导航
- `README_EN.md` / `README_CN.md`
  - 完整项目说明
- `docs/`
  - 开发文档和结构文档
- `examples/README.md`
  - 示例目录导航

## What Is Intentionally Absent

当前 repo 不再把下面这些历史表面当成现役结构：

- `docs-v2/`
- `openagent_cli/`
- 已删除的旧 example 目录

如果未来要恢复它们，应该以真实目录和真实测试一起恢复，而不是只留文档引用。
