# 内置 CLI

`openagents` 命令随 SDK 一并安装。默认 `pip install io-openagent-sdk` 就能用；
想获得彩色输出、交互式 prompt、热重载等增强体验，安装 `cli` extra：

```bash
pip install 'io-openagent-sdk[cli]'     # 或 uv sync --extra cli
```

`cli` extra 引入 `rich`、`questionary`、`watchdog`、`PyYAML` 四个依赖；
任何一项缺失时对应功能会自动降级，不会崩溃。

## 子命令一览

| 命令 | 作用 |
| --- | --- |
| `openagents schema` | dump `AppConfig` 或某个 plugin 的 JSON Schema |
| `openagents validate <path>` | 校验 `agent.json` 是否能加载 |
| `openagents list-plugins` | 列出所有已注册的 plugin |
| `openagents version` | 打印 SDK / Python / extras / plugin 数量 |
| `openagents doctor` | 环境体检（Python 版本、extras、API key env） |
| `openagents config show <path>` | 打印解析后的完整 `AppConfig`，支持 `--redact` |
| `openagents init <name>` | 从内置模板脚手架新项目 |
| `openagents new plugin <seam> <name>` | 生成 plugin 骨架 + 单测 stub |
| `openagents run <path>` | 执行一次单轮对话 |
| `openagents chat <path>` | 交互式多轮 REPL |
| `openagents dev <path>` | 配置文件变更时自动 `Runtime.reload()` |
| `openagents replay <path>` | 复放一段会话事件 / transcript |
| `openagents completion <shell>` | 为 bash/zsh/fish/powershell 生成补全脚本 |

`openagents --version` / `-V` 等价于 `openagents version`。

## 退出码约定

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 用户错误：缺必填参数、文件找不到、多 agent 未指定、slash 命令非法等 |
| `2` | 配置校验错误：`load_config` 抛出、JSON/YAML 非法、严格模式下未解析的 plugin `type` |
| `3` | 运行期错误：LLM 失败、plugin 抛异常、`run.error` 非空 |

## 常见用法

### 一次性执行

```bash
openagents run agent.json --input "hello"

# 终端不是 TTY 时默认输出 JSONL（方便 jq）：
openagents run agent.json --input "hello" | jq -c .

# 从文件读取 prompt：
openagents run agent.json --input-file ./prompt.txt

# 走 stdin：
echo "hello" | openagents run agent.json

# 只要最终输出，跳过 streaming：
openagents run agent.json --input "hi" --format text --no-stream
```

多 agent 配置必须用 `--agent <id>`：

```bash
openagents run multi.json --agent coder --input "implement X"
```

### 交互式对话

```bash
openagents chat agent.json
```

内置 slash 命令：

| 命令 | 说明 |
| --- | --- |
| `/exit` / `/quit` | 退出 REPL（退出码 0） |
| `/reset` | 轮换 session_id，清空上下文 |
| `/save <path>` | 把最近一轮结果保存为 JSON（可被 `openagents replay` 读取） |
| `/context` | 打印上一轮的 `final_output` / `stop_reason` |
| `/tools` | 列出当前 agent 的 tool id 和类型 |

### 开发态热重载

```bash
openagents dev agent.json
```

内部调用 `Runtime.reload()`。**注意**：按照 kernel 约束，`dev` **不会**
热替换 top-level `runtime` / `session` / `events` plugin；如需替换这些
整体组件请重启进程。

安装了 `watchdog` 时使用事件驱动；否则退化到 `--poll-interval` 秒级轮询。

### 复放一段历史

```bash
# 来自 openagents run --format events 的 JSONL
openagents replay ./transcript.jsonl

# 来自 /save 的 session envelope
openagents replay ./session.json

# 只看第 2 轮
openagents replay ./transcript.jsonl --turn 2

# 重新导出为标准 JSON envelope（可再被 replay 读取）
openagents replay ./transcript.jsonl --format json > normalized.json
```

### 工程脚手架

```bash
# 新建一个 minimal 模板项目
openagents init my-agent --template minimal --provider mock --yes

# 生成一个新的 tool plugin 骨架（+ tests/unit/test_xxx.py）
openagents new plugin tool calculator
```

可选模板：`minimal`（默认）、`coding-agent`、`pptx-wizard`。

> `pptx-wizard` 脚手架是 `examples/pptx_generator/` 的双 Agent 最小切片（intent-analyst + slide-generator，`chain` 记忆 + markdown 持久化），对 mock provider 直接可跑。完整的 7 阶段 wizard（环境检查 / Tavily 研究 / 大纲 / 主题 / 并行切片 / 编译QA）仍在 `examples/pptx_generator/`，克隆仓库后见那里的 README。

### 环境排查

```bash
openagents doctor
openagents doctor --config agent.json --format json
```

`doctor` **不会**打印任何 API key 的值——只报告是否已 set。

### 查看最终解析结果

```bash
openagents config show agent.json
openagents config show agent.json --redact   # 替换 api_key/token/password/secret 为 ***
openagents config show agent.json --format yaml
```

`impl` 字段会自动被展开成 `builtin` 或 decorator 注册表里对应的 Python
dotted path。

### 生成 shell 补全

```bash
# bash（全局安装示例）
openagents completion bash | sudo tee /etc/bash_completion.d/openagents

# zsh
openagents completion zsh  > ~/.zsh/completions/_openagents

# fish
openagents completion fish > ~/.config/fish/completions/openagents.fish

# PowerShell
openagents completion powershell >> $PROFILE
```

补全脚本由运行时的 argparse 树即时生成，所以新加的子命令会自动出现。

## JSONL 事件流（stability 承诺）

`openagents run --format events` 输出的每一行形如：

```json
{"schema": 1, "name": "tool.called", "payload": {...}}
```

- `schema` 字段是 `EVENT_SCHEMA_VERSION`（当前为 `1`）。
- 不兼容变更时 `schema` bump；**新增字段** 属 additive-only，**不会**
  bump `schema`。下游解析器应忽略未知字段而不报错。
- 最末尾额外追加一行 `{"name": "run.finished", ...}`，包含
  `run_id`、`stop_reason`、`final_output`、`error`。

## 自定义子命令

`openagents/cli/main.py` 只有一个 registry dispatcher：

```python
# openagents/cli/commands/__init__.py
COMMANDS: list[str] = [
    "schema", "validate", "list-plugins", ...
]
```

新增子命令等价于在 `openagents/cli/commands/` 下放一个模块（暴露
`add_parser(subparsers)` + `run(args) -> int`）并把名字加进
`COMMANDS`。不需要改 `main.py`。

更多细节见 `docs/developer-guide.md` 的 "CLI" 一节以及
`docs/seams-and-extension-points.md`。
