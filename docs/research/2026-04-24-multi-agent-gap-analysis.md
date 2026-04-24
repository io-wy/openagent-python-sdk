# 多 Agent 支持差距分析 — OpenAgents Python SDK

**日期：** 2026-04-24
**状态：** 探索阶段
**对照基线：** `docs/superpowers/specs/2026-04-24-multi-agent-design.md`（Approved）+ `docs/research/2026-04-24-agent-sdk-comparison.md`
**代码基线：** main @ commit `95c35e6`（多 agent 示例已入库）

---

## 1. 现状总览

多 Agent seam 已按 spec 初步落地，`multi_agent.enabled=true` 时 `ctx.agent_router` 可用：

```
Runtime facade
    │
    │  __init__()
    │  ├─ load_runtime_components()
    │  └─ load_agent_router_plugin(multi_agent)          ← 按需
    │       └─ sets _run_fn = self.run_detailed
    │       └─ injects into DefaultRuntime._agent_router
    │
    ▼
DefaultRuntime.run()
    ├─ session_manager.session(session_id)  ←── 🔒 asyncio.Lock
    │   ├─ pattern.setup() / memory.inject()
    │   ├─ _inject_context_dependencies()
    │   │     └─ ctx.agent_router = self._agent_router
    │   ├─ pattern.execute(ctx)
    │   │     │
    │   │     └─ tool/pattern 调用 ctx.agent_router.delegate(...)
    │   │              │
    │   │              ▼
    │   │         DefaultAgentRouter
    │   │              ├─ _check_depth(ctx)
    │   │              ├─ _resolve_session(ctx, isolation)
    │   │              └─ self._run_fn(request=...)
    │   │                     └─ Runtime.run_detailed()  ← 递归
    │   │
    │   └─ returns RunResult
    │
    └─ except HandoffSignal as sig:  ←── BaseException，绕过 except Exception
          emit RUN_COMPLETED with handoff_from = sig.result.run_id
          return RunResult(... metadata['handoff_from'] = child.run_id)
```

**已落地清单：**

- 接口 `AgentRouterPlugin` + `HandoffSignal` + 三类异常（`AgentNotFoundError`、`DelegationDepthExceededError`）
- `DefaultAgentRouter` 含三种隔离模式（`shared` / `isolated` / `forked`）+ 深度检查
- `MultiAgentConfig` pydantic schema
- `RunContext.agent_router` 字段 + `_inject_context_dependencies` 注入
- Plugin registry + loader（`load_agent_router_plugin`）
- `DefaultRuntime.run()` 捕获 `HandoffSignal` 并返回 `metadata.handoff_from`
- `Runtime.__init__` post-construct 绑 `_run_fn = self.run_detailed`
- 单测 + 集成测试 + 4-agent mock / real 示例

---

## 2. 差距矩阵（spec 承诺 vs 实际行为）

