# 示例说明

当前仓库只保留两组维护中的 example。

这不是“缩水”，而是把仓库收回到真实、可跑、可测的维护面，避免文档继续引用已经删除的历史目录。

除特别说明外，这两组 example 都默认使用 MiniMax 的 Anthropic-compatible 接口，
需要 `MINIMAX_API_KEY`。

## 怎么选

- 第一次跑仓库
  - 先看 `quickstart`
- 想看一个高设计密度、贴近真实应用分层的例子
  - 看 `production_coding_agent`
- 想学自定义 plugin / seam
  - 先读 [插件开发](plugin-development.md)
  - 再看 `tests/fixtures/` 和 `examples/production_coding_agent/app/`

## `examples/quickstart/`

用途：

- 最小 builtin-only setup
- 第一次确认 kernel 能跑

关键文件：

- `examples/quickstart/agent.json`
- `examples/quickstart/run_demo.py`

展示内容：

- `window_buffer`
- `react`
- builtin search tool
- 同一个 session 下连续运行

运行：

```bash
uv run python examples/quickstart/run_demo.py
```

相关验证：

```bash
uv run pytest -q tests/integration/test_runtime_from_config_integration.py
```

## `examples/production_coding_agent/`

用途：

- 演示一个高设计密度、production-style 的 coding agent
- 展示“SDK seam + app-defined protocol”如何一起工作
- 展示严格的本地验证路径

关键文件：

- `examples/production_coding_agent/agent.json`
- `examples/production_coding_agent/run_demo.py`
- `examples/production_coding_agent/run_benchmark.py`
- `examples/production_coding_agent/app/`
- `examples/production_coding_agent/workspace/`
- `examples/production_coding_agent/outputs/`

展示内容：

- task packet assembly
- persistent coding memory
- filesystem boundary
- safe tool execution
- local follow-up semantics
- structured delivery artifacts
- benchmark-style local evaluation harness

它不是在宣称“本地测完就能直接投入市场”，而是在示范：

- 一个可成长的 coding agent 应该怎样分层
- 什么该放 seam
- 什么该放 app protocol
- 怎样把验证写成可复现的集成测试

运行：

```bash
uv run python examples/production_coding_agent/run_demo.py
```

Benchmark：

```bash
uv run python examples/production_coding_agent/run_benchmark.py
```

相关验证：

```bash
uv run pytest -q tests/integration/test_production_coding_agent_example.py
```

## 如果你想学自定义扩展

虽然当前 repo 不再保留一堆独立 demo 目录，但”怎么自定义”并没有消失，主要参考面是：

- `tests/fixtures/custom_plugins.py`
- `tests/fixtures/runtime_plugins.py`
- `tests/unit/test_plugin_loader.py`
- `tests/unit/test_runtime_orchestration.py`
- `examples/production_coding_agent/app/`
- `openagents/plugins/builtin/tool_executor/filesystem_aware.py` — filesystem 执行策略示例（`FilesystemAwareToolExecutor`，展示 `evaluate_policy()` 的结构）
- `openagents/plugins/builtin/pattern/react.py` — `ReActPattern` 源码，展示 `resolve_followup()` 和 `repair_empty_response()` 的实际调用点

## 推荐阅读顺序

如果你想按最有效的顺序熟悉当前仓库，推荐：

1. `quickstart`
2. `production_coding_agent`
3. [插件开发](plugin-development.md)
4. [仓库结构](repository-layout.md)

## 运行集成测试

所有维护中示例都有配套的集成测试：

```bash
# 运行全部集成测试
uv run pytest -q tests/integration/
```

## research_analyst

该示例展示 post-seam-consolidation（2026-04-18）的扩展方式如何在一个真实任务里串起来。

