# loguru 作为可选日志后端的集成设计

- **Date**: 2026-04-22
- **Status**: Proposed
- **Author**: brainstorming session
- **Scope**: `openagents/observability/`, `pyproject.toml`, `docs/observability*.md`, `docs/configuration*.md`, `docs/repository-layout*.md`

## 1. 动机与定位

当前 `openagents.observability` 包在 `openagents.*` stdlib logger 树上安装自己的 handler，支持两种输出形态：

- `pretty=False` → 纯文本 `logging.StreamHandler`
- `pretty=True` → `rich.logging.RichHandler`（通过 `[rich]` extra）

两种形态都是**单 sink**。用户场景里一个缺口是"同一进程同时要彩色 stderr + 轮转文件 + JSON 结构化文件"——`rich` 做不到多 sink；stdlib `logging` 多 handler 手工配太啰嗦且和 `LoggingConfig` 的字段模型对不上。

loguru 的独占价值恰好是**多 sink + rotation/retention/compression + serialize=True 的 JSON 行**，用一个 `logger.add(...)` 表达。本次变更以**可选依赖**形式引入 loguru，作为第三种输出形态，和既有两种三选一。

### 非目标

- **不**改动库内任何 `logging.getLogger("openagents.*")` 调用点（20+ 处保持不动）
- **不**让 `openagents` 库代码直接 `from loguru import logger`——loguru 只出现在 observability 内部
- **不**替换或改造现有 EventBus 通道（`FileLoggingEventBus` / `OtelBridge` / `AsyncEventBus` / `RichConsoleEventBus`）：RuntimeEvent 和 logging record 是两条独立通道
- **不**为 `loguru_sinks` 设计环境变量（多 sink 结构塞 env 反人类）
- **不**暴露 `openagents.observability.get_logger()` 新 helper 让业务拿到 `loguru.logger.bind(...)` 入口（属于 structured-logging 重改造，另起 spec）
- **不**动 `seams-and-extension-points.md`：loguru 不是新 seam，只是 observability 内部实现细节

## 2. 架构

### 2.1 三分输出模型

```
openagents.* stdlib loggers ──► observability.configure() 安装 handler
                                    │
                                    ├─ plain StreamHandler      (pretty=False, loguru_sinks=[])
                                    ├─ RichHandler              (pretty=True,  loguru_sinks=[])
                                    └─ _LoguruInterceptHandler  (pretty=False, loguru_sinks=[…])
                                                │
                                                ▼
                                    global loguru.logger ──► N sinks (stderr / file / JSON …)
                                                              每 sink: filter 要求 extra["_openagents"] is True
```

三种形态**互斥**：`pretty=True` 与非空 `loguru_sinks` 由 pydantic `model_validator` 在配置解析期拒绝。

### 2.2 不变量

1. **库代码零改动**——库内所有日志点继续 `logging.getLogger("openagents.*")`
2. **现有过滤链先生效**——`PrefixFilter`/`LevelOverrideFilter`/`RedactFilter` 仍在 stdlib handler 侧运行，**在转发给 loguru 之前**完成 redaction，不会被绕过
3. **library etiquette 不破**——尽管 loguru 是全局单例，我们遵守三条约束：
   - 注册的每个 sink 都带 `filter=lambda r: r["extra"].get("_openagents") is True`——用户自己 `from loguru import logger; logger.add(...)` 加的 sink **不会**收到我们的记录，我们的 sink **也不会**收用户的记录（双向隔离）
   - 每次 `add()` 返回的 sink ID 存在模块级 `_INSTALLED_SINK_IDS`
   - `reset_logging()` 按 ID 精准 `logger.remove(sid)`，**绝不**调 `loguru.logger.remove()`（无参版本会清空全部 sink，包括用户的）
4. **EventBus 不受影响**——`FileLoggingEventBus` 等插件按 JSONL 追加 RuntimeEvent，和 logging record 通道完全独立

### 2.3 模块布局

```
openagents/observability/
├── __init__.py        (修改：导出 LoguruSinkConfig / LoguruNotInstalledError)
├── _rich.py           (不变)
├── _loguru.py         (新增，所有 loguru import 关在此)
├── config.py          (修改：LoguruSinkConfig + loguru_sinks 字段 + model_validator)
├── errors.py          (修改：新增 LoguruNotInstalledError)
├── filters.py         (不变)
├── logging.py         (修改：_build_handler 新分支 + reset_logging 追加调用)
└── redact.py          (不变)
```