| #   | Spec 承诺                                                           | 实际行为                                                                                                    | 严重度                                         |
| --- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| G1  | 目标 agent 不存在时抛 `AgentNotFoundError`                          | 实际抛 `ConfigError`（从 `Runtime.run_detailed` 冒出）；`AgentNotFoundError` **从未被 raise**                | P0 — 对外异常契约断裂                          |
| G2  | `"forked"` **复制父 session 历史快照** 到新 session                 | **只改 session_id 命名**（`{sess}:fork:{run}`）；消息、artifacts、state 都**不会复制**                      | P0 — 承诺与行为不一致，用户隐性踩坑            |
| G3  | spec 原文："depth is tracked by walking the `parent_run_id` chain — **no new fields needed**" | 实际使用进程级 `dict[str, int]` 缓存，**从不 GC**                                                           | P0 — 内存泄漏；实现与 spec 相反                |
| G4  | `MultiAgentConfig.default_child_budget` 作为子 run 兜底预算          | schema **没有这个字段**；`delegate(budget=None)` 真的把 `None` 往下传，子 run 无预算保护                     | P0 — 成本/步数失控风险                         |
| G5  | （spec 未显式要求，但 OpenAgents 核心优势是细粒度事件）             | `DefaultAgentRouter` **一个 `emit` 都没有**；`stream_projection` 也不认识 router 事件                       | P1 — 可观测性缺位                              |
| G6  | `"shared"` 模式：child 能看 parent 的 full conversation history      | **父持 session lock 时调 router → 子再拿同一 lock → 死锁**。`session_manager.session()` 内部是 per-id `asyncio.Lock`（非可重入），父任务再进来也会等 | **🔴 P0 — 功能根本不可用**                     |
| G7  | （语义细节）                                                        | `deps` 继承只能全传或完全覆盖；无法表达 "显式无 deps"                                                       | P2 — 小语义瑕疵                                |
| G8  | （不在 spec）                                                       | handoff 链 > 1 层（A transfer→ B transfer→ C）时 `metadata.handoff_from` 只记 B，丢失 A 的血统              | P2 — 审计/追踪降级                             |
| G9  | 覆盖率 `fail_under = 90`                                            | 缺测：`shared` 锁行为、`forked` 历史复制承诺、budget 兜底、router 事件                                      | P1                                             |
| G10 | （不在 spec）                                                       | 父 run 流式 (`run_stream`) 时，子 run 的事件被 `stream_projection` 按 `run_id` 过滤丢弃 — 父端**看不见子进度** | P2 — UX                                        |
| G11 | （不在 spec）                                                       | 没有子 run 取消原语（父 timeout / 用户 abort 不能传到子）                                                   | P2                                             |
| G12 | （不在 spec）                                                       | 循环委派 A→B→A→B... 在未到 `max_depth` 前不会触发；无专门 cycle detection                                 | P3 — 可选                                      |

---

## 3. 最痛的三个洞

### ① `shared` 模式死锁（G6）

```
parent task                        child (same task, awaited)
    │
    │ async with session.session("s1"):   ← 拿到 asyncio.Lock("s1")
    │   pattern.execute(ctx)
    │     router.delegate(..., isolation="shared")
    │       _run_fn(child_request with session_id="s1")
    │         Runtime.run_detailed(...)
    │           DefaultRuntime.run(...)
    │             async with session.session("s1"):  ⬅ 再拿 lock("s1") ❌ 永等
```

现行单测用 `MagicMock` 绕过了 session manager，所以没捕捉到这个问题。真实运行任何 `session_isolation="shared"` 都会挂死。

### ② `"forked"` 名不副实（G2）

spec 写 "copies parent history snapshot"，代码只做 `f"{session_id}:fork:{run_id}"` 的命名拼接。子 session 是**全新的空 session**，和 `isolated` 的行为区别只在命名风格。

要真实现需要：

- Session 协议加 `fork_session(source, target)` 原语，或
- Router 在 `delegate()` 前显式 `load_messages(parent) → append_message(child)`

### ③ 子 run 无预算兜底 + 深度用 dict（G3 + G4）

```python
# 现在
await router._run_fn(request=RunRequest(..., budget=None, ...))
# ↓ 子 agent 默认跑到自己配置的 max_steps=16，成本无上限

# spec 期望
budget=budget or self._config.default_child_budget  # 字段根本不存在
```

并且 `_run_depths` 这个 dict 在长跑服务器里只增不减。

---

## 4. 建议修复顺序

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
P0 — "承诺 vs 实现" 对齐（阻塞生产使用）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
G6: shared 死锁
    方案 A — session lock 改成"可重入"（用 contextvars 记录已持有的 sid）
    方案 B — router 在 shared 模式下直接调 pattern/memory，不走完整 DefaultRuntime.run（绕开 session lock）
    方案 C — 删掉 "shared" 语义，改名警告，只允许从顶层 tool 出口调用
G2: forked 真抄历史 → SessionPlugin 加 fork_session(src, dst)
G4: default_child_budget 字段落地 + delegate 兜底
G1: AgentNotFoundError 真抛（或从接口删除）
G3: _run_depths 改成按 parent_run_id 链行走（无状态、无泄漏）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
P1 — 可观测性 + 稳定性
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
G5: emit agent_router.delegate.{started,completed,failed}
      + agent_router.transfer.{raised} 事件
