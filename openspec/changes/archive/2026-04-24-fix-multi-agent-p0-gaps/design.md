## Context

多 Agent seam 在 2026-04-24 按 `docs/superpowers/specs/2026-04-24-multi-agent-design.md` 初步落地，但有 5 项 spec 承诺未兑现（详见 `docs/research/2026-04-24-multi-agent-gap-analysis.md`）。其中 `shared` 模式会在真实运行时死锁，`forked` 只改命名不抄历史——当前单测/集成测试都用 MagicMock 绕过了 session manager，所以未暴露。本次 change 在**不新增 seam**、**不破坏现有单 agent 用户**的前提下，把 5 项 P0 一次性对齐。

影响范围：
- `openagents/plugins/builtin/agent_router/default.py` — 主要逻辑改动
- `openagents/plugins/builtin/session/{in_memory,jsonl_file,sqlite_backed}.py` — 新增 `fork_session`、锁可重入改造
- `openagents/interfaces/session.py`（若存在 Protocol）— `fork_session` 方法
- `openagents/config/schema.py` — `MultiAgentConfig.default_child_budget`

## Goals / Non-Goals

**Goals:**
- `shared` 模式真实运行不再死锁；父子可以在同一 session 内串行写
- `forked` 模式真实复制父 session 的 messages/artifacts 到新 sid；父子独立演进
- 子 run 有预算兜底路径：`delegate(budget=None)` 时自动用 `default_child_budget`
- 调不存在的 agent_id 立即抛 `AgentNotFoundError`，而不是等到 `Runtime.run_detailed` 抛 `ConfigError`
- 深度检查用无状态的 parent_run_id 链行走，消除 `_run_depths` 内存泄漏
- 覆盖率保持 ≥ 90%

**Non-Goals:**
- 不新增 P1/P2 项（router 事件发射、stream_projection 投影、handoff_chain 累积、循环检测、子 run 取消）——留到后续 change
- 不改 `HandoffSignal` 机制（当前工作良好）
- 不引入多进程/跨机 agent 协调（仍是进程内）
- 不扩展 `multi_agent` config 到能定义"agent topology"（仍由 app 层编排）

## Decisions

### D1. `shared` 死锁修法：可重入 session 锁（选方案 A）

**选项：**
- **A. 让 session 锁"asyncio-task 可重入"**：用 `contextvars.ContextVar[set[str]]` 记录当前 task 已持有的 session_id 集合，`session()` CM 在进入时检查——若已在集合里，跳过 `lock.acquire` 只做状态访问
- B. router 在 `shared` 模式绕开 `Runtime.run_detailed`，直接 inline 调 pattern/memory
- C. 删除 `shared` 语义

**决策：A**
- B 会让 `shared` 和其他模式代码路径严重分叉，且子 run 的 `DefaultRuntime.run()` 里有 skills prep / events emit / budget enforcement 一整套，inline 等于维护第二份 runtime
- C 太激进——spec 已批准 3 个模式，示例和 test 都写了；语义上"child 看父 live history"是合理需求
- A 的代价是 session backend 必须统一实现：**3 个 builtin session（InMemory / Jsonl / SQLite）都要改**。但每个只是 2-3 行的 contextvars gate，量小、测试点清晰

实现骨架（各 session 通用）：
```python
_reentrant_sessions: ContextVar[frozenset[str]] = ContextVar("_reentrant_sessions", default=frozenset())

@asynccontextmanager
async def session(self, session_id: str):
    held = _reentrant_sessions.get()
    if session_id in held:
        # Already held by this asyncio task chain — reuse without re-acquiring.
        yield await self._ensure_loaded(session_id)
        return
    lock = self._locks.setdefault(session_id, asyncio.Lock())
    token = _reentrant_sessions.set(held | {session_id})
    await lock.acquire()
    try:
        yield await self._ensure_loaded(session_id)
    finally:
        lock.release()
        _reentrant_sessions.reset(token)
```