`_loguru.py` 照搬 `_rich.py` 的 import-time guard 套路：所有 `from loguru import logger` 放在函数内部或 `_require_loguru()` 内，模块顶层只做 `from __future__ import annotations` 和类型 import。

## 3. 配置

### 3.1 Schema 变更

`openagents/observability/config.py` 新增：

```python
class LoguruSinkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str                              # "stderr" | "stdout" | 绝对/相对文件路径
    level: str = "INFO"                      # 复用 _normalize_level
    format: str | None = None                # loguru format string；None 走 loguru 默认
    serialize: bool = False                  # True → JSON line 输出
    colorize: bool | None = None             # None 走 loguru 自动探测
    rotation: str | None = None              # "10 MB" / "00:00" / "1 week"
    retention: str | None = None             # "7 days"
    compression: str | None = None           # "gz" / "zip"
    enqueue: bool = False                    # 异步 sink（进程内队列）
    filter_include: list[str] | None = None  # 按 logger 前缀再过滤

    @field_validator("level", mode="before")
    @classmethod
    def _v_level(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("level must be a string")
        return _normalize_level(v)


class LoggingConfig(BaseModel):
    # ...既有字段不变...
    loguru_sinks: list[LoguruSinkConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_backend_exclusivity(self) -> "LoggingConfig":
        if self.pretty and self.loguru_sinks:
            raise ValueError(
                "pretty=True and loguru_sinks are mutually exclusive; "
                "use a loguru sink with colorize=True for colored output"
            )
        return self
```

### 3.2 环境变量策略

| env var | 既有/新增 | 行为 |
| --- | --- | --- |
| `OPENAGENTS_LOG_LEVEL` / `STREAM` / `REDACT` / ... | 既有 | 保留原语义，继续覆盖简单字段 |
| `OPENAGENTS_LOG_AUTOCONFIGURE` / `PRETTY` | 既有 | 保留原语义 |
| `OPENAGENTS_LOG_LOGURU_DISABLE` | **新增** | 设为 `1`/`true`/`yes`/`on` 时，`loguru_sinks` 非空也降级为 plain `StreamHandler`（不装 loguru，不注册 sink）——为 CI / debug 提供逃生门 |

**不**为 `loguru_sinks` 结构本身设计 env var。多 sink 配置只能走 `LoggingConfig` 对象或 YAML。

**与 env 合并的交互：** `merge_env_overrides(base)` 会用 `LoggingConfig(**merged)` 重建模型，这意味着 `_check_backend_exclusivity` model_validator 会**再次运行**。如果 YAML 声明了 `loguru_sinks` 而用户又设了 `OPENAGENTS_LOG_PRETTY=1`，合并后会触发互斥校验并抛 `ValidationError`。这是期望行为（loud fail 优于静默二选一），spec 要求实现与测试都覆盖这一路径。

### 3.3 典型配置样例

```yaml
logging:
  level: INFO
  pretty: false
  loguru_sinks:
    - target: stderr
      level: INFO
      colorize: true
    - target: .logs/app.log
      level: DEBUG
      rotation: "10 MB"
      retention: "7 days"
      compression: gz
    - target: .logs/events.jsonl
      level: INFO
      serialize: true
      enqueue: true
```

## 4. 实现骨架

### 4.1 `_loguru.py`

