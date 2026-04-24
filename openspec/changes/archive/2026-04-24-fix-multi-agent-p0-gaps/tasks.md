## 1. 准备与基线

- [x] 1.1 跑一遍现有 `tests/unit/test_agent_router.py` 和 `tests/integration/test_multi_agent.py`，记录基线绿；确认 `openspec/changes/fix-multi-agent-p0-gaps/` 下的 proposal/design/specs 齐全
- [x] 1.2 在一个**单独测试文件**里加一个会失败的红测：真实（不用 MagicMock）执行 `shared` 模式的 delegate，断言不会挂死（用 `asyncio.wait_for(..., timeout=5)` 包裹）。这个测试在后续 2.x 完成前应保持红

## 2. Session 锁可重入（修 G6 — shared 死锁）

- [x] 2.1 在 `openagents/plugins/builtin/session/_reentry.py`（新文件）定义 `_HELD_SESSIONS: ContextVar[frozenset[str]]`（default `frozenset()`），导出 `reentrant_session(lock, sid) -> async cm` 辅助函数
- [x] 2.2 改造 `openagents/plugins/builtin/session/in_memory.py` 的 `session()` CM：用 contextvars gate，若 sid 已在 held 集合则跳过 `lock.acquire`
- [x] 2.3 同样改造 `openagents/plugins/builtin/session/jsonl_file.py`
- [x] 2.4 同样改造 `openagents/plugins/builtin/session/sqlite_backed.py`
- [x] 2.5 `tests/unit/test_session_reentry.py`（新文件）：对每个 backend 跑三个 case — 同 task 嵌套 `async with session("s")` 不死锁；不同 task 并发竞争同 sid 仍互斥；嵌套退出后再入能正常再次 acquire
- [x] 2.6 1.2 的红测现在应该变绿；若仍红，回到 2.x 查漏

## 3. `fork_session` session 协议扩展（修 G2 — forked 真快照）

- [x] 3.1 在 session Protocol（`openagents/interfaces/session.py` 若存在，否则在 `plugins/capabilities.py`）定义 `async def fork_session(source: str, target: str) -> None` 契约
- [x] 3.2 `in_memory.py`：`fork_session` = 持 source 锁 deepcopy `_states[source]`、`_messages[source]`、`_artifacts[source]` 到 target；target 已存在则抛 `FileExistsError`（或 `SessionExistsError`）
- [x] 3.3 `jsonl_file.py`：`fork_session` = copy 文件 `{root}/{source}.jsonl` → `{root}/{target}.jsonl`，atomic（临时文件 + `os.replace`）
- [x] 3.4 `sqlite_backed.py`：`fork_session` = 单事务 `INSERT INTO events(session_id, ...) SELECT '{target}', ... FROM events WHERE session_id='{source}'`；先 SELECT 确认 target 无行
- [x] 3.5 `tests/unit/test_session_fork.py`（新文件）：contract test 喂 3 个 backend，验证 messages + artifacts + state 都复制；target 存在时抛；fork 后父/子独立写
- [x] 3.6 在 `DefaultAgentRouter` 加 `_session_manager: SessionManagerPlugin | None` 字段（Runtime.__init__ 注入）
- [x] 3.7 改 `DefaultAgentRouter.delegate`：`forked` 分支在 `_run_fn` 前 `await self._session_manager.fork_session(ctx.session_id, target_sid)`
- [x] 3.8 集成测试：用 in_memory session + 真实 Runtime，验证 forked 模式下子真看得到父的消息

## 4. `default_child_budget` 兜底（修 G4）

- [x] 4.1 `openagents/config/schema.py`：`MultiAgentConfig` 加 `default_child_budget: RunBudget | None = None`
- [x] 4.2 `DefaultAgentRouter.__init__`：读 `cfg.get("default_child_budget")` 构造 `RunBudget`（若 cfg 是 dict 而非 model_dump，注意嵌套解析）
- [x] 4.3 `DefaultAgentRouter.delegate`：budget 解析优先级 explicit → default_child_budget → None
- [x] 4.4 `test_agent_router.py`：新增三个 case — explicit budget wins / default_child_budget fallback / no budget None
- [x] 4.5 更新 `tests/unit/test_config_schema.py` 验证 schema 解析（实际合并入 `test_agent_router.py` 的 schema 测试段）

