## ADDED Requirements

### Requirement: Agent router seam contract

The `AgentRouterPlugin` seam SHALL expose `delegate(agent_id, input_text, ctx, *, session_isolation, budget, deps) -> RunResult` and `transfer(agent_id, input_text, ctx, *, session_isolation, budget, deps) -> NoReturn`. When `multi_agent.enabled` is true the builtin `DefaultAgentRouter` MUST be injected onto `RunContext.agent_router`; when the block is absent or `enabled=false`, `RunContext.agent_router` MUST be None.

#### Scenario: Router injection when enabled
- **WHEN** `AppConfig.multi_agent.enabled` is true
- **THEN** `Runtime.__init__` wires a `DefaultAgentRouter` whose `_run_fn` points at `Runtime.run_detailed`, and every subsequently-constructed `RunContext` has `agent_router` set to that router instance

#### Scenario: Router absent when disabled
- **WHEN** `AppConfig.multi_agent` is None OR `AppConfig.multi_agent.enabled` is false
- **THEN** `RunContext.agent_router` is None and a plugin attempting to call it raises `AttributeError`

#### Scenario: Transfer ends the parent run with child output
- **WHEN** a pattern or tool calls `ctx.agent_router.transfer("other", "...", ctx)` inside `DefaultRuntime.run()`
- **THEN** `transfer` raises `HandoffSignal(child_result)`, `DefaultRuntime.run()` catches it (it is a `BaseException`), emits `RUN_COMPLETED`, and returns a `RunResult` whose `final_output` equals the child's `final_output` and whose `metadata["handoff_from"]` equals the child's `run_id`

### Requirement: Unknown agent_id raises AgentNotFoundError

`DefaultAgentRouter.delegate` and `DefaultAgentRouter.transfer` SHALL validate the target `agent_id` against the runtime's known agents **before** invoking `_run_fn`. Unknown ids MUST raise `AgentNotFoundError(agent_id)` — not `ConfigError` or a generic `Exception`.

#### Scenario: delegate to unknown agent
- **WHEN** a caller invokes `ctx.agent_router.delegate("nope", "hi", ctx)` and no agent with id `"nope"` is defined in `AppConfig.agents`
- **THEN** the call raises `AgentNotFoundError` whose `agent_id` attribute equals `"nope"`, before any `_run_fn` / child run is started

#### Scenario: transfer to unknown agent
- **WHEN** a caller invokes `ctx.agent_router.transfer("nope", "hi", ctx)` and the id is unknown
- **THEN** the call raises `AgentNotFoundError` (NOT `HandoffSignal`), and the parent run proceeds to handle the exception like any other error

### Requirement: Delegation depth is tracked via request metadata

`DefaultAgentRouter` SHALL NOT maintain any process-level or instance-level mutable state to track delegation depth. The depth of a child run MUST be stored in its `RunRequest.metadata` under the reserved key `__openagents_delegation_depth__`. `_check_depth` MUST read this key from `ctx.run_request.metadata`; calling `delegate` from a context whose stored depth is ≥ `max_delegation_depth` MUST raise `DelegationDepthExceededError` before any child run begins. The root run (no ancestor) has depth 0.

#### Scenario: Root delegate records depth 1 on child
- **WHEN** a root run (metadata has no `__openagents_delegation_depth__` key) calls `delegate("b", ...)`
- **THEN** the child `RunRequest.metadata["__openagents_delegation_depth__"]` equals 1

#### Scenario: Depth propagates across levels
- **WHEN** an agent at depth N calls `delegate("c", ...)`
- **THEN** the child `RunRequest.metadata["__openagents_delegation_depth__"]` equals N+1

#### Scenario: Depth limit enforced
- **WHEN** `max_delegation_depth = 3` and a ctx has `metadata["__openagents_delegation_depth__"] = 3`
- **THEN** calling `delegate` raises `DelegationDepthExceededError(depth=3, limit=3)` before `_run_fn` is called

#### Scenario: No global state grows
- **WHEN** 10,000 sequential delegations occur across different `run_id`s
- **THEN** the router instance's `__dict__` contains no collection whose length grew with run count

### Requirement: Child run budget fallback

`DefaultAgentRouter.delegate` and `.transfer` SHALL resolve the child `RunBudget` in this order: (1) the explicit `budget=` argument if non-None; (2) `MultiAgentConfig.default_child_budget` if configured; (3) None. `MultiAgentConfig` MUST accept a `default_child_budget: RunBudget | None = None` field.

#### Scenario: Explicit budget wins
- **WHEN** caller passes `budget=RunBudget(max_steps=2)` and `default_child_budget=RunBudget(max_steps=5)` is configured
- **THEN** the child `RunRequest.budget.max_steps == 2`