```python
from __future__ import annotations
import logging
import sys
from typing import Any, Callable

from openagents.observability.errors import LoguruNotInstalledError
from openagents.observability.config import LoguruSinkConfig

_INSTALLED_SINK_IDS: list[int] = []


def _require_loguru() -> Any:
    try:
        from loguru import logger as _lg
    except ImportError as exc:
        raise LoguruNotInstalledError() from exc
    return _lg


def _sink_filter(cfg_filter_include: list[str] | None) -> Callable[[dict], bool]:
    def _f(record: dict) -> bool:
        extra = record["extra"]
        if extra.get("_openagents") is not True:
            return False
        if cfg_filter_include is None:
            return True
        name = extra.get("_oa_name", "")
        return any(name == p or name.startswith(p + ".") for p in cfg_filter_include)
    return _f


def install_sinks(sinks: list[LoguruSinkConfig]) -> None:
    """全局 loguru.logger.add() 每个 sink；记 id 供 reset 回收。
    任一 sink 构造失败抛出前，会回滚本次已装的 sink。"""
    logger = _require_loguru()
    batch: list[int] = []
    try:
        for cfg in sinks:
            if cfg.target == "stderr":
                target: Any = sys.stderr
            elif cfg.target == "stdout":
                target = sys.stdout
            else:
                target = cfg.target  # 字符串路径 → loguru 自动建文件 sink
            kwargs: dict[str, Any] = dict(
                level=cfg.level,
                filter=_sink_filter(cfg.filter_include),
                enqueue=cfg.enqueue,
                serialize=cfg.serialize,
            )
            if cfg.format is not None:
                kwargs["format"] = cfg.format
            if cfg.colorize is not None:
                kwargs["colorize"] = cfg.colorize
            if cfg.rotation is not None:
                kwargs["rotation"] = cfg.rotation
            if cfg.retention is not None:
                kwargs["retention"] = cfg.retention
            if cfg.compression is not None:
                kwargs["compression"] = cfg.compression
            sink_id = logger.add(target, **kwargs)
            batch.append(sink_id)
        _INSTALLED_SINK_IDS.extend(batch)
    except Exception:
        for sid in batch:
            try:
                logger.remove(sid)
            except ValueError:
                pass
        raise


def remove_installed_sinks() -> None:
    """只移我们装的，绝不碰用户装的。"""
    try:
        from loguru import logger
    except ImportError:
        _INSTALLED_SINK_IDS.clear()
        return
    for sid in _INSTALLED_SINK_IDS:
        try:
            logger.remove(sid)
        except ValueError:
            pass  # 已被外部移除，忽略
    _INSTALLED_SINK_IDS.clear()


# Python LogRecord 的标准字段集合；与 filters.RedactFilter 中的 skip set 一致
_LOGRECORD_STD_ATTRS: frozenset[str] = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
})


class _LoguruInterceptHandler(logging.Handler):
    """stdlib LogRecord → loguru.logger 的转发器。

    采用 loguru 官方 README 推荐的 InterceptHandler 模式：
    - 动态帧回溯求 depth（而非固定值）——在 stdlib logging 内部栈帧走完之前不停
    - level 名→loguru level 查找带 ValueError fallback，自定义数字级别也不丢
    - 非 underscore / 非标准 LogRecord 字段作为 extras 透传，让 RedactFilter
      过滤后的结果实际落到 serialize=True 的 JSON sink 里
    """

    def __init__(self) -> None:
        super().__init__()
        self._openagents_installed = True  # 对齐既有 reset_logging 回收
        self._logger = _require_loguru()  # 早失败

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 1. level 查找 + 数字 fallback（官方 InterceptHandler 模式）
            try:
                level = self._logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            # 2. 动态 depth：从当前帧向上走，跳过 stdlib logging 内部帧
            import logging as _logging_mod
            frame = logging.currentframe()
            depth = 2
            while frame and frame.f_code.co_filename == _logging_mod.__file__:
                frame = frame.f_back
                depth += 1

            # 3. 收集 extras：跳过下划线开头和 LogRecord 标准字段
            extras: dict[str, Any] = {}
            for key, value in record.__dict__.items():
                if key.startswith("_") or key in _LOGRECORD_STD_ATTRS:
                    continue
                extras[key] = value
            extras["_openagents"] = True
            extras["_oa_name"] = record.name

            self._logger.bind(**extras).opt(
                depth=depth,
                exception=record.exc_info,
            ).log(level, record.getMessage())
        except Exception:
            self.handleError(record)
```

### 4.2 `logging.py` 改动