注意：
- `ContextVar` + `asyncio.Task` 行为良好；`await self._run_fn(...)` 继承当前 task，`held` 传递正确
- 如果后续 `run_detailed` 被 `asyncio.create_task` 开成新 task（目前不这么做），这个可重入会失效——需要防御测试

### D2. `forked` 真快照：`SessionManagerPlugin.fork_session(source, target)`

**选项：**
- **A. session 协议加 `fork_session`，router 在 delegate 前调用**
- B. router 自己用 `load_messages` / `list_artifacts` / `append_message` / `save_artifact` 手工复制
- C. 懒复制：子 session 在读的时候 fallback 读父

**决策：A**
- B 把 session 内部细节泄漏到 router；且 state / checkpoints 的复制逻辑一旦变更，router 需要跟进
- C 实现复杂（读路径 join 父子两个 session）、语义模糊（父写之后子能不能看到？spec 说"快照"意味 NO）
- A 把复制语义归属到 session 协议，每个 backend 自行实现最优路径（SQLite 可以纯 SQL `INSERT SELECT`，InMemory 纯 dict copy）

接口：
```python
# openagents/interfaces/session.py（或 SessionManagerPlugin Protocol）
async def fork_session(self, source_session_id: str, target_session_id: str) -> None:
    """Copy all messages/artifacts/state from source to target. Target must not exist.

    After fork_session returns, target has the same observable state as source at
    call time. Future writes to either side DO NOT propagate to the other.
    """
```

router 侧：
```python
def _resolve_session(self, ctx, isolation):
    if isolation == "forked":
        target = f"{ctx.session_id}:fork:{ctx.run_id}"
        # 在 _resolve 阶段只返回 sid，fork 在 delegate 中的 await 之前执行
        return target, ("fork", ctx.session_id, target)
    ...
```
（`delegate()` 拿到 `(target_sid, action)` 后，如 action 是 fork 则先调 `session_manager.fork_session(src, target)`，再构造 `RunRequest`）

### D3. `default_child_budget` 字段 + 兜底优先级

新增 `MultiAgentConfig.default_child_budget: RunBudget | None = None`。

`delegate()` / `transfer()` 的 budget 解析优先级：
1. 调用方显式传入的 `budget` 参数（非 None）
2. `self._config.default_child_budget`（如果配置了）
3. 从父 ctx 继承的 `RunBudget`（若父有预算，子共用父的剩余额度 — 这一条留到 P1，不在本 change）
4. None（即 agent 自己 `runtime.max_steps` / `step_timeout_ms` 兜底）

本 change 只实现 1 + 2。父预算继承（点 3）复杂（涉及共享额度扣减），留到后续。

### D4. `AgentNotFoundError` 预校验

`DefaultAgentRouter` 持有 `agents_by_id` 引用？当前没有——router 只有 `_run_fn`。两个选项：

**A. router 拿一个 `agent_exists` 回调**
```python
self._agent_exists: Callable[[str], bool] | None = None
# Runtime.__init__ 里注入：_agent_router._agent_exists = lambda aid: aid in self._agents_by_id
```

B. router 做子 run 前先 `try: self._run_fn(...)` catch `ConfigError`——太绕

**决策：A**。简单、无侵入。

### D5. 深度检查：走 parent_run_id 链

移除 `_run_depths`。新逻辑：
```python
def _check_depth(self, ctx: RunContext) -> None:
    depth = 0
    current = ctx.run_request
    while current and current.parent_run_id:
        depth += 1
        if depth >= self._max_depth:
            raise DelegationDepthExceededError(depth, self._max_depth)
        current = self._run_request_index.get(current.parent_run_id)  # ❌ 需要索引
```

问题：`RunContext` 只有 `parent_run_id: str | None`（字符串），没有指向父 `RunRequest` 的引用。要真·走链需要 runtime 持有 run history——成本过大。

