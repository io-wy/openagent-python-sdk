# Optional Loguru Multi-Sink Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `loguru` as a third optional logging output mode alongside plain `StreamHandler` and `rich` `RichHandler`, driven by a new `loguru_sinks: list[LoguruSinkConfig]` field on `LoggingConfig`. Library code keeps using stdlib `logging.getLogger(...)`; a new `_LoguruInterceptHandler` forwards `LogRecord`s into `loguru.logger` with tagged extras so library-owned sinks are cleanly isolated from sinks the user's app may install.

**Architecture:** Three mutually-exclusive output modes selected in `_build_handler()`:
- `pretty=False, loguru_sinks=[]` → plain `StreamHandler` (unchanged)
- `pretty=True,  loguru_sinks=[]` → `RichHandler` via existing `_rich.py` (unchanged)
- `pretty=False, loguru_sinks=[…]` → **new** `_LoguruInterceptHandler` + N loguru sinks installed on the global `loguru.logger`, each filtered to `record["extra"]["_openagents"] is True`. A module-level `_INSTALLED_SINK_IDS` list tracks sink IDs so `reset_logging()` can tear down only what we installed.

**Tech Stack:** Python 3.10+ · `loguru>=0.7.0` (new optional extra) · pydantic v2 `model_validator` · pytest + `pytest.importorskip("loguru")` · `uv` package manager · `rtk` CLI wrapper.

**Spec:** `docs/superpowers/specs/2026-04-22-loguru-optional-logging-design.md`

---

## File Structure

### Created

- `openagents/observability/_loguru.py` — all loguru imports behind import-time guards; defines `_require_loguru`, `_sink_filter`, `install_sinks`, `remove_installed_sinks`, `_LoguruInterceptHandler`, `_INSTALLED_SINK_IDS`. Mirrors `_rich.py` in shape. (~150 lines)
- `tests/unit/observability/test_loguru_integration.py` — module-level `pytest.importorskip("loguru")`; 13 test cases covering intercept, filter isolation, multi-sink, rollback, depth, extras, exception, level fallback, reset idempotency. (~450 lines)

### Modified

- `pyproject.toml` — add `loguru` to `[project.optional-dependencies]`, append to `dev` and `all`, add `openagents/observability/_loguru.py` to `[tool.coverage.report].omit`
- `openagents/observability/errors.py` — add `LoguruNotInstalledError(ImportError)` mirroring `RichNotInstalledError`
- `openagents/observability/filters.py` — extract inline `skip` set in `RedactFilter.filter()` to module-level `_LOGRECORD_STD_ATTRS: frozenset[str]`; `RedactFilter` references it. Single source of truth, shared with `_loguru.py`
- `openagents/observability/config.py` — add `LoguruSinkConfig` (extra="forbid"); add `loguru_sinks: list[LoguruSinkConfig]` field on `LoggingConfig`; add `_check_backend_exclusivity` `model_validator(mode="after")`; add `OPENAGENTS_LOG_LOGURU_DISABLE` to `_FIELD_ENV_MAP` semantics (read at handler build time, not stored on the config)
- `openagents/observability/logging.py` — `_build_handler` gains loguru branch + `OPENAGENTS_LOG_LOGURU_DISABLE` downgrade-with-WARNING path; `reset_logging` calls `remove_installed_sinks()` under ImportError guard; `configure()` wraps the `_build_handler → addFilter` section in try/except to guarantee full rollback on filter-wiring failure
- `openagents/observability/__init__.py` — export `LoguruSinkConfig`, `LoguruNotInstalledError`
- `tests/unit/observability/test_logging_config.py` — add schema test cases for `LoguruSinkConfig` and `_check_backend_exclusivity` (no loguru runtime dependency)
- `tests/unit/observability/test_app_config_logging.py` — add YAML/dict roundtrip test for `loguru_sinks`
- `tests/unit/observability/test_filters.py` — add test asserting `_LOGRECORD_STD_ATTRS` is the same object referenced from `_loguru.py`
- `docs/observability.md`, `docs/observability.en.md` — new "multi-sink (loguru)" section with full YAML example
- `docs/configuration.md`, `docs/configuration.en.md` — `LoggingConfig` field table: add `loguru_sinks`; new `LoguruSinkConfig` field table; env var table: add `OPENAGENTS_LOG_LOGURU_DISABLE`
- `docs/repository-layout.md`, `docs/repository-layout.en.md` — `observability/` directory listing: add `_loguru.py` row

### Not modified (explicit non-scope per spec §1 and §10)

- Any `logging.getLogger("openagents.*")` call site in the library (20+ locations left untouched)
- `openagents/plugins/builtin/events/*.py` — EventBus channel is orthogonal to the logging-record channel
- `docs/seams-and-extension-points.md`, `docs/plugin-development.md`, `docs/developer-guide.md` — loguru is observability-internal, not a new seam

---

## Reference Patterns

Implementers should skim these before starting:

- `openagents/observability/_rich.py` — the exact shape `_loguru.py` mirrors (`_require_<lib>()` import-time guard, factory functions used by `logging.py::_build_handler`)
- `openagents/observability/errors.py::RichNotInstalledError` — error-class shape being mirrored
- `openagents/observability/logging.py` — the `configure()` / `reset_logging()` / `_build_handler()` flow being extended; note the `_openagents_installed=True` handler tag pattern used by `reset_logging` to know what to clean up
- `openagents/observability/filters.py::RedactFilter.filter` — the `skip` set being extracted (currently defined inline at method body, lines 74-96)
- `openagents/observability/config.py::_FIELD_ENV_MAP` and `merge_env_overrides` — the env-override machinery whose re-validation triggers spec test case 14
- `tests/unit/observability/test_configure.py` — the `_reset_before_and_after` autouse fixture pattern and `_installed_handlers()` probe (reuse these idioms)
- loguru 0.7.x official README's "Entirely compatible with standard logging" section — canonical InterceptHandler pattern that task 8's `emit()` reproduces (frame walking + `logger.level().name` + `.opt(depth=..., exception=...)`)

---

## Convention Notes