```python
def _build_handler(config: LoggingConfig) -> logging.Handler:
    loguru_disabled = _env_value("OPENAGENTS_LOG_LOGURU_DISABLE")
    loguru_disabled_flag = (
        loguru_disabled is not None
        and loguru_disabled.lower() in {"1", "true", "yes", "on"}
    )
    if config.loguru_sinks and loguru_disabled_flag:
        _OBS_LOGGER.warning(
            "OPENAGENTS_LOG_LOGURU_DISABLE set; %d loguru sink(s) skipped, "
            "falling back to plain StreamHandler",
            len(config.loguru_sinks),
        )
    if config.loguru_sinks and not loguru_disabled_flag:
        from openagents.observability._loguru import (
            _LoguruInterceptHandler,
            install_sinks,
        )
        install_sinks(config.loguru_sinks)
        return _LoguruInterceptHandler()
    if config.pretty:
        from openagents.observability._rich import make_rich_handler
        return make_rich_handler(
            stream=config.stream,
            show_time=config.show_time,
            show_path=config.show_path,
        )
    stream = sys.stderr if config.stream == "stderr" else sys.stdout
    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s"))
    handler._openagents_installed = True  # type: ignore[attr-defined]
    return handler


def reset_logging() -> None:
    # ... 既有逻辑 ...
    try:
        from openagents.observability._loguru import remove_installed_sinks
        remove_installed_sinks()
    except ImportError:
        pass  # loguru 未装，无 sink 需清理
```

`configure()` 在 `_build_handler()` 抛异常时必须保证 sink 已回滚——`install_sinks()` 自身的 batch rollback 已负责；`configure()` 层额外加一个 try：handler 构造成功后、后续 `addFilter` 链条任何一步失败时，也要 `remove_installed_sinks()` + 重置 root logger 状态，确保不留半配置。

**载荷不变量（必须写死在实现里并被测试锁定）：** `configure()` 第一行调用 `reset_logging()` 清空所有既有状态，紧接着 `_build_handler()` 里 `install_sinks()` 才会往 `_INSTALLED_SINK_IDS` 写新 sink ID。这意味着：后续 filter 链路失败触发的 `remove_installed_sinks()` 调用，`_INSTALLED_SINK_IDS` 里**有且仅有**本次 `configure()` 本调用刚安装的 batch——不会误杀任何前一次 configure 残留。这个顺序是 rollback 正确性的载荷不变量，不可调换。

### 4.3 实现要点

| 选择 | 理由 |
| --- | --- |
| 动态 `depth` 帧回溯 | 让 loguru 的 `{name}:{function}:{line}` 指向业务调用点；固定 `depth=N` 在 `LoggerAdapter` / 额外 filter / Python 版本差异下会指错。采用官方 README 的 InterceptHandler pattern——从 `logging.currentframe()` 出发，跳过文件名等于 `logging.__file__` 的所有栈帧 |
| Level 名查找 + 数字 fallback | `logger.level(record.levelname).name` 覆盖标准名；`ValueError` 分支回落到 `record.levelno` 支持用户自定义数字级别，不静默丢记录 |
| 非标准字段作为 extras 透传到 `bind(**extras)` | LogRecord 上的自定义字段（`logger.info("msg", extra={"request_id": ...})` 或直接写属性）要流进 loguru `record["extra"]`，否则 `serialize=True` JSON sink 拿不到结构化字段；使用和 `RedactFilter.skip` 同一份标准字段集合避免双份定义漂移 |
| `bind(_openagents=True, _oa_name=record.name)` 而非改全局 state | 每条记录带 tag，sink filter 靠 tag 隔离 |
| sink ID 存 `list` 而非 `set` | 保留 `add` 顺序；与 loguru 内部习惯一致 |
| `_require_loguru()` 在 handler `__init__` 就调 | 早失败；不等到首条日志才抛 |
| 逃生门用 `OPENAGENTS_LOG_LOGURU_DISABLE` 而非 `LoggingConfig` 字段 | 配置级字段意味着"声明性开关"；逃生门是**运行时**紧急降级，放环境变量更贴近用途 |

## 5. 错误处理

### 5.1 新增错误类

```python
# openagents/observability/errors.py
class LoguruNotInstalledError(ImportError):
    """loguru_sinks 非空但 loguru 未装。镜像 RichNotInstalledError。"""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "loguru is required for loguru_sinks. "
               "Install with: pip install io-openagent-sdk[loguru]"
        )
```

### 5.2 错误语义表