**简化版**：承认无法追溯跨 run 的链，只计 **current ctx 的直接祖先数**：
```python
def _check_depth(self, ctx: RunContext) -> None:
    # ctx.run_request.parent_run_id == 上一层 parent 的 run_id
    # 每一层 parent_run_id 存在就代表深了 1 级；但只能看到直接父
    # ——这其实和"当前是不是 root"是一个 bit 的信息
```

**再设计**：用 `RunRequest.metadata` 记录深度：`metadata["delegation_depth"]`。父在构造子 `RunRequest` 时写入 `parent_depth + 1`；router 读它：
```python
def _check_depth(self, ctx: RunContext) -> None:
    depth = (ctx.run_request.metadata or {}).get("delegation_depth", 0)
    if depth >= self._max_depth:
        raise DelegationDepthExceededError(depth, self._max_depth)

# delegate() 里构造子 request:
child_request = RunRequest(
    ...,
    metadata={"delegation_depth": depth + 1},
)
```

这是**无进程状态**的设计：每个 RunRequest 携带自己的深度，链行走退化为"读自己的 metadata"。满足 spec "walking the parent_run_id chain — no new fields needed" 的精神（实际用了 metadata 键，不是 `RunRequest` 新字段）。

删掉 `_run_depths`；老测试里直接操作它的写法（`router._run_depths["deep-run"] = 2`）要换成 `ctx.run_request.metadata = {"delegation_depth": 2}`。

## Risks / Trade-offs

- **[风险] 可重入锁语义改变** → 锁只在**同 asyncio task** 内可重入；跨任务（`asyncio.create_task`）行为不变。测试覆盖：父 task 内嵌调用 + 并发 task 竞争同 sid 都需验证。
- **[风险] `fork_session` 在并发场景的原子性** → SQLite 可以事务性复制；InMemory / Jsonl 需要在复制期间持 source 和 target 两把锁，避免复制中间状态。mitigation：`fork_session` 协议要求持 source 锁做快照，再持 target 锁写入。
- **[Trade-off] `delegation_depth` metadata 外泄** → `RunRequest.metadata` 本是用户自由字段，我们占用一个保留 key。mitigation：用命名空间前缀 `__openagents_delegation_depth__`，在 schema/文档里明确此 key 保留。
- **[Trade-off] D5 的"链行走"精神 vs 实际**  → 实际是直接读 metadata，不走真链。如果有 app 绕开 router 手工构造 child `RunRequest` 不带 metadata，深度检查失效。接受这个代价——绕开 router 的场景本就不受 spec 保护。
- **[风险] 3 个 session backend 实现 `fork_session` 的一致性** → 用一组通用的 contract test 覆盖所有 backend（一套测试喂 3 个 backend）。

## Migration Plan

- 现有 `multi_agent.enabled=true` 用户的现象改变：
  - `shared` 原本死锁 → 现在正常运行（⬆️ 行为改善）
  - `forked` 原本空 session → 现在真有父历史（⬆️ 符合 spec）
  - 未显式传 `budget` 的 delegate → 若用户没配 `default_child_budget`，行为不变（仍是 None）；配了则生效
  - 未知 agent_id → 错误类型从 `ConfigError` 变 `AgentNotFoundError`（⬇️ 破坏性）；但 `AgentNotFoundError` 继承自 `Exception`，catch `Exception` 的代码不受影响
- 无数据迁移；无配置破坏性（`default_child_budget` 可选）
- Rollback：删除 `fork_session` 以外的所有改动可回到现状（`fork_session` 是新加，不回退无影响）

## Open Questions

- `metadata["__openagents_delegation_depth__"]` 这个键名要不要提到接口文档里作为保留键？（倾向：要）
- `fork_session` 是否需要返回"快照时间戳"用于审计？（倾向：不需要，保持最小接口）
- 父 run 预算继承（D3 的点 3）是否在本 change 内做？（决策：**不做**，留 P1）