- **Commands use `rtk` wrapper** per `C:\Users\qwdma\.claude\CLAUDE.md`: `rtk uv run pytest ...`, `rtk git commit ...`.
- **Tests use `uv run pytest -q`** per repo `CLAUDE.md`; never bare `pytest`.
- **Coverage floor: 92%** (`pyproject.toml`). `_loguru.py` is added to `omit`, so its absence in CI (if loguru extra isn't installed) doesn't break the floor. `errors.py`, `config.py`, `filters.py`, `logging.py` contributions are **not** omitted and must carry real test coverage.
- **Commits:** one per task, conventional-commits (`feat:`, `test:`, `refactor:`, `docs:`, `chore:`).
- **TDD:** every source-change task opens with failing tests, implements minimally to green, then commits.
- **Co-evolve tests and source** per repo `CLAUDE.md` — do not land source changes without the matching test update in the same commit.
- **`pytest.importorskip("loguru")`** at module top of `test_loguru_integration.py`, so CI without the extra cleanly skips (doesn't error).
- **Library etiquette invariant:** never call `loguru.logger.remove()` without a sink ID; only remove IDs we ourselves installed. This is the behavior that spec test case 4 locks in.

---

## Task 1: pyproject.toml — add loguru extra, dev+all, coverage omit

**Files:**
- Modify: `D:\Project\openagent-python-sdk\pyproject.toml`

**Rationale:** Make `loguru` installable before any source change imports it. Pure config; verified by import smoke test.

- [ ] **Step 1: Add `loguru` optional extra**

In `pyproject.toml`, inside `[project.optional-dependencies]`, insert:

```toml
loguru = [
    "loguru>=0.7.0",
]
```

- [ ] **Step 2: Append `loguru` to `dev` and `all`**

Modify `dev` (currently `["coverage[toml]>=7.6.0", ..., "litellm>=1.50.0"]`) to append `"loguru>=0.7.0"`.

Modify `all` to add `loguru` to its list:

```toml
all = [
    "io-openagent-sdk[cli,mcp,mem0,openai,otel,rich,sqlite,dev,tokenizers,yaml,pptx,langfuse,phoenix,litellm,loguru]",
]
```

- [ ] **Step 3: Add `_loguru.py` to coverage omit**

Extend `[tool.coverage.report].omit` with `"openagents/observability/_loguru.py"`.

- [ ] **Step 4: Sync deps**

Run: `rtk uv sync --extra dev --extra loguru`
Expected: exits 0; `loguru` appears in `uv.lock`.

- [ ] **Step 5: Smoke-verify loguru importable**

Run: `rtk uv run python -c "import loguru; print(loguru.__version__)"`
Expected: a version string `>= 0.7.0`; exit 0.

- [ ] **Step 6: Confirm full suite still green**

Run: `rtk uv run pytest -q`
Expected: all existing tests pass.

- [ ] **Step 7: Commit**

```bash
rtk git add pyproject.toml uv.lock
rtk git commit -m "chore(deps): add loguru as optional extra and coverage omit"
```

---

## Task 2: LoguruNotInstalledError

**Files:**
- Modify: `openagents/observability/errors.py`
- Modify: `tests/unit/observability/test_configure.py` (add a small class `TestLoguruNotInstalledError`) — co-locate with the mirror `TestRichNotInstalledError` pattern if already present; otherwise write tests in a new file `tests/unit/observability/test_errors.py`
- Modify: `openagents/observability/__init__.py` (export)

**Rationale:** Mirror `RichNotInstalledError` exactly — same message contract, same `ImportError` subclass, same optional `message` override. Tiny, isolated, easy to TDD.

- [ ] **Step 1: Write failing tests**

Create `D:\Project\openagent-python-sdk\tests\unit\observability\test_errors.py` (if it doesn't exist) or append to `test_configure.py` a new class:

```python
import pytest


class TestLoguruNotInstalledError:
    def test_is_importerror_subclass(self):
        from openagents.observability import LoguruNotInstalledError
        assert issubclass(LoguruNotInstalledError, ImportError)

    def test_default_message_contains_pip_hint(self):
        from openagents.observability import LoguruNotInstalledError
        exc = LoguruNotInstalledError()
        assert "loguru" in str(exc)
        assert "pip install io-openagent-sdk[loguru]" in str(exc)

    def test_accepts_custom_message(self):
        from openagents.observability import LoguruNotInstalledError
        exc = LoguruNotInstalledError("custom")
        assert str(exc) == "custom"

    def test_raisable(self):
        from openagents.observability import LoguruNotInstalledError
        with pytest.raises(LoguruNotInstalledError):
            raise LoguruNotInstalledError()
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_errors.py`
Expected: all 4 tests FAIL with `ImportError: cannot import name 'LoguruNotInstalledError'`.

- [ ] **Step 3: Add the error class**

In `openagents/observability/errors.py`, append:

```python
class LoguruNotInstalledError(ImportError):
    """Raised when loguru-backed multi-sink logging is requested but loguru is missing.

    Mirrors RichNotInstalledError: fail loud with the exact pip command.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "loguru is required for loguru_sinks. "
               "Install with: pip install io-openagent-sdk[loguru]"
        )
```

- [ ] **Step 4: Export from `__init__.py`**

In `openagents/observability/__init__.py`, add `LoguruNotInstalledError` to the imports from `errors` and to `__all__`:

```python
from openagents.observability.errors import (
    LoguruNotInstalledError,
    RichNotInstalledError,
)

__all__ = [
    "LoggingConfig",
    "LoguruNotInstalledError",
    "RichNotInstalledError",
    "configure",
    "configure_from_env",
    "reset_logging",
]
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_errors.py`
Expected: all 4 PASS.

- [ ] **Step 6: Commit**

```bash
rtk git add openagents/observability/errors.py openagents/observability/__init__.py tests/unit/observability/test_errors.py
rtk git commit -m "feat(observability): add LoguruNotInstalledError mirroring RichNotInstalledError"
```

---

## Task 3: Extract `_LOGRECORD_STD_ATTRS` to module level in filters.py

**Files:**
- Modify: `openagents/observability/filters.py`
- Modify: `tests/unit/observability/test_filters.py`

**Rationale:** The standard LogRecord attribute set (26 names) is currently inlined in `RedactFilter.filter()` (lines 74-96). `_loguru.py` needs the same set. Extracting to a module-level `frozenset` gives a single source of truth; `_loguru.py` imports it by identity. Breaking this out as its own task lets us verify no behavior regression in `RedactFilter` before the loguru code depends on it.

- [ ] **Step 1: Write failing tests**

Open `D:\Project\openagent-python-sdk\tests\unit\observability\test_filters.py` and append:

```python
def test_logrecord_std_attrs_is_frozenset_at_module_level():
    from openagents.observability.filters import _LOGRECORD_STD_ATTRS
    assert isinstance(_LOGRECORD_STD_ATTRS, frozenset)
    # Spot-check the well-known LogRecord attributes
    for name in ("msg", "args", "levelname", "levelno", "name", "exc_info"):
        assert name in _LOGRECORD_STD_ATTRS


def test_logrecord_std_attrs_covers_all_redactfilter_skips():
    """RedactFilter's skip set was originally inline; the constant must be
    a superset of (or equal to) those 26 attribute names. This test
    guards against accidental shrinkage."""
    from openagents.observability.filters import _LOGRECORD_STD_ATTRS
    canonical = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
    }
    assert canonical <= _LOGRECORD_STD_ATTRS
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_filters.py -k logrecord_std_attrs`
Expected: FAIL with `ImportError: cannot import name '_LOGRECORD_STD_ATTRS'`.

- [ ] **Step 3: Extract the constant**

In `openagents/observability/filters.py`, add at module top (below `_LEVEL_NAMES`):

```python
# Standard LogRecord attribute names. Extracted to module level so _loguru.py
# can import the exact same frozenset by identity, preventing drift between
# RedactFilter's skip set and the intercept handler's extras harvester.
_LOGRECORD_STD_ATTRS: frozenset[str] = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
})
```

Replace the inline `skip = {...}` construction inside `RedactFilter.filter()` with a reference:

```python
def filter(self, record: logging.LogRecord) -> bool:
    for key in list(record.__dict__.keys()):
        if key.startswith("_") or key in _LOGRECORD_STD_ATTRS:
            continue
        wrapped = {key: record.__dict__[key]}
        redacted = redact(wrapped, keys=self._keys, max_value_length=self._max)
        record.__dict__[key] = redacted[key]
    return True
```

- [ ] **Step 4: Run full filters test suite — confirm no regression**

Run: `rtk uv run pytest -q tests/unit/observability/test_filters.py`
Expected: **all** filter tests pass, including the two new ones and every pre-existing `RedactFilter` test.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/observability/filters.py tests/unit/observability/test_filters.py
rtk git commit -m "refactor(observability): extract _LOGRECORD_STD_ATTRS to module level in filters.py"
```

---

## Task 4: LoguruSinkConfig pydantic model

**Files:**
- Modify: `openagents/observability/config.py`
- Modify: `tests/unit/observability/test_logging_config.py`

**Rationale:** The `LoguruSinkConfig` model is pure schema — no runtime loguru dependency. TDD the validation rules before wiring it into `LoggingConfig`.

- [ ] **Step 1: Write failing tests**

In `tests/unit/observability/test_logging_config.py`, append a new class:

```python
class TestLoguruSinkConfig:
    def test_minimal_config(self):
        from openagents.observability.config import LoguruSinkConfig
        cfg = LoguruSinkConfig(target="stderr")
        assert cfg.target == "stderr"
        assert cfg.level == "INFO"
        assert cfg.serialize is False
        assert cfg.enqueue is False
        assert cfg.format is None
        assert cfg.rotation is None
        assert cfg.filter_include is None

    def test_all_fields(self):
        from openagents.observability.config import LoguruSinkConfig
        cfg = LoguruSinkConfig(
            target=".logs/app.log",
            level="DEBUG",
            format="{time} {level} {message}",
            serialize=True,
            colorize=False,
            rotation="10 MB",
            retention="7 days",
            compression="gz",
            enqueue=True,
            filter_include=["openagents.llm", "openagents.runtime"],
        )
        assert cfg.target == ".logs/app.log"
        assert cfg.level == "DEBUG"
        assert cfg.rotation == "10 MB"
        assert cfg.filter_include == ["openagents.llm", "openagents.runtime"]

    def test_level_normalized_case(self):
        from openagents.observability.config import LoguruSinkConfig
        cfg = LoguruSinkConfig(target="stderr", level="warning")
        assert cfg.level == "WARNING"

    def test_level_invalid_rejected(self):
        import pytest
        from pydantic import ValidationError
        from openagents.observability.config import LoguruSinkConfig
        with pytest.raises(ValidationError):
            LoguruSinkConfig(target="stderr", level="BOGUS")

    def test_unknown_field_rejected(self):
        import pytest
        from pydantic import ValidationError
        from openagents.observability.config import LoguruSinkConfig
        with pytest.raises(ValidationError):
            LoguruSinkConfig(target="stderr", rotate_when_full=True)  # typo

    def test_target_required(self):
        import pytest
        from pydantic import ValidationError
        from openagents.observability.config import LoguruSinkConfig
        with pytest.raises(ValidationError):
            LoguruSinkConfig()
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_logging_config.py::TestLoguruSinkConfig`
Expected: all 6 FAIL with `ImportError: cannot import name 'LoguruSinkConfig'`.

- [ ] **Step 3: Implement `LoguruSinkConfig`**

In `openagents/observability/config.py`, add above `class LoggingConfig`:

```python
class LoguruSinkConfig(BaseModel):
    """Configuration for a single loguru sink.

    Fields map directly onto ``loguru.logger.add(...)`` kwargs.
    Leaving optional fields as ``None`` means loguru's default applies.
    """

    model_config = ConfigDict(extra="forbid")

    target: str
    level: str = "INFO"
    format: str | None = None
    serialize: bool = False
    colorize: bool | None = None
    rotation: str | None = None
    retention: str | None = None
    compression: str | None = None
    enqueue: bool = False
    filter_include: list[str] | None = None

    @field_validator("level", mode="before")
    @classmethod
    def _v_level(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("level must be a string")
        return _normalize_level(value)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_logging_config.py::TestLoguruSinkConfig`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/observability/config.py tests/unit/observability/test_logging_config.py
rtk git commit -m "feat(observability): add LoguruSinkConfig pydantic schema"
```

---

## Task 5: LoggingConfig.loguru_sinks + `_check_backend_exclusivity`

**Files:**
- Modify: `openagents/observability/config.py`
- Modify: `tests/unit/observability/test_logging_config.py`

**Rationale:** Wire the new sink list onto `LoggingConfig` and add the mutual-exclusion validator. Also verify `merge_env_overrides` re-runs validation (spec test case 14).

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/observability/test_logging_config.py`:

```python
class TestLoggingConfigLoguruSinks:
    def test_default_is_empty_list(self):
        from openagents.observability.config import LoggingConfig
        cfg = LoggingConfig()
        assert cfg.loguru_sinks == []

    def test_accepts_list_of_dicts(self):
        from openagents.observability.config import LoggingConfig, LoguruSinkConfig
        cfg = LoggingConfig(loguru_sinks=[{"target": "stderr", "level": "WARNING"}])
        assert len(cfg.loguru_sinks) == 1
        assert isinstance(cfg.loguru_sinks[0], LoguruSinkConfig)
        assert cfg.loguru_sinks[0].level == "WARNING"

    def test_pretty_and_loguru_sinks_mutually_exclusive(self):
        import pytest
        from pydantic import ValidationError
        from openagents.observability.config import LoggingConfig
        with pytest.raises(ValidationError, match="mutually exclusive"):
            LoggingConfig(pretty=True, loguru_sinks=[{"target": "stderr"}])

    def test_pretty_false_with_sinks_ok(self):
        from openagents.observability.config import LoggingConfig
        cfg = LoggingConfig(pretty=False, loguru_sinks=[{"target": "stderr"}])
        assert cfg.pretty is False
        assert len(cfg.loguru_sinks) == 1

    def test_pretty_true_without_sinks_ok(self):
        from openagents.observability.config import LoggingConfig
        cfg = LoggingConfig(pretty=True)
        assert cfg.pretty is True
        assert cfg.loguru_sinks == []

    def test_env_merge_triggers_revalidation(self, monkeypatch):
        """Spec test case 14: setting OPENAGENTS_LOG_PRETTY=1 via env, while
        base config has loguru_sinks, must re-fire the model_validator and
        raise pydantic.ValidationError — not a generic Exception."""
        import pytest
        from pydantic import ValidationError
        from openagents.observability.config import (
            LoggingConfig,
            merge_env_overrides,
        )
        base = LoggingConfig(loguru_sinks=[{"target": "stderr"}])
        monkeypatch.setenv("OPENAGENTS_LOG_PRETTY", "1")
        with pytest.raises(ValidationError, match="mutually exclusive"):
            merge_env_overrides(base)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_logging_config.py::TestLoggingConfigLoguruSinks`
Expected: all 6 FAIL (field doesn't exist).

- [ ] **Step 3: Implement**

In `openagents/observability/config.py`:

(a) Import `model_validator` alongside existing pydantic imports:
```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

(b) Add the field on `LoggingConfig` (after existing fields):
```python
loguru_sinks: list[LoguruSinkConfig] = Field(default_factory=list)
```

(c) Add the validator at the bottom of `LoggingConfig` (below existing `field_validator`s):
```python
@model_validator(mode="after")
def _check_backend_exclusivity(self) -> "LoggingConfig":
    if self.pretty and self.loguru_sinks:
        raise ValueError(
            "pretty=True and loguru_sinks are mutually exclusive; "
            "use a loguru sink with colorize=True for colored output"
        )
    return self
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_logging_config.py::TestLoggingConfigLoguruSinks`
Expected: all 6 PASS.

- [ ] **Step 5: Run full logging_config test module**

Run: `rtk uv run pytest -q tests/unit/observability/test_logging_config.py`
Expected: **every** test in the file passes; no regressions in pre-existing cases.

- [ ] **Step 6: Commit**

```bash
rtk git add openagents/observability/config.py tests/unit/observability/test_logging_config.py
rtk git commit -m "feat(observability): add loguru_sinks field with mutual-exclusivity validator"
```

---

## Task 6: YAML/dict roundtrip for loguru_sinks in AppConfig

**Files:**
- Modify: `tests/unit/observability/test_app_config_logging.py`

**Rationale:** The spec §6.1 calls out that `AppConfig`-driven YAML/dict roundtrip must cover `loguru_sinks`. This locks the config path for downstream YAML loading; no source change needed beyond Task 5.

- [ ] **Step 1: Read the file to understand existing conventions**

Open `tests/unit/observability/test_app_config_logging.py`; see what class/fixture pattern it uses to build `AppConfig` / apply config.

- [ ] **Step 2: Add new test(s) following that convention**

Add a test roughly like (adapt import/call paths to match whatever the file already uses):

```python
def test_app_config_accepts_loguru_sinks_via_dict():
    from openagents.observability.config import LoggingConfig
    payload = {
        "level": "INFO",
        "pretty": False,
        "loguru_sinks": [
            {"target": "stderr", "colorize": True},
            {"target": ".logs/app.log", "rotation": "10 MB", "retention": "7 days"},
            {"target": ".logs/events.jsonl", "serialize": True, "enqueue": True},
        ],
    }
    cfg = LoggingConfig.model_validate(payload)
    assert len(cfg.loguru_sinks) == 3
    assert cfg.loguru_sinks[0].colorize is True
    assert cfg.loguru_sinks[1].rotation == "10 MB"
    assert cfg.loguru_sinks[2].serialize is True
```

If there's already a YAML-roundtrip helper in the file, reuse that idiom with the same payload.

- [ ] **Step 3: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_app_config_logging.py`
Expected: new test passes; no regressions.

- [ ] **Step 4: Commit**

```bash
rtk git add tests/unit/observability/test_app_config_logging.py
rtk git commit -m "test(observability): cover loguru_sinks in AppConfig roundtrip"
```

---

## Task 7: `_loguru.py` scaffold — `_require_loguru`, `_sink_filter`, module state

**Files:**
- Create: `openagents/observability/_loguru.py`
- Create: `tests/unit/observability/test_loguru_integration.py`

**Rationale:** Lay down the non-handler pieces first: the import guard, the filter factory, the sink-ID registry. These have no stdlib interop and are pure logic — easy to TDD in isolation.

- [ ] **Step 1: Write failing tests**

Create `D:\Project\openagent-python-sdk\tests\unit\observability\test_loguru_integration.py`:

```python
"""Integration tests for the loguru intercept handler.

Requires the [loguru] extra. Module skipped cleanly if loguru is absent.
"""

from __future__ import annotations

import pytest

pytest.importorskip("loguru")

import logging

from openagents.observability._loguru import (
    _INSTALLED_SINK_IDS,
    _sink_filter,
    _require_loguru,
    install_sinks,
    remove_installed_sinks,
)


@pytest.fixture(autouse=True)
def _reset_loguru_state():
    """Clean both openagents and global loguru state around every test.

    Test-only concession: ``loguru.logger.remove()`` (no args) is called here
    to wipe loguru's default stderr sink (ID 0, installed at import time).
    Production code MUST NOT do this — it would clear sinks the user's app
    installed. Tests own the process and are free to do it.
    """
    from loguru import logger as _lg
    from openagents.observability import reset_logging
    _lg.remove()  # drop loguru default sink so stderr-capture tests are clean
    reset_logging()
    yield
    _lg.remove()
    reset_logging()


class TestRequireLoguru:
    def test_returns_loguru_logger(self):
        lg = _require_loguru()
        # loguru.logger is a singleton exposing level/bind/add/remove
        assert hasattr(lg, "add")
        assert hasattr(lg, "remove")
        assert hasattr(lg, "bind")


class TestSinkFilter:
    def test_rejects_record_without_openagents_tag(self):
        f = _sink_filter(None)
        assert f({"extra": {}}) is False
        assert f({"extra": {"_openagents": False}}) is False

    def test_accepts_tagged_record_no_include(self):
        f = _sink_filter(None)
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm"}}) is True

    def test_filter_include_prefix_match(self):
        f = _sink_filter(["openagents.llm"])
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm"}}) is True
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm.anthropic"}}) is True
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.runtime"}}) is False

    def test_filter_include_empty_list_matches_nothing(self):
        f = _sink_filter([])
        assert f({"extra": {"_openagents": True, "_oa_name": "openagents.llm"}}) is False


class TestInstalledSinkIdsModuleState:
    def test_initially_empty(self):
        assert _INSTALLED_SINK_IDS == []

    def test_remove_on_empty_is_noop(self):
        remove_installed_sinks()  # must not raise
        assert _INSTALLED_SINK_IDS == []
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_loguru_integration.py`
Expected: all FAIL with `ModuleNotFoundError: No module named 'openagents.observability._loguru'`.

- [ ] **Step 3: Create `_loguru.py` scaffold**

Write `D:\Project\openagent-python-sdk\openagents\observability\_loguru.py`:

```python
"""Internal loguru helpers.

All ``loguru`` imports live here behind import-time guards. Public callers
(``observability.configure()``) use ``install_sinks``/``remove_installed_sinks``
and receive ``LoguruNotInstalledError`` if loguru is missing.

Library etiquette: we install N sinks on the global ``loguru.logger`` but
(a) every sink filter requires ``record['extra']['_openagents'] is True``
so user-installed sinks never see our records and vice versa, and
(b) ``remove_installed_sinks()`` only removes the IDs we recorded — we
never call the no-arg ``logger.remove()``, which would clear everything.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from openagents.observability.config import LoguruSinkConfig
from openagents.observability.errors import LoguruNotInstalledError
from openagents.observability.filters import _LOGRECORD_STD_ATTRS

_INSTALLED_SINK_IDS: list[int] = []


def _require_loguru() -> Any:
    """Return ``loguru.logger`` or raise ``LoguruNotInstalledError`` with pip hint."""
    try:
        from loguru import logger
    except ImportError as exc:
        raise LoguruNotInstalledError() from exc
    return logger


def _sink_filter(cfg_filter_include: list[str] | None) -> Callable[[dict], bool]:
    """Build a per-sink filter: accept only records this library tagged."""
    def _f(record: dict) -> bool:
        extra = record["extra"]
        if extra.get("_openagents") is not True:
            return False
        if cfg_filter_include is None:
            return True
        name = extra.get("_oa_name", "")
        return any(name == p or name.startswith(p + ".") for p in cfg_filter_include)
    return _f
```

(The `install_sinks`, `remove_installed_sinks`, and `_LoguruInterceptHandler` pieces land in tasks 8/9.)

Temporarily stub the not-yet-implemented functions so the test import succeeds:

```python
def install_sinks(sinks: list[LoguruSinkConfig]) -> None:  # pragma: no cover - filled in task 8
    raise NotImplementedError("install_sinks is implemented in task 8")


def remove_installed_sinks() -> None:
    try:
        from loguru import logger
    except ImportError:
        _INSTALLED_SINK_IDS.clear()
        return
    for sid in _INSTALLED_SINK_IDS:
        try:
            logger.remove(sid)
        except ValueError:
            pass
    _INSTALLED_SINK_IDS.clear()
```

(We implement `remove_installed_sinks` **in this task** because the `_reset_loguru_state` fixture relies on it; `install_sinks` is stubbed and tested in the next task.)

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_loguru_integration.py`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/observability/_loguru.py tests/unit/observability/test_loguru_integration.py
rtk git commit -m "feat(observability): scaffold _loguru.py with _require_loguru, _sink_filter, remove_installed_sinks"
```

---

## Task 8: `install_sinks()` — multi-sink lifecycle with batch rollback

**Files:**
- Modify: `openagents/observability/_loguru.py`
- Modify: `tests/unit/observability/test_loguru_integration.py`

**Rationale:** This is the business of translating `list[LoguruSinkConfig]` into `loguru.logger.add(...)` calls, tracking their IDs, and guaranteeing atomic-ish installation (batch rollback on partial failure).

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/observability/test_loguru_integration.py`:

```python
class TestInstallSinks:
    def test_installs_single_stderr_sink(self):
        from openagents.observability.config import LoguruSinkConfig
        install_sinks([LoguruSinkConfig(target="stderr")])
        assert len(_INSTALLED_SINK_IDS) == 1

    def test_installs_multiple_sinks(self, tmp_path):
        from openagents.observability.config import LoguruSinkConfig
        install_sinks([
            LoguruSinkConfig(target="stderr", colorize=False),
            LoguruSinkConfig(target=str(tmp_path / "app.log")),
            LoguruSinkConfig(target=str(tmp_path / "events.jsonl"), serialize=True),
        ])
        assert len(_INSTALLED_SINK_IDS) == 3

    def test_stderr_target_routes_to_sys_stderr(self, capsys):
        from loguru import logger
        from openagents.observability.config import LoguruSinkConfig
        install_sinks([LoguruSinkConfig(target="stderr", format="{message}")])
        logger.bind(_openagents=True, _oa_name="test").info("hello-to-stderr")
        captured = capsys.readouterr()
        assert "hello-to-stderr" in captured.err

    def test_stdout_target_routes_to_sys_stdout(self, capsys):
        from loguru import logger
        from openagents.observability.config import LoguruSinkConfig
        install_sinks([LoguruSinkConfig(target="stdout", format="{message}")])
        logger.bind(_openagents=True, _oa_name="test").info("hello-to-stdout")
        captured = capsys.readouterr()
        assert "hello-to-stdout" in captured.out

    def test_file_target_writes_to_path(self, tmp_path):
        from loguru import logger
        from openagents.observability.config import LoguruSinkConfig
        log_path = tmp_path / "out.log"
        install_sinks([LoguruSinkConfig(target=str(log_path), format="{message}")])
        logger.bind(_openagents=True, _oa_name="test").info("written")
        # enqueue defaults to False, so write is sync
        assert log_path.exists()
        assert "written" in log_path.read_text(encoding="utf-8")

    def test_filter_rejects_untagged_records(self, capsys):
        from loguru import logger
        from openagents.observability.config import LoguruSinkConfig
        install_sinks([LoguruSinkConfig(target="stderr", format="{message}")])
        # Untagged: no _openagents=True in extra
        logger.info("untagged-should-not-appear")
        captured = capsys.readouterr()
        assert "untagged-should-not-appear" not in captured.err

    def test_batch_rollback_on_partial_failure(self, tmp_path):
        """Spec test 11: if any sink's add() raises (e.g. invalid rotation),
        all sinks successfully added in this call must be removed."""
        import pytest
        from openagents.observability.config import LoguruSinkConfig
        good = LoguruSinkConfig(target="stderr")
        bad = LoguruSinkConfig(target=str(tmp_path / "x.log"), rotation="not-a-valid-size")
        with pytest.raises(Exception):
            install_sinks([good, bad])
        assert _INSTALLED_SINK_IDS == []

    def test_remove_does_not_touch_user_sinks(self, tmp_path, capsys):
        """Spec test 4: user-installed sinks (no _openagents tag) must
        survive our reset."""
        from loguru import logger
        from openagents.observability.config import LoguruSinkConfig
        user_sink_path = tmp_path / "user.log"
        user_sink_id = logger.add(str(user_sink_path), format="{message}")
        try:
            install_sinks([LoguruSinkConfig(target="stderr")])
            assert len(_INSTALLED_SINK_IDS) == 1
            remove_installed_sinks()
            # User's sink still alive
            logger.info("user-still-here")
            assert "user-still-here" in user_sink_path.read_text(encoding="utf-8")
        finally:
            logger.remove(user_sink_id)

    def test_user_sink_never_receives_openagents_records(self, tmp_path):
        """Spec test 3 (reverse direction): user's own sink — which filters
        OUT our _openagents tag — must not receive records forwarded by
        our intercept handler."""
        from loguru import logger
        from openagents.observability.config import LoguruSinkConfig
        user_sink_path = tmp_path / "user.log"
        user_sink_id = logger.add(
            str(user_sink_path),
            format="{message}",
            filter=lambda r: r["extra"].get("_openagents") is not True,
        )
        try:
            install_sinks([LoguruSinkConfig(target=str(tmp_path / "oa.log"), format="{message}")])
            # Record going through our tagged intercept path
            logger.bind(_openagents=True, _oa_name="test").info("openagents-only")
            # Record from user's own codepath
            logger.info("user-only")
            user_content = user_sink_path.read_text(encoding="utf-8")
            assert "user-only" in user_content
            assert "openagents-only" not in user_content
            oa_content = (tmp_path / "oa.log").read_text(encoding="utf-8")
            assert "openagents-only" in oa_content
            assert "user-only" not in oa_content
        finally:
            logger.remove(user_sink_id)

    def test_require_loguru_raises_when_loguru_missing(self, monkeypatch):
        """Spec test 7: simulate ImportError by shadowing the import site.
        This is tricky because loguru is already imported; we patch
        _require_loguru's internal import by monkey-patching sys.modules."""
        import sys
        import importlib
        # Save and remove loguru from sys.modules; also block re-import
        saved = sys.modules.pop("loguru", None)
        monkeypatch.setattr(
            "builtins.__import__",
            lambda name, *a, **k: (_ for _ in ()).throw(ImportError(f"mock-block-{name}"))
            if name == "loguru"
            else importlib.__import__(name, *a, **k),
        )
        try:
            from openagents.observability._loguru import _require_loguru
            from openagents.observability import LoguruNotInstalledError
            with pytest.raises(LoguruNotInstalledError) as excinfo:
                _require_loguru()
            assert "pip install io-openagent-sdk[loguru]" in str(excinfo.value)
        finally:
            if saved is not None:
                sys.modules["loguru"] = saved
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_loguru_integration.py::TestInstallSinks`
Expected: most FAIL — `install_sinks` raises `NotImplementedError`.

- [ ] **Step 3: Implement `install_sinks`**

In `openagents/observability/_loguru.py`, replace the `install_sinks` stub with:

```python
def install_sinks(sinks: list[LoguruSinkConfig]) -> None:
    """Install one loguru sink per config entry. Record their IDs.

    Atomicity: if any sink's ``logger.add()`` raises, all sinks
    successfully added within this call are removed before the exception
    propagates. Pre-existing sinks from prior ``install_sinks`` calls are
    untouched.
    """
    logger = _require_loguru()
    batch: list[int] = []
    try:
        for cfg in sinks:
            if cfg.target == "stderr":
                target: Any = sys.stderr
            elif cfg.target == "stdout":
                target = sys.stdout
            else:
                target = cfg.target
            kwargs: dict[str, Any] = {
                "level": cfg.level,
                "filter": _sink_filter(cfg.filter_include),
                "enqueue": cfg.enqueue,
                "serialize": cfg.serialize,
            }
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_loguru_integration.py::TestInstallSinks`
Expected: all 9 PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/observability/_loguru.py tests/unit/observability/test_loguru_integration.py
rtk git commit -m "feat(observability): implement install_sinks with batch rollback and user-sink isolation"
```

---

## Task 9: `_LoguruInterceptHandler.emit()` — InterceptHandler with dynamic depth, level fallback, extras propagation

**Files:**
- Modify: `openagents/observability/_loguru.py`
- Modify: `tests/unit/observability/test_loguru_integration.py`

**Rationale:** The core translation layer. Four distinct behaviors per spec §4.1:
1. Level-name → loguru level lookup with numeric fallback
2. Dynamic frame-walk depth (the official loguru InterceptHandler pattern)
3. Extras harvested from `record.__dict__` (skip underscore + `_LOGRECORD_STD_ATTRS`), forwarded via `bind(**extras)`
4. Exception info forwarded via `.opt(exception=record.exc_info)`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/observability/test_loguru_integration.py`:

```python
class TestLoguruInterceptHandler:
    def _build_handler_with_sink(self, tmp_path, **sink_overrides):
        """Helper: install a single file sink and return the log path."""
        from openagents.observability._loguru import (
            _LoguruInterceptHandler,
            install_sinks,
        )
        from openagents.observability.config import LoguruSinkConfig
        kwargs = {"target": str(tmp_path / "out.log"), "format": "{message}"}
        kwargs.update(sink_overrides)
        install_sinks([LoguruSinkConfig(**kwargs)])
        handler = _LoguruInterceptHandler()
        lg = logging.getLogger("openagents.test")
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
        return handler, lg, tmp_path / "out.log"

    def test_basic_forward_standard_level(self, tmp_path):
        handler, lg, log_path = self._build_handler_with_sink(tmp_path)
        lg.info("hello-forwarded")
        assert "hello-forwarded" in log_path.read_text(encoding="utf-8")

    def test_custom_numeric_level_falls_back_to_levelno(self, tmp_path):
        """Spec test 13."""
        logging.addLevelName(25, "CUSTOMV25")
        handler, lg, log_path = self._build_handler_with_sink(
            tmp_path,
            level="DEBUG",
            format="{level.no} {message}",
        )
        lg.log(25, "custom-level-msg")
        content = log_path.read_text(encoding="utf-8")
        assert "custom-level-msg" in content
        # level.no in loguru should reflect the numeric 25 we passed
        assert "25" in content

    def test_exception_forwarded(self, tmp_path):
        """Spec test 10: exception info reaches loguru sink."""
        handler, lg, log_path = self._build_handler_with_sink(
            tmp_path, format="{message}\n{exception}"
        )
        try:
            raise RuntimeError("boom-inner")
        except RuntimeError:
            lg.exception("boom-outer")
        content = log_path.read_text(encoding="utf-8")
        assert "boom-outer" in content
        assert "RuntimeError" in content
        assert "boom-inner" in content

    def test_extras_propagated_to_bind(self, tmp_path):
        """Spec test 15: non-standard LogRecord attrs flow as loguru extra."""
        handler, lg, log_path = self._build_handler_with_sink(
            tmp_path, serialize=True
        )
        lg.info("with-request-id", extra={"request_id": "r-42"})
        import json
        # serialize=True emits one JSON line per record
        line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        data = json.loads(line)
        assert data["record"]["extra"]["request_id"] == "r-42"
        assert data["record"]["extra"]["_openagents"] is True

    def test_extras_redacted_before_reaching_sink(self, tmp_path):
        """Spec test 9: RedactFilter runs before forward, so sensitive
        fields on the record are already masked when they reach loguru."""
        import json
        from openagents.observability.filters import RedactFilter
        handler, lg, log_path = self._build_handler_with_sink(
            tmp_path, serialize=True
        )
        handler.addFilter(RedactFilter(keys=["api_key"], max_value_length=500))
        lg.info("sensitive", extra={"api_key": "sk-abc", "request_id": "r-1"})
        line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        data = json.loads(line)
        # request_id passes through untouched
        assert data["record"]["extra"]["request_id"] == "r-1"
        # api_key is redacted (the RedactFilter replaces it; exact token is
        # implementation-defined — we assert it no longer equals "sk-abc")
        assert data["record"]["extra"]["api_key"] != "sk-abc"

    def test_depth_points_to_caller_not_handler(self, tmp_path):
        """Spec test 0: the canonical InterceptHandler pattern walks frames
        so {function}/{line} point at the caller."""
        handler, lg, log_path = self._build_handler_with_sink(
            tmp_path, format="{function}:{line} {message}"
        )
        def _my_caller_func():
            lg.info("from-caller")
        _my_caller_func()
        content = log_path.read_text(encoding="utf-8")
        assert "_my_caller_func" in content
        # The handler function name "emit" should NOT appear as the caller.
        # (It may appear in other contexts, but not as the {function} field.)
        # Strong assertion: the line that contains our message starts with
        # the caller function name.
        last = [ln for ln in content.splitlines() if "from-caller" in ln][-1]
        assert last.startswith("_my_caller_func:")
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_loguru_integration.py::TestLoguruInterceptHandler`
Expected: FAIL with `ImportError: cannot import name '_LoguruInterceptHandler'`.

- [ ] **Step 3: Implement `_LoguruInterceptHandler`**

Append to `openagents/observability/_loguru.py`:

```python
class _LoguruInterceptHandler(logging.Handler):
    """Forwards stdlib ``LogRecord`` to ``loguru.logger``.

    Follows loguru's official InterceptHandler pattern:
    - level name → loguru level, with fallback to the numeric level for
      user-defined levels that loguru doesn't know by name
    - dynamic frame-walk depth so ``{name}:{function}:{line}`` points at
      the business caller, not this handler
    - non-standard LogRecord attributes are harvested as extras and
      forwarded via ``bind(**extras)``, so data shaped by ``RedactFilter``
      (and similar filters) reaches ``serialize=True`` JSON sinks
    """

    def __init__(self) -> None:
        super().__init__()
        self._openagents_installed = True  # align with reset_logging cleanup tag
        self._logger = _require_loguru()  # fail loud at handler construction

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 1. Level name → loguru level, with numeric fallback
            try:
                level: Any = self._logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            # 2. Dynamic depth: step out of all frames inside stdlib logging
            frame = logging.currentframe()
            depth = 2
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            # 3. Harvest non-standard extras
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

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_loguru_integration.py::TestLoguruInterceptHandler`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/observability/_loguru.py tests/unit/observability/test_loguru_integration.py
rtk git commit -m "feat(observability): implement _LoguruInterceptHandler with depth/level/extras forwarding"
```

---

## Task 10: Identity-equality test for `_LOGRECORD_STD_ATTRS` across modules

**Files:**
- Modify: `tests/unit/observability/test_loguru_integration.py`

**Rationale:** Spec test case 16 — prevent future drift. The constant must be imported-by-reference, not copy-pasted. One short test to lock it in.

- [ ] **Step 1: Write test**

Append to `tests/unit/observability/test_loguru_integration.py`:

```python
def test_logrecord_std_attrs_is_shared_singleton():
    """Spec test 16: prevent silent drift between filters.py and _loguru.py."""
    from openagents.observability import _loguru
    from openagents.observability import filters
    assert _loguru._LOGRECORD_STD_ATTRS is filters._LOGRECORD_STD_ATTRS
```

- [ ] **Step 2: Run — expect PASS already**

Run: `rtk uv run pytest -q tests/unit/observability/test_loguru_integration.py::test_logrecord_std_attrs_is_shared_singleton`
Expected: PASS (since `_loguru.py` imports it from `filters`, identity holds).

- [ ] **Step 3: Commit**

```bash
rtk git add tests/unit/observability/test_loguru_integration.py
rtk git commit -m "test(observability): assert _LOGRECORD_STD_ATTRS singleton identity across modules"
```

---

## Task 11: `logging.py::_build_handler` — loguru branch + downgrade warning

**Files:**
- Modify: `openagents/observability/logging.py`
- Modify: `tests/unit/observability/test_configure.py`

**Rationale:** Wire selection of the loguru handler into `configure()`. Covers three observable behaviors: (a) `loguru_sinks=[…]` → `_LoguruInterceptHandler`, (b) `OPENAGENTS_LOG_LOGURU_DISABLE=1` → plain `StreamHandler` + WARNING (spec test 8), (c) pre-existing plain/rich branches unchanged.

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/observability/test_configure.py`:

```python
class TestConfigureLoguruBranch:
    def test_loguru_sinks_installs_intercept_handler(self):
        pytest.importorskip("loguru")
        from openagents.observability import LoggingConfig, configure
        from openagents.observability._loguru import (
            _INSTALLED_SINK_IDS,
            _LoguruInterceptHandler,
        )
        configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        handlers = _installed_handlers()
        assert len(handlers) == 1
        assert isinstance(handlers[0], _LoguruInterceptHandler)
        assert len(_INSTALLED_SINK_IDS) == 1

    def test_disable_env_downgrades_to_stream_handler_with_warning(
        self, monkeypatch, caplog
    ):
        """Spec test 8."""
        pytest.importorskip("loguru")
        from openagents.observability import LoggingConfig, configure
        monkeypatch.setenv("OPENAGENTS_LOG_LOGURU_DISABLE", "1")
        with caplog.at_level(
            logging.WARNING, logger="openagents.observability.logging"
        ):
            configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        handlers = _installed_handlers()
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.StreamHandler)
        assert not isinstance(
            handlers[0],
            __import__(
                "openagents.observability._loguru", fromlist=["_LoguruInterceptHandler"]
            )._LoguruInterceptHandler,
        )
        # WARNING emitted
        assert any(
            "OPENAGENTS_LOG_LOGURU_DISABLE" in r.message for r in caplog.records
        )

    def test_configure_plain_branch_still_works(self):
        from openagents.observability import LoggingConfig, configure
        configure(LoggingConfig(pretty=False, level="INFO"))
        handlers = _installed_handlers()
        assert len(handlers) == 1
        assert handlers[0].__class__.__name__ == "StreamHandler"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_configure.py::TestConfigureLoguruBranch`
Expected: loguru tests FAIL (no branch); plain branch test passes.

- [ ] **Step 3: Add loguru branch to `_build_handler`**

In `openagents/observability/logging.py`, modify `_build_handler` to exactly match spec §4.2:

```python
def _build_handler(config: LoggingConfig) -> logging.Handler:
    loguru_disabled_raw = _env_value("OPENAGENTS_LOG_LOGURU_DISABLE")
    loguru_disabled_flag = (
        loguru_disabled_raw is not None
        and loguru_disabled_raw.lower() in {"1", "true", "yes", "on"}
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
    import sys

    stream = sys.stderr if config.stream == "stderr" else sys.stdout
    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s"))
    handler._openagents_installed = True  # type: ignore[attr-defined]
    return handler
```

Also add `_env_value` to the imports at the top of the module (it's in `config.py`):

```python
from openagents.observability.config import (
    LoggingConfig,
    _env_value,
    load_from_env,
    merge_env_overrides,
)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_configure.py::TestConfigureLoguruBranch`
Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/observability/logging.py tests/unit/observability/test_configure.py
rtk git commit -m "feat(observability): route loguru_sinks through _LoguruInterceptHandler with disable-env escape hatch"
```

---

## Task 12: `reset_logging()` cleans loguru sinks + `configure()` rollback on filter-wiring failure

**Files:**
- Modify: `openagents/observability/logging.py`
- Modify: `tests/unit/observability/test_configure.py`

**Rationale:** Two cleanup invariants:
(a) `reset_logging()` must call `remove_installed_sinks()` (guarded by ImportError so builds without loguru still work).
(b) `configure()` must treat handler-install + filter-wiring as one atomic block — if any `addFilter` fails after the loguru sinks have been installed, we need to remove them before propagating the exception. Without this, partial state leaks.

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/observability/test_configure.py`:

```python
class TestResetLoggingLoguruCleanup:
    def test_reset_clears_installed_sink_ids(self):
        pytest.importorskip("loguru")
        from openagents.observability import LoggingConfig, configure, reset_logging
        from openagents.observability._loguru import _INSTALLED_SINK_IDS
        configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        assert len(_INSTALLED_SINK_IDS) == 1
        reset_logging()
        assert _INSTALLED_SINK_IDS == []

    def test_repeated_configure_replaces_sinks_not_stacks(self):
        """Spec test 5."""
        pytest.importorskip("loguru")
        from openagents.observability import LoggingConfig, configure
        from openagents.observability._loguru import _INSTALLED_SINK_IDS
        configure(LoggingConfig(loguru_sinks=[
            {"target": "stderr"},
            {"target": "stdout"},
        ]))
        assert len(_INSTALLED_SINK_IDS) == 2
        configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        assert len(_INSTALLED_SINK_IDS) == 1

    def test_reset_idempotent(self):
        from openagents.observability import reset_logging
        reset_logging()
        reset_logging()  # must not raise
```

Also add a test for the rollback invariant (spec §4.2 load-bearing invariant). This one needs to force an exception after sink install but during filter wiring — we simulate it by subclassing `PrefixFilter` to raise on `__init__`. But that's invasive; a simpler form is to directly patch `PrefixFilter` via monkeypatch so constructing it raises:

```python
class TestConfigureRollback:
    def test_filter_construction_failure_rolls_back_loguru_sinks(
        self, monkeypatch
    ):
        """Load-bearing invariant from spec §4.2: reset_logging() at top of
        configure() clears prior state before install_sinks() writes, so
        a subsequent rollback during filter wiring only removes this call's
        batch. We simulate a filter-wiring failure to verify the batch is
        cleaned up."""
        pytest.importorskip("loguru")
        from openagents.observability import LoggingConfig, configure
        from openagents.observability._loguru import _INSTALLED_SINK_IDS

        # Force PrefixFilter.__init__ to raise
        import openagents.observability.logging as log_mod
        orig_prefix = log_mod.PrefixFilter

        class BoomFilter(orig_prefix):
            def __init__(self, *args, **kwargs):
                raise RuntimeError("simulated filter failure")

        monkeypatch.setattr(log_mod, "PrefixFilter", BoomFilter)

        with pytest.raises(RuntimeError, match="simulated filter failure"):
            configure(LoggingConfig(loguru_sinks=[{"target": "stderr"}]))
        # After the failure, no loguru sinks should remain
        assert _INSTALLED_SINK_IDS == []
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/observability/test_configure.py -k "reset_logging_loguru or configure_rollback"`
Expected: at least some FAIL — `reset_logging` doesn't touch loguru; `configure` doesn't rollback on filter failure.

- [ ] **Step 3: Update `reset_logging`**

In `openagents/observability/logging.py::reset_logging`, at the end of the function (after the existing level-restore block), append:

```python
    try:
        from openagents.observability._loguru import remove_installed_sinks
    except ImportError:
        # loguru not installed → nothing to remove; don't raise
        return
    remove_installed_sinks()
```

- [ ] **Step 4: Update `configure` to roll back on filter-wiring failure**

Wrap the handler-build and filter-attach section in `configure` with try/except:

```python
def configure(config: LoggingConfig | None = None) -> None:
    if config is None:
        config = load_from_env() or LoggingConfig()
    config = merge_env_overrides(config)

    _warn_on_foreign_loggers(config)
    reset_logging()  # step 1: pristine baseline

    root = logging.getLogger(_LOGGER_ROOT)
    root.setLevel(_name_to_level(config.level))
    root.propagate = False

    for name, level_name in config.per_logger_levels.items():
        if name != _LOGGER_ROOT and not name.startswith(_LOGGER_ROOT + "."):
            continue
        logging.getLogger(name).setLevel(_name_to_level(level_name))
        _OVERRIDDEN_LOGGERS.add(name)

    try:
        handler = _build_handler(config)
        handler.addFilter(PrefixFilter(include=config.include_prefixes, exclude=config.exclude_prefixes))
        if config.per_logger_levels:
            handler.addFilter(LevelOverrideFilter(config.per_logger_levels))
        if config.redact_keys:
            handler.addFilter(RedactFilter(keys=config.redact_keys, max_value_length=config.max_value_length))
        root.addHandler(handler)
    except Exception:
        # Roll back any loguru sinks this call installed before the error
        # reached filter-wiring. The load-bearing invariant is that
        # reset_logging() above already cleared prior state, so
        # _INSTALLED_SINK_IDS (if non-empty) contains only this call's batch.
        try:
            from openagents.observability._loguru import remove_installed_sinks
            remove_installed_sinks()
        except ImportError:
            pass
        # Also reset root logger state so we don't leak half-config
        reset_logging()
        raise
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/observability/test_configure.py`
Expected: all tests pass (new + old).

- [ ] **Step 6: Commit**

```bash
rtk git add openagents/observability/logging.py tests/unit/observability/test_configure.py
rtk git commit -m "feat(observability): clean loguru sinks on reset and rollback on configure filter failure"
```

---

## Task 13: Documentation — bilingual

**Files:**
- Modify: `docs/observability.md`, `docs/observability.en.md`
- Modify: `docs/configuration.md`, `docs/configuration.en.md`
- Modify: `docs/repository-layout.md`, `docs/repository-layout.en.md`

**Rationale:** Per spec §8, three doc pairs need updates. Co-evolve the EN and ZH versions in the same commit.

- [ ] **Step 1: Update `docs/observability.md` (Chinese)**

Open and locate the section about `pretty`/`rich`. Add a new section "多 sink 日志（loguru）" with:

1. One-paragraph intro: why loguru (multi-sink, rotation, JSON), that it's a third option alongside plain/rich, requires `[loguru]` extra.
2. Full YAML example from spec §3.3 (stderr colorize + file rotation + JSON sink).
3. Bullet list of constraints:
   - Mutually exclusive with `pretty: true`
   - Never touches sinks the user's app installs on `loguru.logger` directly
   - `OPENAGENTS_LOG_LOGURU_DISABLE=1` env var escape hatch
   - Does **not** cover the RuntimeEvent channel (`FileLoggingEventBus` stays for event archiving)
4. `LoguruNotInstalledError` + pip command shown.

- [ ] **Step 2: Update `docs/observability.en.md` (English)**

Translate the same new section into English, matching the tone of the existing `.en.md` file.

- [ ] **Step 3: Update `docs/configuration.md` + `.en.md`**

(a) In `LoggingConfig` field table, add a row for `loguru_sinks` (type: `list[LoguruSinkConfig]`, default `[]`, description: multi-sink loguru configuration; mutually exclusive with `pretty`).

(b) Add a new `LoguruSinkConfig` field table listing all 10 fields (`target`, `level`, `format`, `serialize`, `colorize`, `rotation`, `retention`, `compression`, `enqueue`, `filter_include`) with type, default, and one-line description each.

(c) In the env var table, add `OPENAGENTS_LOG_LOGURU_DISABLE` with behavior: "Set to `1`/`true`/`yes`/`on` to force-downgrade `loguru_sinks` configurations to the plain `StreamHandler`. Intended as a CI / debug escape hatch."

Do both language variants.

- [ ] **Step 4: Update `docs/repository-layout.md` + `.en.md`**

In the `openagents/observability/` directory listing, add a row for `_loguru.py`: "internal loguru helpers (install_sinks, remove_installed_sinks, _LoguruInterceptHandler); all loguru imports gated behind `_require_loguru()`".

- [ ] **Step 5: Spot-check docs render**

Skim each file in a Markdown previewer or just visually for broken lists/tables.

- [ ] **Step 6: Commit**

```bash
rtk git add docs/observability.md docs/observability.en.md docs/configuration.md docs/configuration.en.md docs/repository-layout.md docs/repository-layout.en.md
rtk git commit -m "docs(observability): document optional loguru multi-sink backend (bilingual)"
```

---

## Task 14: Full verification — tests, coverage floor, and cross-cutting smoke

**Files:**
- No changes; verification only.

**Rationale:** Make sure everything composes. Spec coverage floor is 92% and `_loguru.py` is in `omit` so the floor shouldn't move. Run the example demos as smoke checks where they touch logging (they shouldn't break since `loguru_sinks` defaults to `[]`).

- [ ] **Step 1: Run full test suite**

Run: `rtk uv run pytest -q`
Expected: all pass; no skips except those that legitimately depend on extras the env doesn't have.

- [ ] **Step 2: Coverage check**

Run: `rtk uv run coverage run -m pytest && rtk uv run coverage report`
Expected: total coverage ≥ 92%. Verify `openagents/observability/_loguru.py` appears in `omit`d files. Verify `errors.py`, `config.py`, `filters.py`, `logging.py` contributions are well above 92%.

- [ ] **Step 3: Quickstart example smoke**

Run: `rtk uv run python examples/quickstart/run_demo.py` (requires `MINIMAX_API_KEY`; skip this substep if unavailable).
Expected: no log-related regressions.

- [ ] **Step 4: lint**

Run: `rtk uv run ruff check openagents/observability tests/unit/observability`
Expected: clean.

- [ ] **Step 5: Final commit if anything drifted**

If previous steps surfaced a trivial fix (import ordering, blank-line), commit it:

```bash
rtk git add <paths>
rtk git commit -m "chore: cleanup after full verification"
```

Otherwise skip — no empty commits.

---

## Post-Implementation Checklist

Before declaring done, verify:

- [ ] All 17 spec test cases (0-16) have corresponding pytest test(s) passing
- [ ] `_loguru.py` listed in `[tool.coverage.report].omit`
- [ ] `LoguruNotInstalledError`, `LoguruSinkConfig`, `_check_backend_exclusivity`, `_LOGRECORD_STD_ATTRS` all have tests that hit their branches (these are NOT omitted)
- [ ] `test_loguru_integration.py` top uses `pytest.importorskip("loguru")` so CI without the extra skips cleanly
- [ ] `_LOGRECORD_STD_ATTRS` lives in `filters.py` only; `_loguru.py` imports it by name (identity test case 16 passes)
- [ ] `reset_logging()` calls `remove_installed_sinks()` under ImportError guard
- [ ] `configure()` rollback restores `_INSTALLED_SINK_IDS == []` on filter-wiring failure
- [ ] `OPENAGENTS_LOG_LOGURU_DISABLE=1` downgrade emits a WARNING on `_OBS_LOGGER`
- [ ] Three doc pairs (observability, configuration, repository-layout) updated in both EN and ZH
- [ ] `pyproject.toml` `all` extra includes `loguru`; `dev` extra includes `loguru>=0.7.0`
- [ ] No `logging.getLogger("openagents.*")` call site in the library was modified (sanity: `rtk git log --stat origin/main..HEAD -- openagents/` should only touch `observability/`)

## Out-of-Scope Reminders (YAGNI)

Per spec §10, do **not** add any of the following while implementing:

- Env var representation of `loguru_sinks` (multi-sink in env = reject)
- Library-side migration to `from loguru import logger` for business code
- `FileLoggingEventBus` / `OtelBridge` / `AsyncEventBus` changes
- `openagents.observability.get_logger()` helper
- `@logger.catch` decorator adoption inside the library
- A separate "hot-swap sinks" API beyond what `Runtime.reload()` → `configure()` already provides
