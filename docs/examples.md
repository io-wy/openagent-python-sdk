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

虽然当前 repo 不再保留一堆独立 demo 目录，但“怎么自定义”并没有消失，主要参考面是：

- `tests/fixtures/custom_plugins.py`
- `tests/fixtures/runtime_plugins.py`
- `tests/unit/test_plugin_loader.py`
- `tests/unit/test_runtime_orchestration.py`
- `examples/production_coding_agent/app/`

## 推荐阅读顺序

如果你想按最有效的顺序熟悉当前仓库，推荐：

1. `quickstart`
2. `production_coding_agent`
3. [插件开发](plugin-development.md)
4. [仓库结构](repository-layout.md)

## research_analyst

该示例展示 0.3.x 新增的 7 个 builtin 在一个真实任务里怎么串起来。

| seam | builtin | 作用 |
| --- | --- | --- |
| `tool_executor` | `retry` | 把 `safe` 包一层；`/pages/flaky` 前两次 sleep 超过 200ms 超时 → `ToolTimeoutError` → 自动重试，第三次成功返回 |
| `execution_policy` | `composite` + `filesystem` + `network_allowlist` | `composite` AND-组合文件根白名单和网络 host 白名单 |
| `followup_resolver` | `rule_based` | 第二轮"你刚才查了哪些 URL"通过 regex → 模板本地解析，不打模型（`FollowupFirstReActPattern` 里显式调 `ctx.followup_resolver.resolve(...)`）|
| `session` | `jsonl_file` | 全部 transcript / artifact / checkpoint 落盘到 `sessions/<sid>.jsonl`；重启后可重放 |
| `events` | `file_logging` | 所有事件追加到 `sessions/events.ndjson`，便于审计 |
| `response_repair_policy` | `strict_json` | 模型返回 markdown fenced JSON 时从文本里抽出 JSON；失败可 fallback 到 `basic` |

pattern 层用的是 `FollowupFirstReActPattern`（`examples/research_analyst/app/followup_pattern.py`）——kernel **不会**自动调用 `followup_resolver.resolve()`，这是 app-layer 的显式选择，参见 `docs/seams-and-extension-points.md` 关于 "followup_resolver 由 pattern 调用" 的说明。

### 注意事项

- **`HttpRequestTool` 对 5xx 不抛**：工具内部吞掉了 HTTP 错误码并返回 `{"success": false, "error": "..."}`，`SafeToolExecutor` 不会看到异常——所以"503 → 重试"走不通。示例的 stub 改为前两次 **sleep** 超过执行器超时，才真正触发 `ToolTimeoutError`，让 `retry` builtin 实际生效。
- **ReAct 每轮只调一次工具**：builtin `react` pattern 一轮只允许一个 tool_call；多工具编排需要在 app 层的 pattern 里自己做。

## 继续阅读

- [开发者指南](developer-guide.md)
- [Seam 与扩展点](seams-and-extension-points.md)
- [配置参考](configuration.md)
- [插件开发](plugin-development.md)
- [API 参考](api-reference.md)