| 触发场景 | 行为 |
| --- | --- |
| `loguru_sinks` 非空，loguru 未装，无 `DISABLE` env | `_LoguruInterceptHandler.__init__` 里 `_require_loguru()` 抛 `LoguruNotInstalledError`；`configure()` 需确保 sink 未装 / 已回滚 |
| `loguru_sinks` 非空，loguru 未装，`DISABLE=1` | 降级为 plain `StreamHandler`，不抛；发一条 WARNING 到 `_OBS_LOGGER` 告知用户降级 |
| `logger.add(target, rotation="bad")` 抛 | `install_sinks()` batch rollback（已装的本次 sink 逐个 remove），原异常向上传播 |
| sink 运行时写失败（磁盘满等） | 由 loguru 内部 `catch=True` 默认处理，打到 stderr——本 spec 不改这一层 |

## 6. 测试策略

### 6.1 文件结构

- 新增 `tests/unit/observability/test_loguru_integration.py`（需 loguru，加 `pytest.importorskip("loguru")` 模块级守卫）
- 修改 `tests/unit/observability/test_logging_config.py` 补 schema 用例（不依赖 loguru 运行时）
- 修改 `tests/unit/observability/test_app_config_logging.py` 补 yaml/dict 往返用例（如 `AppConfig` 透传 `LoggingConfig`）

### 6.2 测试用例清单

| # | 用例 | 依赖 loguru |
|---|---|---|
| 0 | `configure(loguru_sinks=[stderr])` → 发记录 → sink 收到，记录的 `function` / `line` 指向业务 caller 而非 handler（验证动态 depth 帧回溯） | ✅ |
| 1 | `configure(loguru_sinks=[stderr])` → 发记录 → sink 收到 | ✅ |
| 2 | 多 sink：stderr colorize + file serialize → 各收各的 | ✅ |
| 3 | 用户自装 loguru sink 后 `configure(...)` → 用户 sink 不收 openagents 记录 | ✅ |
| 4 | `configure(...)` + `reset_logging()` → 用户 sink 不被误杀 | ✅ |
| 5 | `configure(...)` 两次（sink 集合不同）→ 只剩新配置，旧 sink 全清 | ✅ |
| 6 | `LoggingConfig(pretty=True, loguru_sinks=[...])` → pydantic `model_validator` 拒绝 | ❌ |
| 7 | `loguru_sinks` 非空 + loguru 未装 → `LoguruNotInstalledError`（pip 指令在消息里） | 需模拟 import 失败 |
| 8 | `OPENAGENTS_LOG_LOGURU_DISABLE=1` → 降级 plain `StreamHandler`，发 WARNING | ❌ |
| 9 | `logger.info("...", extra={"api_key": "sk-xxx", "request_id": "r-1"})` + `RedactFilter` → `serialize=True` sink 收到 JSON，`api_key` 值被 redact、`request_id` 原样保留 | ✅ |
| 10 | `logger.exception("boom")` 的 traceback 传到 loguru 侧（`opt(exception=…)` 生效） | ✅ |
| 11 | `install_sinks` 中途一个 rotation 字符串非法 → 已装的本次 sink 回滚，异常向上抛 | ✅ |
| 12 | `reset_logging()` 幂等：空状态下重复调不抛 | ✅ |
| 13 | 自定义数字级别（`logging.addLevelName(25, "VERBOSE")`）→ 不被 emit 静默丢弃，loguru 侧以数字级别记录 | ✅ |
| 14 | `LoggingConfig(loguru_sinks=[...])` + `OPENAGENTS_LOG_PRETTY=1` env → `merge_env_overrides` 重建时触发互斥校验并抛 `ValidationError` | ❌ |
| 15 | 非 underscore 的 record 自定义属性流入 loguru `extra`：发 `logger.info("x", extra={"request_id": "r-1"})` → `serialize=True` sink 的 JSON 输出里能看到 `record.extra.request_id == "r-1"` | ✅ |

fixture 复用既有 `_reset_before_and_after` autouse 模式，同时扩展清理 loguru 侧残留。

### 6.3 Coverage

- `openagents/observability/_loguru.py` 加入 `pyproject.toml` 的 `tool.coverage.report.omit`（和 `mem0_memory.py` / `mcp_tool.py` / `sqlite_backed.py` / `otel_bridge.py` / `litellm_client.py` 同等待遇）——CI 若未装 loguru 跑不到该文件
- `LoguruNotInstalledError` 本身在 `errors.py`，**不**在 omit，纳入 92% 底线
- `LoguruSinkConfig` + `_check_backend_exclusivity` model_validator 在 `config.py`，**不**在 omit

### 6.4 dev extras 策略

