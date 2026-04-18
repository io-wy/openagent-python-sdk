# pptx-agent CLI 使用指南

## 安装

```bash
uv add "io-openagent-sdk[pptx]"
```

还需要系统级依赖：Python ≥3.10、Node.js ≥18、npm、`markitdown`（Python 包）。首次运行时，CLI 会检测并引导你安装缺项。

## 命令

- `pptx-agent new [--topic "..."] [--slug ...]` — 开始新 deck
- `pptx-agent resume <slug>` — 恢复一个被中断的 deck
- `pptx-agent memory [--section user_feedback]` — 查看已保存的用户偏好

## 7 阶段流程

1. **Intent Analysis** — 把你的自然语言描述转成结构化 IntentReport
2. **Environment Check** — 检查 Python / Node / npm / markitdown / API keys，缺项交互修复
3. **Research** — 用 Tavily MCP（或 REST fallback）联网搜索
4. **Outline** — 生成 slide-by-slide 的大纲，支持接受 / 重新生成 / 中止
5. **Theme** — 从调色板 / 字体 / 风格目录里选择
6. **Slide Generation** — 每张 slide 独立的 agent run，并行生成；JSON schema 校验失败自动重试，仍失败时 fallback 到 freeform
7. **Compile + QA** — 生成 PptxGenJS 源码、运行 `node compile.js`、`markitdown` 回读校验

## Resume

所有项目状态持久化在 `outputs/<slug>/project.json`（atomic write，每次写入前备份）。任何阶段 Ctrl+C 退出后，都可以 `pptx-agent resume <slug>` 从该阶段恢复。

## Keys & `.env`

- 必需：`LLM_API_KEY`、`LLM_API_BASE`、`LLM_MODEL`
- 可选：`TAVILY_API_KEY`（启用联网研究）
- 用户级 `.env`：`~/.config/pptx-agent/.env`（跨项目共享）
- 项目级 `.env`：`outputs/<slug>/.env`（覆盖用户级）