G5: stream_projection 识别 router 事件，投影为 RunStreamChunk
G8: metadata.handoff_chain: list[run_id] 替代单值 handoff_from
G9: 覆盖 shared 锁行为、forked 历史复制、budget 兜底、事件

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
P2 — 体验 & 产品层（可能留给 app 层）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
G10: 子 run 事件投影到父 stream
G11: child run 取消传播
G12: cycle detection（可选 config）
```

---

## 5. 待讨论的设计问题：`"shared"` 到底该不该保留？

研究报告 `2026-04-24-agent-sdk-comparison.md` 里三家做法：

- **OpenAI Agents SDK `handoff`**：默认传完整历史，走 `input_filter` 过滤。无锁问题，因为是控制权**顺序转移**，父已经退出循环
- **Vercel subagent-as-tool**：子 agent **完全隔离**，连历史都不继承（避免 token 爆炸）
- **OpenAgents spec 的 `shared`**：试图让子看父的 live session —— 在**单进程单锁**架构下本质上是冲突的

两个方向可选：

| 方向                     | 改动                                                                                                  | 风险                                             |
| ------------------------ | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| **保留 `shared`**        | session 锁可重入（`contextvars` 记录已持 sid 集合）；文档明确 "shared 只适合同 session 内顺序调用"   | 并发模型变复杂；要小心 artifacts/state 读写顺序 |
| **去掉 `shared`**        | 只留 `isolated` + `forked`；让 "让子看父历史" 走 `forked`（真快照）                                   | 破坏性改动；已有 example/test 需要改名            |

---

## 6. 附录：文件清单

- `openagents/interfaces/agent_router.py` — 协议 + 异常
- `openagents/plugins/builtin/agent_router/default.py` — `DefaultAgentRouter`
- `openagents/plugins/builtin/runtime/default_runtime.py:1008` — `HandoffSignal` catch
- `openagents/runtime/runtime.py:62-71` — post-construct router 绑定
- `openagents/config/schema.py:119` — `MultiAgentConfig`
- `openagents/interfaces/run_context.py:45` — `agent_router: Any | None`
- `openagents/interfaces/capabilities.py:23` — `AGENT_ROUTER_DELEGATE` 常量
- `openagents/plugins/registry.py:17` / `:152` — 注册
- `openagents/plugins/loader.py:297` — `load_agent_router_plugin`
- `tests/unit/test_agent_router.py` — 21 个单测
- `tests/integration/test_multi_agent.py` — 6 个集成测试
- `examples/multi_agent/` — 4-agent mock + real 示例

---

*作者：Claude（explore 模式）*

---

## 7. 修复记录（2026-04-24）

5 项 P0 已全部对齐，详见 OpenSpec change `openspec/changes/fix-multi-agent-p0-gaps/`。

| # | 原问题 | 修复方式 | 状态 |
|---|-------|--------|------|
| G1 | `AgentNotFoundError` 从未被 raise | `DefaultAgentRouter._agent_exists` 注入回调，delegate/transfer 开头预校验 | ✅ Done |
| G2 | `"forked"` 只改名字不复制历史 | `SessionManagerPlugin.fork_session(src, dst)` 协议新增，3 个 builtin backend 实现；router 在 forked 分支先调 fork_session | ✅ Done |
| G3 | `_run_depths` 内存泄漏 | 移除；深度改走 `RunRequest.metadata["__openagents_delegation_depth__"]`，无状态 | ✅ Done |
| G4 | 子 run 无预算兜底 | `MultiAgentConfig.default_child_budget: RunBudget \| None`；`delegate` 优先级 explicit → default → None | ✅ Done |
| G6 | `shared` 模式会死锁 | `_reentry.reentrant_session` 用 `contextvars` 实现 asyncio-task 可重入；3 个 session backend 统一应用 | ✅ Done |

覆盖率：`DefaultAgentRouter` 单文件 ≥95%；新增 `tests/unit/test_session_reentry.py` + `tests/unit/test_session_fork.py` + `tests/unit/test_shared_mode_deadlock.py`；`tests/unit/test_agent_router.py` 和 `tests/integration/test_multi_agent.py` 扩展了 budget、AgentNotFoundError、forked 历史复制、depth via metadata 等场景。

P1/P2 项（路由事件发射、stream_projection 投影、handoff 链累积、cycle detection、子 run 取消）仍未落地，留待后续 change。