`pyproject.toml` `dev` 追加 `loguru>=0.7.0`，理由：loguru 是纯 Python、无 C 扩展、体积小（<200KB），与 `rich` 同等级；本地跑 `uv run pytest -q` 默认覆盖用例 1-5/9-12。

## 7. pyproject.toml 改动

```toml
[project.optional-dependencies]
# ... 既有 extras ...
loguru = [
    "loguru>=0.7.0",
]
all = [
    "io-openagent-sdk[cli,mcp,mem0,openai,otel,rich,sqlite,dev,tokenizers,yaml,pptx,langfuse,phoenix,litellm,loguru]",
]
dev = [
    # ... 既有 dev 依赖 ...
    "io-openagent-sdk[rich]",
    "litellm>=1.50.0",
    "loguru>=0.7.0",   # 新增
]

[tool.coverage.report]
omit = [
    # ... 既有 omit ...
    "openagents/observability/_loguru.py",
]
```

## 8. 文档改动

### 8.1 `docs/observability.md` + `.en.md`

新增一节「多 sink 日志（loguru）」，含：
- 概念说明：与 plain / rich 三选一，以及"为什么需要 loguru"（多 sink、rotation、JSON）
- 完整 YAML 样例（stderr colorize + file rotation + JSON sink 三 sink 经典组合）
- 约束声明：
  - 与 `pretty=True` 互斥
  - 不触碰用户自己注册的 loguru sink
  - `OPENAGENTS_LOG_LOGURU_DISABLE=1` 逃生门
  - **不覆盖 EventBus 通道**（`FileLoggingEventBus` 仍是 RuntimeEvent 的 NDJSON 归档工具，作用域不同）
- `LoguruNotInstalledError` 和对应 pip 指令

### 8.2 `docs/configuration.md` + `.en.md`

`LoggingConfig` 字段表补 `loguru_sinks`；新增 `LoguruSinkConfig` 字段表。

### 8.3 `docs/repository-layout.md` + `.en.md`

`openagents/observability/` 目录说明追加一行 `_loguru.py`（对齐 `_rich.py` 的表述）。

### 8.4 不动的文档

- `docs/seams-and-extension-points.md`：loguru 不是新 seam
- `docs/plugin-development.md`：loguru 不是插件
- `docs/developer-guide.md`：核心架构章节无需改

## 9. 交付清单摘要

**代码：**

| 文件 | 类型 |
| --- | --- |
| `pyproject.toml` | 改：新增 `loguru` extra，`all`/`dev` 追加，`coverage.omit` 追加 |
| `openagents/observability/errors.py` | 改：新增 `LoguruNotInstalledError` |
| `openagents/observability/_loguru.py` | **新文件** |
| `openagents/observability/config.py` | 改：`LoguruSinkConfig` + `loguru_sinks` 字段 + `model_validator` |
| `openagents/observability/logging.py` | 改：`_build_handler` 分支 + `reset_logging` 追加调用 + `configure` 回滚 |
| `openagents/observability/__init__.py` | 改：导出 `LoguruSinkConfig`、`LoguruNotInstalledError` |

**测试：**

| 文件 | 类型 |
| --- | --- |
| `tests/unit/observability/test_loguru_integration.py` | **新文件**（10+ 用例） |
| `tests/unit/observability/test_logging_config.py` | 改：schema 用例补 `loguru_sinks` 解析 + 互斥校验 |
| `tests/unit/observability/test_app_config_logging.py` | 改：yaml/dict 往返用例补 `loguru_sinks` |

**文档：** `docs/observability.*.md`、`docs/configuration.*.md`、`docs/repository-layout.*.md` 三对 bilingual。

## 10. 不做的事（YAGNI 清单）

- 不在 env var 里支持 `loguru_sinks` 多 sink 结构
- 不改库内 `logging.getLogger(...)` 任何调用点
- 不把 `file_logging` / `otel_bridge` / `async_event_bus` 改成 loguru sink
- 不暴露 `openagents.observability.get_logger()` 新 helper
- 不支持 `loguru.bind()` 上下文在业务代码中的回流
- 不为"hot-swap sinks on reload"单独建 API——`Runtime.reload()` 走 `configure()` 整条重装路径，天然支持
- 不集成 loguru 的 `@logger.catch` 装饰器风格到库内——保持 stdlib 风格的职责边界
