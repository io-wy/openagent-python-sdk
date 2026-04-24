## Why

多 Agent seam 已在 2026-04-24 落地（`AgentRouterPlugin` + `DefaultAgentRouter` + `multi_agent` config），但 5 项 spec 承诺与实际行为不一致，构成生产阻塞：

1. **`shared` 模式会死锁** — 父持 session `asyncio.Lock`（非可重入），router 调 `Runtime.run_detailed` 让子去抢同 sid 的锁，永等
2. **`forked` 名不副实** — spec 承诺 "copies parent history snapshot"，实际只做 `{sess}:fork:{run}` 的命名拼接，子 session 空空如也
3. **子 run 无预算兜底** — spec 里的 `default_child_budget` 字段根本没进 `MultiAgentConfig`，`delegate(budget=None)` 被直接传下去，子 run 可跑到自己配置的 `max_steps` 无上限
4. **`AgentNotFoundError` 从未被 raise** — 接口导出了这个异常，但实际抛的是 `ConfigError`（从 `Runtime.run_detailed` 冒出来）；外部 catch 这个类永远 catch 不到
5. **`_run_depths` 内存泄漏** — 进程级 dict 只增不减；spec 原文要求 "depth is tracked by walking the `parent_run_id` chain — no new fields needed"，实现反了

详见 `docs/research/2026-04-24-multi-agent-gap-analysis.md`。

## What Changes

- **BREAKING（`shared` 语义）**：session manager 的锁改成 asyncio-task 可重入（contextvars 记录当前 task 已持有的 session_id 集合），使父/子同 task 再次进入同一 session 时不阻塞
- **`forked` 真快照**：`SessionManagerPlugin` 新增 `fork_session(source, target)` 能力；`DefaultAgentRouter._resolve_session(..., "forked")` 之后调用它把父 session 的 messages/artifacts 复制到新 sid
- **`default_child_budget` 字段落地**：`MultiAgentConfig` 加 `default_child_budget: RunBudget | None`；`delegate()` / `transfer()` 的 `budget` 入参为 None 时兜底到这个字段；兜底不到就继承 ctx.run_request.budget 的 MAX_COST/MAX_STEPS 的一个 fraction（配置化，default 1.0 即完全继承）
- **`AgentNotFoundError` 真抛**：`DefaultAgentRouter.delegate/transfer` 在调 `_run_fn` 前先用 `_agents_by_id` 检查 agent_id；未知 id 立即抛 `AgentNotFoundError`（保留 `ConfigError` 作为 fallback，兼容直接调 `Runtime.run_detailed` 的路径）
- **深度走 parent_run_id 链**：移除 `DefaultAgentRouter._run_depths` 字段；`_check_depth(ctx)` 改为根据 `ctx.run_request.parent_run_id` + runtime 的 run history（或 request 链）统计祖先数量；无进程级可变状态
- 测试补齐：shared 真·并发执行（不 mock session manager）、forked 历史复制验证、budget 兜底、AgentNotFoundError 路径、深度链行走

## Capabilities

### New Capabilities
- `agent-router`: 多 agent 委派/交接 seam 的对外契约：`delegate` / `transfer` 方法、3 种 session 隔离语义、深度限制、预算兜底、错误契约、session 锁可重入要求

### Modified Capabilities
<!-- 现有 specs/ 下没有多 agent 或 session 相关 spec；本次变更均属新能力 -->

## Impact

- `openagents/interfaces/agent_router.py` — 文档化 `AgentNotFoundError` 触发点
- `openagents/plugins/builtin/agent_router/default.py` — 改 `_check_depth`、加 agent_id 预校验、budget 兜底、`forked` 走 `fork_session`、删除 `_run_depths`
- `openagents/config/schema.py` — `MultiAgentConfig.default_child_budget: RunBudget | None`
- `openagents/plugins/builtin/session/*.py` — 3 个 session backend（InMemory / JsonlFile / SQLite）新增 `fork_session`；session CM 改可重入
- `openagents/interfaces/session.py`（若存在 Protocol）— `fork_session` 方法签名
- `openagents/plugins/builtin/runtime/default_runtime.py` — 维持 `HandoffSignal` 捕获；无需改动
- `tests/unit/test_agent_router.py`、`tests/integration/test_multi_agent.py` — 扩展
- `tests/unit/test_session_*.py` — 新增 `fork_session` + 可重入锁测试
- `docs/research/2026-04-24-multi-agent-gap-analysis.md` — 更新 "已修复" 状态（完工后）
- `examples/multi_agent/` — 可保留，改前 demo 已有（示例行为不变）