| 机制 | 实现位置 | 作用 |
| --- | --- | --- |
| 自定义 `tool_executor` | `examples/research_analyst/app/executor.py::SandboxedResearchExecutor` | 继承 `SafeToolExecutor`，覆写 `evaluate_policy()`：内嵌 `CompositePolicy` AND-组合 filesystem + network allowlist；`execute()` 委托给 `RetryToolExecutor(inner=SafeToolExecutor)` 实现重试 + 超时 |
| pattern 子类 + `resolve_followup()` 覆写 | `FollowupFirstReActPattern`（`examples/research_analyst/app/followup_pattern.py`）| 继承 builtin `ReActPattern`，加载 `followup_rules.json` 后在 `resolve_followup()` 里做 regex → 模板本地解析；builtin `ReActPattern.execute()` 会先调用它短路 LLM |
| `session` | builtin `jsonl_file` | 全部 transcript / artifact / checkpoint 落盘到 `sessions/<sid>.jsonl`；重启后可重放 |
| `events` | builtin `file_logging` | 所有事件追加到 `sessions/events.ndjson`，便于审计 |

pattern 层用的是 `FollowupFirstReActPattern`（`examples/research_analyst/app/followup_pattern.py`），
只重写 `resolve_followup()` 即可。builtin `ReActPattern.execute()` 负责在 LLM loop 之前调用它。
与旧 seam 不同，follow-up 的调用点现在由 kernel 内部负责而不是 app 层显式调用。

### 注意事项

- **`HttpRequestTool` 对 5xx 不抛**：工具内部吞掉了 HTTP 错误码并返回 `{"success": false, "error": "..."}`，`SafeToolExecutor` 不会看到异常——所以"503 → 重试"走不通。示例的 stub 改为前两次 **sleep** 超过执行器超时，才真正触发 `ToolTimeoutError`，让 `retry` builtin 实际生效。
- **ReAct 每轮只调一次工具**：builtin `react` pattern 一轮只允许一个 tool_call；多工具编排需要在 app 层的 pattern 里自己做。

## pptx-agent（生产级 PPT 生成 CLI）

位置：`examples/pptx_generator/`。7 阶段交互式向导（意图 → 环境 → 研究 → 大纲 → 主题 → 切片 → 编译QA），基于 Rich + questionary 的 TUI，默认通过 Tavily MCP 联网研究。

- 安装：`uv add "io-openagent-sdk[pptx]"`
- 运行：`pptx-agent new --topic "..."` 或 `pptx-agent resume <slug>`
- 查看已保存的用户偏好：`pptx-agent memory list`
- 删除偏好：`pptx-agent memory forget <id>`
- 回放已完成的运行：`openagents replay outputs/<slug>/events.jsonl`（每次 `new` / `resume` 都会落盘 NDJSON 事件流，敏感字段自动脱敏）
- 详细 CLI 说明：[`docs/pptx-agent-cli.md`](pptx-agent-cli.md)（[EN](pptx-agent-cli.en.md)）

7 阶段的交互细节——意图逐字段编辑、大纲增删改重排、主题 3–5 候选图库 + 自定义编辑器、切片 schema 校验-重试 ≤2-fallback 到 freeform、跨会话偏好写回——详见 CLI 指南。

**MCP 连接模式选型**：研究阶段一次 agent run 会对同一个 Tavily MCP server 发起多次工具调用。把 `mcp` 工具的 `config.connection_mode` 从默认的 `per_call` 改成 `pooled`，可以让 N 次调用只 fork 一次 node 子进程，大幅降低延迟与资源占用。对外部服务不稳定或经常崩溃的场景，仍然建议保持 `per_call` 以利用 cancel-scope 隔离。对应的 `connection_mode: "pooled"` 示例：

```json
{
  "id": "tavily_mcp",
  "type": "mcp",
  "config": {
    "server": {"command": "npx", "args": ["-y", "tavily-mcp"]},
    "connection_mode": "pooled",
    "probe_on_preflight": true
  }
}
```

开启 `probe_on_preflight` 后，`mcp` 未安装 / `npx` 不在 PATH / tavily-mcp 启动就崩溃等问题会在 agent 第一轮循环之前就被 runtime 翻译成 `stop_reason=failed` 的 `RunResult`，避免"LLM 先规划了再失败"的浪费。

## 继续阅读

- [开发者指南](developer-guide.md)
- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [API 参考](api-reference.md)