## 5. `AgentNotFoundError` 预校验（修 G1）

- [x] 5.1 `DefaultAgentRouter.__init__`：加 `_agent_exists: Callable[[str], bool] | None = None`
- [x] 5.2 `openagents/runtime/runtime.py` `Runtime.__init__` 的 router 绑定代码里：`_agent_router._agent_exists = lambda aid: aid in self._agents_by_id`
- [x] 5.3 `DefaultAgentRouter.delegate` 和 `.transfer` 开头：若 `self._agent_exists is not None` 且 `not self._agent_exists(agent_id)`，抛 `AgentNotFoundError(agent_id)`
- [x] 5.4 `test_agent_router.py`：新增 case — `delegate("nope", ...)` 抛 `AgentNotFoundError`；`transfer("nope", ...)` 抛 `AgentNotFoundError`（不是 `HandoffSignal`）
- [x] 5.5 `test_multi_agent.py`：集成测试验证通过真实 Runtime 调未知 agent 的错误链路

## 6. 深度走 metadata（修 G3 — 删除 `_run_depths`）

- [x] 6.1 新增保留键常量 `__DELEGATION_DEPTH_KEY__ = "__openagents_delegation_depth__"`（放 `openagents/interfaces/agent_router.py` 或 `capabilities.py`）
- [x] 6.2 `DefaultAgentRouter._check_depth(ctx)`：从 `ctx.run_request.metadata` 读 key，default 0；≥ max 抛 `DelegationDepthExceededError`
- [x] 6.3 `DefaultAgentRouter.delegate`：构造 child `RunRequest` 时 `metadata={..., __DELEGATION_DEPTH_KEY__: parent_depth + 1}`
- [x] 6.4 移除 `_run_depths` 字段 + 所有 read/write
- [x] 6.5 `test_agent_router.py`：改旧的 `router._run_depths["deep-run"] = 2` 测试为 `ctx.run_request.metadata = {"__openagents_delegation_depth__": 2}`；新增 case — 10 次顺序 delegate 后 router `__dict__` 不含任何按 run_id 索引的 collection
- [x] 6.6 文档：在 `docs/superpowers/specs/2026-04-24-multi-agent-design.md` 的 "Error Handling" 段尾加一行 note，说明 depth 通过 metadata 保留键传递

## 7. 示例与文档对齐

- [x] 7.1 `examples/multi_agent/run_demo_mock.py`：新增 "scenario 4 — shared" 和 "scenario 5 — forked" demo，证明不再死锁、forked 子能看父历史
- [x] 7.2 `examples/multi_agent/agent_mock.json`：可选加一个 `default_child_budget` 字段示例
- [x] 7.3 更新 `examples/multi_agent/README.md` 的 "session_isolation" 表格，把"forked 基于父派生"改成"forked 复制父历史快照"
- [x] 7.4 `docs/research/2026-04-24-multi-agent-gap-analysis.md`：末尾追加 "## 7. 修复记录"段，标注 5 项 P0 状态为 Done，引用本 change 的 openspec 路径

## 8. 覆盖率与回归

- [x] 8.1 `uv run pytest -q`（全量）通过 — 1916 passed, 9 skipped
- [x] 8.2 `uv run coverage run -m pytest && uv run coverage report --fail-under=90` 通过；`openagents/plugins/builtin/agent_router/default.py` 单文件覆盖 100%（interface 文件 90%，剩余为 Protocol 抽象方法存根）
- [x] 8.3 手跑 `uv run python examples/multi_agent/run_demo_mock.py` 验证 5 个场景全绿
- [x] 8.4 `openspec validate fix-multi-agent-p0-gaps --strict` 通过

## 9. 提交与归档

- [x] 9.1 分批 commit（commits adb98d2 session / ea0588f router / e33de02 docs / openspec-artifacts — 因 `agent_router/default.py` 与 session backends 各有跨 gap 的文件内重叠，实际落成 4 个按文件边界切的 commit 而非 6 个按 gap 切的 commit）
- [x] 9.2 `openspec archive fix-multi-agent-p0-gaps` 归档 change