#### Scenario: Default_child_budget fallback applies
- **WHEN** caller passes `budget=None` and `default_child_budget=RunBudget(max_steps=5, max_cost_usd=0.10)` is configured
- **THEN** the child `RunRequest.budget.max_steps == 5` and `max_cost_usd == 0.10`

#### Scenario: No budget configured
- **WHEN** caller passes `budget=None` and `default_child_budget` is None
- **THEN** the child `RunRequest.budget` is None

### Requirement: `isolated` session mode

When `session_isolation="isolated"` (the default), `DefaultAgentRouter` SHALL construct a fresh session id for the child, distinct from the parent's `session_id`. The child session MUST contain no messages or artifacts inherited from the parent.

#### Scenario: Isolated creates a new empty session
- **WHEN** parent session_id is `"sess-1"` and delegate is called with `session_isolation="isolated"`
- **THEN** the child `RunRequest.session_id != "sess-1"` AND `session_manager.load_messages(child_sid)` returns an empty list at the moment the child run starts

### Requirement: `shared` session mode — reentrant lock

When `session_isolation="shared"`, `DefaultAgentRouter` SHALL set the child `RunRequest.session_id` equal to the parent's `session_id`. Every builtin `SessionManagerPlugin` (in-memory, jsonl_file, sqlite_backed) SHALL treat its `session(session_id)` context manager as **reentrant within a single asyncio task**: if the current task already holds the lock for `session_id` (tracked via `contextvars.ContextVar`), a nested `async with session(...)` MUST NOT block and MUST NOT re-acquire the lock. Across asyncio tasks the lock SHALL remain mutually exclusive.

#### Scenario: shared delegate does not deadlock
- **WHEN** a parent run is inside `async with session_manager.session("s1")` and its pattern calls `ctx.agent_router.delegate("child", "x", ctx, session_isolation="shared")`
- **THEN** the child run completes without blocking; both parent and child observe the same session_id `"s1"`

#### Scenario: Cross-task mutual exclusion preserved
- **WHEN** two independent asyncio tasks concurrently call `session_manager.session("s1")`
- **THEN** exactly one holds the lock at a time; the other awaits until the first releases

### Requirement: `forked` session mode — real snapshot copy

When `session_isolation="forked"`, `DefaultAgentRouter` SHALL allocate a new child session id (e.g. `"{parent}:fork:{run_id}"`) AND call `SessionManagerPlugin.fork_session(parent_sid, child_sid)` before constructing the child `RunRequest`. After fork, the child session MUST contain a full copy of the parent's messages, artifacts, and state at fork time; subsequent writes to either side MUST NOT propagate to the other.

#### Scenario: Forked child sees parent history
- **GIVEN** parent session `"sess-1"` has messages `[m1, m2]` and artifact `a1`
- **WHEN** delegate is called with `session_isolation="forked"`
- **THEN** the child's session has messages `[m1, m2]` and artifact `a1` when the child run starts

#### Scenario: Parent writes after fork do not reach child
- **GIVEN** parent forks into child session, then parent appends `m3` to its own session
- **WHEN** child reads its messages
- **THEN** child does NOT see `m3`; child's messages remain `[m1, m2]`

#### Scenario: Child writes do not propagate to parent
- **GIVEN** child is inside the forked session and appends `m3`
- **WHEN** parent reads its own session
- **THEN** parent does NOT see child's `m3`

### Requirement: SessionManagerPlugin exposes fork_session

Every `SessionManagerPlugin` implementation (builtin and third-party) SHALL provide `async def fork_session(self, source_session_id: str, target_session_id: str) -> None`. The operation MUST be atomic from the observer's perspective (i.e., no partial state visible if the operation fails) and MUST raise if `target_session_id` already exists.

#### Scenario: fork_session raises on target collision
- **WHEN** `fork_session("a", "b")` is called and session `"b"` already has state
- **THEN** the call raises an error and `"b"`'s state is unchanged

#### Scenario: fork_session copies messages and artifacts
- **WHEN** `fork_session("a", "b")` is called and `"a"` has N messages and K artifacts
- **THEN** after the call, `load_messages("b")` returns exactly those N messages and `list_artifacts("b")` returns those K artifacts

### Requirement: Config field `default_child_budget`

`MultiAgentConfig` SHALL accept an optional `default_child_budget: RunBudget | None = None` field via the pydantic schema. When omitted, it remains None and has no effect.

#### Scenario: Parses default_child_budget
- **WHEN** AppConfig JSON contains `{"multi_agent": {"enabled": true, "default_child_budget": {"max_steps": 5, "max_cost_usd": 0.1}}}`
- **THEN** `config.multi_agent.default_child_budget` is a `RunBudget` with `max_steps=5` and `max_cost_usd=0.1`

#### Scenario: Omitted default_child_budget
- **WHEN** AppConfig JSON contains `{"multi_agent": {"enabled": true}}`
- **THEN** `config.multi_agent.default_child_budget` is None
