# OpenAgents SDK 文档

这套文档面向基于 OpenAgents kernel 做二次开发的开发者。

请先记住一条主线：

- OpenAgents 负责 **single-agent runtime kernel**
- SDK 提供少量高价值 **runtime seam**
- 你的应用负责 **产品自己的 middle protocol**

只要你把这三层分清，这个 SDK 就会很好用。

## 先建立心智模型

```text
Kernel Protocols
    RunRequest, RunResult, RunContext, ToolExecutionRequest, SessionArtifact

Runtime Seams（2026-04-18 consolidation 后共 8 个）
    memory, pattern, tool, tool_executor, context_assembler,
    runtime, session, events, skills

Pattern-subclass method overrides（不再是独立 seam）
    PatternPlugin.resolve_followup()
    PatternPlugin.repair_empty_response()
    ToolExecutorPlugin.evaluate_policy()

App-Defined Protocols
    task envelopes, coding plans, permission state, review contracts,
    retrieval plans, artifact taxonomies, product semantics
```

OpenAgents 最擅长的，不是把所有产品逻辑内建，而是把 kernel 和 seam 做清楚，
让你在上层自由发明协议。

## 推荐阅读路径

### 第一次接触这个 SDK

1. [开发者指南](developer-guide.md)
2. [Seam 与扩展点](seams-and-extension-points.md)
3. [配置参考](configuration.md)
4. [示例说明](examples.md)

### 要写自定义插件

1. [插件开发](plugin-development.md)
2. [配置参考](configuration.md)
3. [API 参考](api-reference.md)
4. [示例说明](examples.md)

### 要设计自己的 middle protocol

1. [开发者指南](developer-guide.md)
2. [Seam 与扩展点](seams-and-extension-points.md)
3. [插件开发](plugin-development.md)

## 各文档负责什么

- [开发者指南](developer-guide.md)
  - 讲架构边界、runtime 生命周期、状态 carrier、协议应该放哪层
- [仓库结构](repository-layout.md)
  - 讲当前顶层目录、文档拓扑、examples/tests 各自负责什么
- [Seam 与扩展点](seams-and-extension-points.md)
  - 讲“遇到一个问题，应该改哪层、用哪个 seam、还是留在 app 层”
- [配置参考](configuration.md)
  - 讲 JSON schema、selector 规则、builtin 名称、优先级与配置模式
- [插件开发](plugin-development.md)
  - 讲 loader 如何工作、各类插件最小契约是什么、怎么写、怎么测
- [API 参考](api-reference.md)
  - 讲 package exports、runtime 方法、核心协议对象、plugin contract
- [示例说明](examples.md)
  - 讲 examples 目录下每个示例解决什么问题、应该从哪个开始看
- [OpenAgent Agent Builder](openagent-agent-builder.md)
  - 讲如何把 OpenAgents 作为一个高层 skill，用来 build 单个 subagent / team-role agent

## 当前维护面

当前 repo 已经收敛到一个更窄但更干净的外层结构：

- 文档统一在 `docs/`
- 顶层入口由 `README.md`、`README_EN.md`、`README_CN.md` 组成
- `examples/` 当前只保留两个维护中的示例：
  - `quickstart`
  - `production_coding_agent`

如果你要快速理解仓库本身，而不是只看 SDK 概念，建议先读：

1. [仓库结构](repository-layout.md)
2. [示例说明](examples.md)
3. [开发者指南](developer-guide.md)

## 快速判断

### 一个新协议应该放哪？

- 如果它改变 tool 怎么执行 / 能不能执行，用 `tool_executor`（覆写 `evaluate_policy()`）
- 如果它决定本轮 run 吃进什么上下文，用 `context_assembler`
- 如果它回答本地 follow-up，覆写 `PatternPlugin.resolve_followup()`
- 如果它修 bad response / empty response，覆写 `PatternPlugin.repair_empty_response()`
- 如果它负责发现、导入、执行 host-level skill package，用顶层 `skills` 组件
- 如果它表达的是产品语义，就放在 app 层，借助：
  - `RunContext.state`
  - `RunContext.scratch`
  - `RunContext.assembly_metadata`
  - `RunRequest.context_hints`
  - `RunArtifact.metadata`

### 什么时候应该新建 seam？

只有在下面这些条件同时满足时，才值得考虑：

- 这个问题在多个应用里重复出现
- 它影响的是 runtime 行为，而不是产品语义
- 它需要独立 selector、独立默认实现、独立测试
- 用现有 carrier 表达会很别扭

不满足这些条件，就先把它做成 app-defined protocol。

### 这是 multi-agent SDK 吗？

不是。OpenAgents 是 single-agent kernel。  
team、mailbox、planner、scheduler、approval、UI workflow 都应该放在这层之上。

## 直接看代码

- package exports: [openagents/__init__.py](../openagents/__init__.py)
- runtime facade: [openagents/runtime/runtime.py](../openagents/runtime/runtime.py)
- builtin runtime: [openagents/plugins/builtin/runtime/default_runtime.py](../openagents/plugins/builtin/runtime/default_runtime.py)
- config schema: [openagents/config/schema.py](../openagents/config/schema.py)
- plugin loader: [openagents/plugins/loader.py](../openagents/plugins/loader.py)
- builtin registry: [openagents/plugins/registry.py](../openagents/plugins/registry.py)
- interfaces: [openagents/interfaces](../openagents/interfaces)
