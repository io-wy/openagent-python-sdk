# Examples

当前 repo 只保留两组维护中的 example：

1. `quickstart`
2. `production_coding_agent`

这样能保证 examples、docs 和 tests 三者保持一致，不再漂到已经删除的历史目录上。

## 环境变量

这两组 example 默认都使用 MiniMax 的 Anthropic-compatible 接口，需要：

- `MINIMAX_API_KEY`

## 目录说明

### `quickstart/`

builtin-only 最小示例：

- builtin memory
- builtin pattern
- builtin search tool

运行：

```bash
uv run python examples/quickstart/run_demo.py
```

### `production_coding_agent/`

高设计密度、production-style coding agent 示例：

- task packet assembly
- persistent coding memory
- filesystem boundary
- safe tool execution
- local follow-up semantics
- delivery artifacts

运行：

```bash
uv run python examples/production_coding_agent/run_demo.py
```

验证：

```bash
uv run pytest -q tests/integration/test_production_coding_agent_example.py
```

Benchmark：

```bash
uv run python examples/production_coding_agent/run_benchmark.py
```

### `research_analyst/`

Offline research-agent example showing the post-2026-04-18 extension model in one place:

- `SandboxedResearchExecutor` (custom `tool_executor`) — overrides `evaluate_policy()` to
  AND-combine `FilesystemExecutionPolicy` + `NetworkAllowlistExecutionPolicy` via
  `CompositePolicy`, and its `execute()` delegates through `RetryToolExecutor(inner=SafeToolExecutor)`
  for timeout + exponential-backoff retry.
- `FollowupFirstReActPattern` (`ReActPattern` subclass) — overrides `resolve_followup()` with
  regex→template rule matching loaded from `followup_rules.json`; invoked automatically by
  builtin `ReActPattern.execute()` before the LLM loop.
- `jsonl_file` session manager (append-only NDJSON persistence under the `sessions` directory)
- `file_logging` event bus (wraps `async` + appends every event to `sessions/events.ndjson`)

Runs entirely against an in-process aiohttp stub server — no internet required. The stub's
`/pages/flaky` route sleeps past the safe executor's 200 ms timeout on the first two attempts,
so `ToolTimeoutError` fires for real and the retry executor actually retries.

```bash
uv run python examples/research_analyst/run_demo.py
```

Verify:

```bash
uv run pytest -q tests/integration/test_research_analyst_example.py
```

### `corecoder_agent/`

CoreCoder（[he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder.git)）的
SDK 实现版，演示如何用现有 seams 把 production-grade coding agent 的细节全部
塞到插件层：

- `CoreCoderPattern` — 直接驱动 `ctx.llm_client.generate(messages, tools)`
  的原生 Anthropic tool_use/tool_result 循环，绕开 `PatternPlugin.call_llm`
  的纯文本路径
- `CompressingContextAssembler` — 三层渐进式压缩（snip → LLM summarize →
  hard-collapse），`ContextAssemblyResult.metadata.layers_fired` 记录每轮触发
  的层
- `CoreCoderMemory` — 持久化 dirty-files 集合 + 上一次 cwd + 历史 summary，
  跨会话灌入 system prompt
- 7 个工具：`read_file` / `write_file` / `edit_file`（严格唯一性 search-replace）
  / `glob` / `grep` / `bash`（9 条 regex denylist）/ `sub_agent`（递归安全的
  sibling agent）

跟 `production_coding_agent` 是同一道题、不同档位：production_coding_agent 偏
保守演示，corecoder_agent 把 seam 拨满档展示真正可用的 coding agent。

运行：

```bash
cp examples/corecoder_agent/.env.example examples/corecoder_agent/.env
# 编辑 .env 填入 LLM_API_BASE / LLM_API_KEY / LLM_MODEL

uv run python examples/corecoder_agent/run_demo.py
```

测试：

```bash
uv run pytest -q tests/unit/examples/corecoder_agent/
```

## 配合文档一起看

建议配合：

- [docs/examples.md](../docs/examples.md)
- [docs/developer-guide.md](../docs/developer-guide.md)
- [docs/seams-and-extension-points.md](../docs/seams-and-extension-points.md)
- [docs/repository-layout.md](../docs/repository-layout.md)
