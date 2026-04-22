# LiteLLM Optional Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `litellm` as an optional LLM provider to cover non-OpenAI protocol backends (AWS Bedrock, Google Vertex AI, Gemini native, Cohere, Azure deployment) without absorbing LiteLLM's product-layer features (router, fallback, budget manager, proxy).

**Architecture:** New class `LiteLLMClient(LLMClient)` in `openagents/llm/providers/litellm_client.py` wrapping `litellm.acompletion` with a narrow, single-call surface. Installed only via `[litellm]` extra. `openagents/llm/registry.py::create_llm_client` gains one branch; `LLMOptions._validate_llm_rules` whitelist adds `"litellm"`. LiteLLM's retry is used single-direction (we don't double-retry). Cost stays on the SDK's own `_compute_cost_for` path for provider symmetry.

**Tech Stack:** Python 3.10+ · `litellm>=1.50.0` (optional extra) · pytest + monkeypatch for stubbing · `uv` package manager · `rtk` CLI wrapper for commands.

**Spec:** `docs/superpowers/specs/2026-04-22-litellm-provider-integration-design.md`

---

## File Structure

### Created
- `openagents/llm/providers/litellm_client.py` — the new provider (~350 lines, patterned after `anthropic.py` rather than `openai_compatible.py`)
- `tests/unit/llm/providers/test_litellm_client.py` — all translation-layer tests (~550-700 lines, 16 methods)

### Modified
- `openagents/config/schema.py` — add `"litellm"` to the provider whitelist in `_validate_llm_rules`
- `openagents/llm/registry.py` — new `provider == "litellm"` branch + `_extract_litellm_kwargs(llm)` helper
- `pyproject.toml` — add `[project.optional-dependencies].litellm`, append `litellm` to `dev` and `all`, add to `[tool.coverage.report].omit`
- `tests/unit/llm/test_registry.py` — add `create_llm_client` routing test + `ImportError` path test + `_extract_litellm_kwargs` filtering test
- `tests/unit/config/test_models.py` — add "litellm" accepted, "litellmm" rejected
- `docs/configuration.md` / `docs/configuration.en.md` — new "LiteLLM Provider (Optional)" section
- `docs/developer-guide.md` / `docs/developer-guide.en.md` — provider list row
- `README.md` / `README.zh-CN.md` — provider row + one-line release note

### Not modified (explicit non-scope)
- `openagents/llm/base.py` — `LLMClient` contract is reused verbatim
- `openagents/llm/providers/_http_base.py` — LiteLLM has its own transport
- `examples/*` — per `CLAUDE.md`, only `quickstart` and `production_coding_agent` examples are maintained

---

## Reference Patterns

Before starting, the implementer should skim:
- `openagents/llm/providers/anthropic.py` — closest scale analogue (~1000 lines, same `LLMResponse`/`LLMChunk` translation shape)
- `openagents/llm/base.py` (lines 116-245) — `_compute_cost_for`, `_parse_structured_output`, `_effective_pricing`
- `openagents/llm/registry.py` — the if/elif extension point
- `tests/unit/llm/providers/test_openai_compatible.py` — monkey-patch style to emulate (note: we patch `litellm.acompletion` directly, not httpx)
- `tests/conftest.py` — already has a logging reset fixture, no new conftest needed

---

## Convention Notes

- **Commands use `rtk` wrapper** per `C:\Users\qwdma\.claude\CLAUDE.md`. Examples: `rtk uv run pytest ...`.
- **Tests use `uv run pytest`**, not bare `pytest`, per repo `CLAUDE.md`.
- **Coverage floor: 92%** (`pyproject.toml` `fail_under = 92`). The new provider file is omitted from coverage counting (same treatment as `mem0_memory.py`), so the floor is unaffected even without the `litellm` extra installed.
- **Commits:** one per task, conventional-commits style (`feat:`, `test:`, `docs:`, `chore:`).
- **TDD:** every task writes failing tests first, then minimal implementation, then verifies green.

---

## Task 1: Add `litellm` to pyproject.toml Extras and Coverage Omit

**Files:**
- Modify: `pyproject.toml`

**Rationale:** Make the extra installable first so the rest of the plan can `uv sync --extra litellm` and run new tests against real `litellm`. No code tests here — this is pure config, verified by import smoke test.

- [ ] **Step 1: Edit pyproject.toml `[project.optional-dependencies]`**

In `D:\Project\openagent-python-sdk\pyproject.toml`, inside the `[project.optional-dependencies]` table (currently ending around line 67), **insert a new `litellm` block** and **append `litellm` to the `dev` list** and **append `litellm` to the `all` list**:

```toml
litellm = [
    "litellm>=1.50.0",
]
```

Modify `dev` (currently lines 14-21) to include `litellm`:
```toml
dev = [
    "coverage[toml]>=7.6.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "ruff>=0.4.0",
    "respx>=0.21.1",
    "io-openagent-sdk[rich]",
    "litellm>=1.50.0",
]
```

Modify `all` (currently lines 65-67) to include `litellm`:
```toml
all = [
    "io-openagent-sdk[cli,mcp,mem0,openai,otel,rich,sqlite,dev,tokenizers,yaml,pptx,langfuse,phoenix,litellm]",
]
```

- [ ] **Step 2: Edit `[tool.coverage.report].omit`**

In `pyproject.toml`, extend the `omit` list (currently lines 92-99) with one line:

```toml
omit = [
    "openagents/plugins/builtin/memory/mem0_memory.py",
    "openagents/plugins/builtin/tool/mcp_tool.py",
    "openagents/plugins/builtin/session/sqlite_backed.py",
    "openagents/plugins/builtin/events/otel_bridge.py",
    "openagents/plugins/builtin/diagnostics/langfuse_plugin.py",
    "openagents/plugins/builtin/diagnostics/phoenix_plugin.py",
    "openagents/llm/providers/litellm_client.py",
]
```

- [ ] **Step 3: Sync deps**

Run: `rtk uv sync --extra dev --extra litellm`
Expected: exits 0, `litellm` appears in `uv.lock`.

- [ ] **Step 4: Smoke-verify litellm is importable**

Run: `rtk uv run python -c "import litellm; print(litellm.__version__)"`
Expected: a version string >= `1.50.0`, exit 0.

- [ ] **Step 5: Ensure existing suite still passes**

Run: `rtk uv run pytest -q`
Expected: all existing tests pass, coverage `>=92%`.

- [ ] **Step 6: Commit**

```bash
rtk git add pyproject.toml uv.lock
rtk git commit -m "chore(deps): add litellm as optional extra and coverage omit"
```

---

## Task 2: Schema Whitelist Accepts "litellm"

**Files:**
- Modify: `openagents/config/schema.py:214` — single-line change to the `allowed` set
- Modify/Test: `tests/unit/config/test_models.py` — add two assertions

**Rationale:** Smallest user-facing gate. Make sure the config validator accepts `provider="litellm"` before any downstream code can use it. Keep the test change co-located with source per `CLAUDE.md` "co-evolve tests with code".

- [ ] **Step 1: Write failing tests in `tests/unit/config/test_models.py`**

Open `D:\Project\openagent-python-sdk\tests\unit\config\test_models.py` and add at the end:

```python
def test_llm_options_accepts_litellm_provider():
    from openagents.config.schema import LLMOptions
    opts = LLMOptions(provider="litellm", model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0")
    assert opts.provider == "litellm"


def test_llm_options_rejects_litellm_typo():
    import pytest
    from openagents.config.schema import LLMOptions
    from openagents.errors.exceptions import ConfigValidationError
    with pytest.raises(ConfigValidationError):
        LLMOptions(provider="litellmm")
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/config/test_models.py::test_llm_options_accepts_litellm_provider tests/unit/config/test_models.py::test_llm_options_rejects_litellm_typo`
Expected: first test FAILs with `ConfigValidationError: 'llm.provider' must be one of ['anthropic', 'mock', 'openai_compatible']`.

- [ ] **Step 3: Edit `openagents/config/schema.py`**

In `_validate_llm_rules` (around line 214):

```python
# before
allowed = {"anthropic", "mock", "openai_compatible"}
# after
allowed = {"anthropic", "litellm", "mock", "openai_compatible"}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/config/test_models.py::test_llm_options_accepts_litellm_provider tests/unit/config/test_models.py::test_llm_options_rejects_litellm_typo`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/config/schema.py tests/unit/config/test_models.py
rtk git commit -m "feat(schema): whitelist litellm as provider"
```

---

## Task 3: LiteLLMClient Skeleton (Lazy Import, Telemetry, provider_name, aclose)

**Files:**
- Create: `openagents/llm/providers/litellm_client.py`
- Create: `tests/unit/llm/providers/test_litellm_client.py`

**Rationale:** Stand up the class shell with its invariants (lazy import guard, telemetry side-effects, dynamic `provider_name`, idempotent `aclose`) before adding any protocol logic. These four behaviors are independent and easy to TDD.

- [ ] **Step 1: Create test file with 4 failing tests**

Create `D:\Project\openagent-python-sdk\tests\unit\llm\providers\test_litellm_client.py`:

```python
"""Tests for LiteLLMClient translation layer."""

from __future__ import annotations

import pytest

# Module-level import; litellm is in dev extras. If missing, skip this file.
litellm = pytest.importorskip("litellm")

from openagents.errors.exceptions import ConfigError  # noqa: E402
from openagents.llm.providers import litellm_client as lc_module  # noqa: E402
from openagents.llm.providers.litellm_client import LiteLLMClient  # noqa: E402


def test_init_without_litellm_raises_config_error(monkeypatch):
    monkeypatch.setattr(lc_module, "litellm", None)
    with pytest.raises(ConfigError) as excinfo:
        LiteLLMClient(model="bedrock/foo")
    assert "pip install" in str(excinfo.value)
    assert "litellm" in str(excinfo.value)


def test_init_disables_telemetry_and_callbacks():
    # Dirty the module state first, then verify __init__ cleans it.
    litellm.telemetry = True
    litellm.success_callback = ["sentinel"]
    litellm.failure_callback = ["sentinel"]
    litellm.drop_params = False

    LiteLLMClient(model="gemini/gemini-1.5-pro")

    assert litellm.telemetry is False
    assert litellm.success_callback == []
    assert litellm.failure_callback == []
    assert litellm.drop_params is True


@pytest.mark.parametrize(
    "model,expected",
    [
        ("bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0", "litellm:bedrock"),
        ("vertex_ai/gemini-1.5-pro", "litellm:vertex_ai"),
        ("gemini/gemini-1.5-pro", "litellm:gemini"),
        ("azure/my-deployment", "litellm:azure"),
        ("just-a-model-name", "litellm"),
    ],
)
def test_provider_name_derives_from_model_prefix(model, expected):
    client = LiteLLMClient(model=model)
    assert client.provider_name == expected


@pytest.mark.asyncio
async def test_aclose_is_idempotent():
    client = LiteLLMClient(model="bedrock/foo")
    await client.aclose()
    await client.aclose()  # must not raise
```

- [ ] **Step 2: Run tests — expect FAIL (module does not exist)**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py`
Expected: collection error or import error — `No module named 'openagents.llm.providers.litellm_client'`.

- [ ] **Step 3: Create `openagents/llm/providers/litellm_client.py`**

```python
"""LiteLLM-backed LLM provider for non-OpenAI protocol backends.

Wraps ``litellm.acompletion`` with the SDK's ``LLMClient`` contract. Covers
AWS Bedrock, Google Vertex AI, Gemini native, Cohere, Azure deployment, and
any other backend LiteLLM supports through ``<prefix>/<model>`` identifiers.

Instantiating this client has process-global side effects: it sets
``litellm.telemetry = False``, clears ``litellm.success_callback`` and
``litellm.failure_callback``, and sets ``litellm.drop_params = True``.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from openagents.errors.exceptions import ConfigError
from openagents.llm.base import LLMClient

if TYPE_CHECKING:
    from openagents.config.schema import LLMPricing, LLMRetryOptions

try:
    import litellm  # type: ignore
except ImportError:  # pragma: no cover
    litellm = None

logger = logging.getLogger("openagents.llm.providers.litellm")


_FORWARDABLE_KWARGS: frozenset[str] = frozenset({
    "aws_region_name",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_profile_name",
    "vertex_project",
    "vertex_location",
    "vertex_credentials",
    "azure_deployment",
    "api_version",
    "seed",
    "top_p",
    "parallel_tool_calls",
    "response_format",
})


def _derive_provider_name(model: str) -> str:
    if not model or "/" not in model:
        return "litellm"
    prefix = model.split("/", 1)[0].strip()
    return f"litellm:{prefix}" if prefix else "litellm"


class LiteLLMClient(LLMClient):
    """LiteLLM-backed ``LLMClient``. See module docstring."""

    def __init__(
        self,
        *,
        model: str,
        api_base: str | None = None,
        api_key_env: str | None = None,
        timeout_ms: int = 30000,
        default_temperature: float | None = None,
        max_tokens: int = 1024,
        pricing: "LLMPricing | None" = None,
        retry_options: "LLMRetryOptions | None" = None,
        extra_headers: dict[str, str] | None = None,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if litellm is None:
            raise ConfigError(
                "provider 'litellm' requires: pip install 'io-openagent-sdk[litellm]'"
            )

        # Process-level telemetry/callbacks lockdown. Idempotent.
        litellm.telemetry = False
        litellm.success_callback = []
        litellm.failure_callback = []
        litellm.drop_params = True

        self.model_id = model or ""
        self.provider_name = _derive_provider_name(self.model_id)

        self._api_base = api_base
        self._api_key_env = api_key_env
        self._timeout_s = max(timeout_ms / 1000.0, 0.1)
        self._default_temperature = default_temperature
        self._max_tokens = max_tokens
        self._pricing = pricing
        self._retry_options = retry_options
        self._extra_headers = dict(extra_headers) if extra_headers else None
        self._extra_kwargs = dict(extra_kwargs) if extra_kwargs else {}

        # Pricing overrides on base class so _compute_cost_for picks them up.
        if pricing is not None:
            self.price_per_mtok_input = pricing.input
            self.price_per_mtok_output = pricing.output
            self.price_per_mtok_cached_read = pricing.cached_read
            self.price_per_mtok_cached_write = pricing.cached_write

    async def aclose(self) -> None:
        session = getattr(litellm, "aclient_session", None) if litellm else None
        if session is None:
            return
        try:
            await session.aclose()
        except Exception:  # pragma: no cover - defensive
            pass
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py`
Expected: 4 tests PASS (parametrized test #3 counts as 5 cases → 7 tests total actually).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/llm/providers/litellm_client.py tests/unit/llm/providers/test_litellm_client.py
rtk git commit -m "feat(llm): skeleton LiteLLMClient with lazy import, telemetry lockdown, dynamic provider_name"
```

---

## Task 4: `generate()` — Text, Usage, Cache Tokens, Structured Output, Cost

**Files:**
- Modify: `openagents/llm/providers/litellm_client.py` — add `generate()`
- Modify: `tests/unit/llm/providers/test_litellm_client.py` — add 5 tests

**Rationale:** The main non-streaming happy path. Bundle the five closely-coupled behaviors (text body, usage extraction, prompt-cache dual-style normalization, structured output parsing, cost path) because they all flow through the same `_to_llm_response` helper.

- [ ] **Step 1: Add 5 failing tests**

Append to `tests/unit/llm/providers/test_litellm_client.py`:

```python
import types
from openagents.config.schema import LLMPricing  # noqa: E402
from openagents.llm.base import LLMToolCall  # noqa: E402


def _fake_response(
    *,
    text: str = "hello",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_tokens: int = 15,
    cached_tokens_openai_style: int | None = None,
    cached_tokens_anthropic_style: int | None = None,
    response_id: str = "resp-1",
    model: str = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
):
    """Build a SimpleNamespace object mimicking a LiteLLM ModelResponse."""
    message = types.SimpleNamespace(content=text, tool_calls=tool_calls)
    choice = types.SimpleNamespace(message=message, finish_reason=finish_reason)
    usage_meta: dict = {}
    usage = types.SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    if cached_tokens_openai_style is not None:
        usage.prompt_tokens_details = types.SimpleNamespace(cached_tokens=cached_tokens_openai_style)
    if cached_tokens_anthropic_style is not None:
        usage.cache_read_input_tokens = cached_tokens_anthropic_style
    response = types.SimpleNamespace(
        choices=[choice],
        usage=usage,
        id=response_id,
        model=model,
    )
    response.model_dump = lambda: {"id": response_id, "model": model}
    return response


@pytest.mark.asyncio
async def test_generate_plain_text_and_usage(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response(text="hi there", prompt_tokens=7, completion_tokens=3, total_tokens=10)

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo", max_tokens=100)
    resp = await client.generate(messages=[{"role": "user", "content": "hey"}])

    assert resp.output_text == "hi there"
    assert resp.content == [{"type": "text", "text": "hi there"}]
    assert resp.usage.input_tokens == 7
    assert resp.usage.output_tokens == 3
    assert resp.usage.total_tokens == 10
    assert resp.provider == "litellm:bedrock"
    assert resp.response_id == "resp-1"
    assert captured["model"] == "bedrock/foo"
    assert captured["messages"] == [{"role": "user", "content": "hey"}]


@pytest.mark.asyncio
async def test_generate_prompt_cache_dual_style(monkeypatch):
    async def fake_acompletion(**kwargs):
        return _fake_response(cached_tokens_openai_style=4, cached_tokens_anthropic_style=6)

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    resp = await client.generate(messages=[{"role": "user", "content": "x"}])

    # Anthropic-style wins when both present (it's the newer field).
    assert resp.usage.metadata["cache_read_input_tokens"] == 6


@pytest.mark.asyncio
async def test_generate_prompt_cache_openai_style_only(monkeypatch):
    async def fake_acompletion(**kwargs):
        return _fake_response(cached_tokens_openai_style=4)

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    resp = await client.generate(messages=[{"role": "user", "content": "x"}])

    assert resp.usage.metadata["cache_read_input_tokens"] == 4


@pytest.mark.asyncio
async def test_generate_response_format_json(monkeypatch):
    async def fake_acompletion(**kwargs):
        return _fake_response(text='{"answer": 42}')

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="gemini/gemini-1.5-pro")
    resp = await client.generate(
        messages=[{"role": "user", "content": "x"}],
        response_format={"type": "json_object"},
    )

    assert resp.structured_output == {"answer": 42}


@pytest.mark.asyncio
async def test_generate_cost_with_pricing(monkeypatch):
    async def fake_acompletion(**kwargs):
        return _fake_response(prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    pricing = LLMPricing(input=3.0, output=15.0)
    client = LiteLLMClient(model="bedrock/foo", pricing=pricing)
    resp = await client.generate(messages=[{"role": "user", "content": "x"}])

    assert resp.usage.metadata["cost_usd"] == pytest.approx(18.0)


@pytest.mark.asyncio
async def test_generate_cost_without_pricing(monkeypatch):
    async def fake_acompletion(**kwargs):
        return _fake_response()

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")  # no pricing
    resp = await client.generate(messages=[{"role": "user", "content": "x"}])

    assert resp.usage.metadata["cost_usd"] is None
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py`
Expected: the 6 new tests FAIL with `NotImplementedError` or `AttributeError` on `generate`.

- [ ] **Step 3: Implement `generate()` and translation helpers**

Add to `openagents/llm/providers/litellm_client.py`:

```python
import json as _json

from openagents.llm.base import (
    LLMResponse,
    LLMUsage,
    LLMToolCall,
    _parse_structured_output,
)


def _extract_cached_tokens(usage_obj: Any) -> int:
    """Read prompt-cache tokens from both OpenAI-style and Anthropic-style fields.

    Anthropic-style ``cache_read_input_tokens`` (if present and non-zero) wins;
    otherwise fall back to OpenAI-style ``prompt_tokens_details.cached_tokens``.
    """
    anthropic_style = getattr(usage_obj, "cache_read_input_tokens", None)
    if isinstance(anthropic_style, int) and anthropic_style > 0:
        return anthropic_style
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if isinstance(cached, int) and cached > 0:
            return cached
    return 0


def _parse_tool_calls(raw: Any) -> list[LLMToolCall]:
    if not raw:
        return []
    out: list[LLMToolCall] = []
    for tc in raw:
        fn = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
        if fn is None:
            continue
        name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None) or ""
        args_raw = getattr(fn, "arguments", None) if not isinstance(fn, dict) else fn.get("arguments")
        tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
        args_str = args_raw if isinstance(args_raw, str) else _json.dumps(args_raw or {})
        try:
            args_dict = _json.loads(args_str) if args_str else {}
            if not isinstance(args_dict, dict):
                args_dict = {}
        except (TypeError, _json.JSONDecodeError):
            args_dict = {}
        out.append(LLMToolCall(name=name, arguments=args_dict, id=tc_id, raw_arguments=args_str))
    return out


class LiteLLMClient(LLMClient):
    # ... existing __init__/aclose unchanged ...

    async def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stream=False,
        )
        raw = await litellm.acompletion(**kwargs)
        return self._to_llm_response(raw, response_format=response_format)

    def _build_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        response_format: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        effective_model = model or self.model_id
        effective_temp = temperature if temperature is not None else self._default_temperature
        effective_max = max_tokens if max_tokens is not None else self._max_tokens

        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "max_tokens": effective_max,
            "timeout": self._timeout_s,
            "stream": stream,
        }
        if effective_temp is not None:
            kwargs["temperature"] = effective_temp
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._extra_headers:
            kwargs["extra_headers"] = self._extra_headers
        api_key = self._resolve_api_key()
        if api_key is not None:
            kwargs["api_key"] = api_key
        kwargs.update(self._extra_kwargs)
        return kwargs

    def _resolve_api_key(self) -> str | None:
        if not self._api_key_env:
            return None
        return os.environ.get(self._api_key_env) or None

    def _to_llm_response(
        self,
        raw: Any,
        *,
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        choice = raw.choices[0]
        message = choice.message
        text = message.content or ""
        tool_calls = _parse_tool_calls(getattr(message, "tool_calls", None))

        usage_obj = getattr(raw, "usage", None)
        usage = LLMUsage(
            input_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
            metadata={"cache_read_input_tokens": _extract_cached_tokens(usage_obj)}
            if usage_obj is not None
            else {},
        ).normalized()
        usage = self._compute_cost_for(usage=usage, overrides=self._pricing)

        dump = raw.model_dump() if hasattr(raw, "model_dump") else None

        response = LLMResponse(
            output_text=text,
            content=[{"type": "text", "text": text}] if text else [],
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=getattr(choice, "finish_reason", None),
            structured_output=_parse_structured_output(text, response_format),
            model=getattr(raw, "model", self.model_id),
            provider=self.provider_name,
            response_id=getattr(raw, "id", None),
            raw=dump,
        )
        return self._store_response(response)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py`
Expected: all tests in file PASS (13 so far).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/llm/providers/litellm_client.py tests/unit/llm/providers/test_litellm_client.py
rtk git commit -m "feat(llm): LiteLLMClient.generate with usage, cache tokens, cost, structured output"
```

---

## Task 5: `generate()` — Tool Calls & Non-Streaming Error Mapping

**Files:**
- Modify: `openagents/llm/providers/litellm_client.py`
- Modify: `tests/unit/llm/providers/test_litellm_client.py`

**Rationale:** Tool calls are already half-implemented by `_parse_tool_calls` (added in Task 4) but need a dedicated test. Exception mapping wraps the raw `litellm.acompletion` call.

- [ ] **Step 1: Add failing tests**

Append to test file:

```python
from openagents.errors.exceptions import (  # noqa: E402
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
)


@pytest.mark.asyncio
async def test_generate_tool_calls(monkeypatch):
    tc = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="search", arguments='{"q": "kittens"}'),
    )

    async def fake_acompletion(**kwargs):
        return _fake_response(text="", tool_calls=[tc], finish_reason="tool_calls")

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    resp = await client.generate(messages=[{"role": "user", "content": "find kittens"}])

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "search"
    assert resp.tool_calls[0].arguments == {"q": "kittens"}
    assert resp.tool_calls[0].raw_arguments == '{"q": "kittens"}'
    assert resp.tool_calls[0].id == "call_1"
    assert resp.stop_reason == "tool_calls"


@pytest.mark.asyncio
async def test_generate_tool_calls_invalid_json_keeps_raw(monkeypatch):
    tc = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="search", arguments='{"q": '),  # invalid
    )

    async def fake_acompletion(**kwargs):
        return _fake_response(text="", tool_calls=[tc], finish_reason="tool_calls")

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    resp = await client.generate(messages=[{"role": "user", "content": "x"}])

    assert resp.tool_calls[0].arguments == {}
    assert resp.tool_calls[0].raw_arguments == '{"q": '


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_class_name,expected_sdk_exc",
    [
        ("RateLimitError", LLMRateLimitError),
        ("APIConnectionError", LLMConnectionError),
        ("Timeout", LLMConnectionError),
        ("APIError", LLMResponseError),
    ],
)
async def test_generate_maps_litellm_exceptions(monkeypatch, exc_class_name, expected_sdk_exc):
    exc_class = getattr(lc_module.litellm.exceptions, exc_class_name)

    # litellm exceptions have varying signatures; build whichever works.
    try:
        exc_instance = exc_class("boom", model="bedrock/foo", llm_provider="bedrock")
    except TypeError:
        try:
            exc_instance = exc_class("boom")
        except TypeError:
            exc_instance = exc_class(message="boom", model="bedrock/foo", llm_provider="bedrock")

    async def fake_acompletion(**kwargs):
        raise exc_instance

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    with pytest.raises(expected_sdk_exc):
        await client.generate(messages=[{"role": "user", "content": "x"}])
```

- [ ] **Step 2: Run tests — expect tool-call tests PASS (already covered by Task 4 code), exception tests FAIL**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py -k "tool_calls or maps_litellm_exceptions"`
Expected: the 2 tool-call tests PASS, the 4 parametrized exception tests FAIL (errors propagate unmapped).

- [ ] **Step 3: Add exception mapper**

Add helper to `openagents/llm/providers/litellm_client.py`:

```python
from openagents.errors.exceptions import (
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
)


def _map_litellm_exception(exc: BaseException) -> Exception:
    exc_module = type(exc).__module__
    name = type(exc).__name__
    if exc_module.startswith("litellm"):
        if name == "RateLimitError":
            return LLMRateLimitError(str(exc))
        if name in ("APIConnectionError", "Timeout"):
            return LLMConnectionError(str(exc))
        # APIError and subclasses, plus unknown litellm exceptions
        return LLMResponseError(str(exc))
    # Non-litellm — let caller re-raise original
    return exc
```

Wrap the `litellm.acompletion` call in `generate()`:

```python
try:
    raw = await litellm.acompletion(**kwargs)
except Exception as exc:
    mapped = _map_litellm_exception(exc)
    if mapped is exc:
        raise
    raise mapped from exc
```

- [ ] **Step 4: Run tests — expect all PASS**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py`
Expected: all tests in file PASS (19 including parametrized).

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/llm/providers/litellm_client.py tests/unit/llm/providers/test_litellm_client.py
rtk git commit -m "feat(llm): LiteLLMClient tool calls and typed exception mapping"
```

---

## Task 6: `complete_stream()` — Deltas, message_stop, Tool Call Increments, Errors

**Files:**
- Modify: `openagents/llm/providers/litellm_client.py`
- Modify: `tests/unit/llm/providers/test_litellm_client.py`

**Rationale:** Streaming is the second-largest surface. Bundle four streaming tests so `complete_stream` is implemented once and verified against all its obligations.

- [ ] **Step 1: Add 4 failing streaming tests**

Append:

```python
from openagents.llm.base import LLMChunk  # noqa: E402


def _mk_stream_chunk(*, content_delta: str | None = None, tool_call_delta=None, usage=None, finish_reason=None):
    delta = types.SimpleNamespace(
        content=content_delta,
        tool_calls=tool_call_delta,
    )
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish_reason)
    chunk = types.SimpleNamespace(choices=[choice])
    if usage is not None:
        chunk.usage = usage
    return chunk


async def _async_gen(items):
    for it in items:
        yield it


@pytest.mark.asyncio
async def test_stream_yields_content_deltas_and_message_stop(monkeypatch):
    usage = types.SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    chunks = [
        _mk_stream_chunk(content_delta="hel"),
        _mk_stream_chunk(content_delta="lo"),
        _mk_stream_chunk(usage=usage, finish_reason="stop"),
    ]

    async def fake_acompletion(**kwargs):
        return _async_gen(chunks)

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    collected = []
    async for c in client.complete_stream(messages=[{"role": "user", "content": "hi"}]):
        collected.append(c)

    content_chunks = [c for c in collected if c.type == "content_block_delta"]
    assert [c.delta for c in content_chunks] == ["hel", "lo"]
    stop = [c for c in collected if c.type == "message_stop"]
    assert len(stop) == 1
    assert stop[0].usage.input_tokens == 2
    assert stop[0].usage.output_tokens == 3


@pytest.mark.asyncio
async def test_stream_tool_call_increments_concat(monkeypatch):
    tc_part1 = types.SimpleNamespace(
        index=0,
        id="call_1",
        function=types.SimpleNamespace(name="search", arguments='{"q": '),
    )
    tc_part2 = types.SimpleNamespace(
        index=0,
        id=None,
        function=types.SimpleNamespace(name=None, arguments='"kittens"}'),
    )
    chunks = [
        _mk_stream_chunk(tool_call_delta=[tc_part1]),
        _mk_stream_chunk(tool_call_delta=[tc_part2]),
        _mk_stream_chunk(finish_reason="tool_calls"),
    ]

    async def fake_acompletion(**kwargs):
        return _async_gen(chunks)

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    tool_deltas = []
    async for c in client.complete_stream(messages=[{"role": "user", "content": "x"}]):
        if c.type == "content_block_delta" and isinstance(c.delta, dict):
            tool_deltas.append(c.delta)

    # Last delta should have accumulated arguments form a valid JSON when concatenated.
    combined_args = "".join(d["tool_use"].get("arguments_delta", "") for d in tool_deltas)
    assert combined_args == '{"q": "kittens"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_class_name,expected_error_type",
    [
        ("RateLimitError", "rate_limit"),
        ("APIConnectionError", "connection"),
        ("Timeout", "connection"),
        ("APIError", "response"),
    ],
)
async def test_stream_maps_exceptions_to_error_chunks(monkeypatch, exc_class_name, expected_error_type):
    exc_class = getattr(lc_module.litellm.exceptions, exc_class_name)
    try:
        exc_instance = exc_class("boom", model="bedrock/foo", llm_provider="bedrock")
    except TypeError:
        try:
            exc_instance = exc_class("boom")
        except TypeError:
            exc_instance = exc_class(message="boom", model="bedrock/foo", llm_provider="bedrock")

    async def fake_stream():
        raise exc_instance
        yield  # noqa: unreachable  (makes it an async generator)

    async def fake_acompletion(**kwargs):
        return fake_stream()

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    collected = []
    async for c in client.complete_stream(messages=[{"role": "user", "content": "x"}]):
        collected.append(c)

    errors = [c for c in collected if c.type == "error"]
    assert len(errors) == 1
    assert errors[0].error_type == expected_error_type
```

- [ ] **Step 2: Run tests — expect FAIL (streaming not implemented)**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py -k "stream"`
Expected: new streaming tests FAIL (base `LLMClient.complete_stream` default yields one delta then stop, so the concatenation and error-chunk assertions fail).

- [ ] **Step 3: Implement `complete_stream()`**

Add to `litellm_client.py`:

```python
from typing import AsyncIterator


_STREAM_ERROR_TYPE_BY_NAME: dict[str, str] = {
    "RateLimitError": "rate_limit",
    "APIConnectionError": "connection",
    "Timeout": "connection",
    "APIError": "response",
}


def _classify_litellm_error_type(exc: BaseException) -> str:
    name = type(exc).__name__
    if type(exc).__module__.startswith("litellm"):
        return _STREAM_ERROR_TYPE_BY_NAME.get(name, "response")
    return "unknown"


class LiteLLMClient(LLMClient):
    # ... existing methods ...

    async def complete_stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMChunk]:
        kwargs = self._build_kwargs(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stream=True,
        )
        kwargs.setdefault("stream_options", {"include_usage": True})

        try:
            stream = await litellm.acompletion(**kwargs)
        except Exception as exc:
            yield LLMChunk(
                type="error",
                error=str(exc),
                error_type=_classify_litellm_error_type(exc),
            )
            return

        try:
            last_usage = None
            async for chunk in stream:
                choice = (chunk.choices or [None])[0]
                if choice is None:
                    continue
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                content_piece = getattr(delta, "content", None)
                if content_piece:
                    yield LLMChunk(type="content_block_delta", delta=content_piece)
                tool_deltas = getattr(delta, "tool_calls", None) or []
                for td in tool_deltas:
                    fn = getattr(td, "function", None)
                    name = getattr(fn, "name", None) if fn else None
                    args_delta = getattr(fn, "arguments", None) if fn else None
                    yield LLMChunk(
                        type="content_block_delta",
                        delta={
                            "tool_use": {
                                "index": getattr(td, "index", None),
                                "id": getattr(td, "id", None),
                                "name": name,
                                "arguments_delta": args_delta or "",
                            }
                        },
                    )
                usage_obj = getattr(chunk, "usage", None)
                if usage_obj is not None:
                    last_usage = LLMUsage(
                        input_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                        output_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
                        total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
                        metadata={"cache_read_input_tokens": _extract_cached_tokens(usage_obj)},
                    ).normalized()
        except Exception as exc:
            yield LLMChunk(
                type="error",
                error=str(exc),
                error_type=_classify_litellm_error_type(exc),
            )
            return

        yield LLMChunk(type="message_stop", usage=last_usage)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/llm/providers/litellm_client.py tests/unit/llm/providers/test_litellm_client.py
rtk git commit -m "feat(llm): LiteLLMClient streaming with tool call deltas and error classification"
```

---

## Task 7: `count_tokens`, Retry Mapping, Kwargs Whitelist, `api_key_env` Fallback

**Files:**
- Modify: `openagents/llm/providers/litellm_client.py`
- Modify: `tests/unit/llm/providers/test_litellm_client.py`

**Rationale:** Four independent surface details grouped in one task because each is small and lives in the init/kwargs path.

- [ ] **Step 1: Add 4 failing tests**

Append:

```python
from openagents.config.schema import LLMRetryOptions  # noqa: E402


def test_count_tokens_uses_litellm_token_counter(monkeypatch):
    captured = {}

    def fake_counter(**kwargs):
        captured.update(kwargs)
        return 42

    monkeypatch.setattr(lc_module.litellm, "token_counter", fake_counter)
    client = LiteLLMClient(model="bedrock/foo")
    assert client.count_tokens("hello world") == 42
    assert captured == {"model": "bedrock/foo", "text": "hello world"}


def test_count_tokens_fallback_on_exception(monkeypatch, caplog):
    def fake_counter(**kwargs):
        raise RuntimeError("no tokenizer")

    monkeypatch.setattr(lc_module.litellm, "token_counter", fake_counter)
    client = LiteLLMClient(model="bedrock/foo")
    with caplog.at_level("WARNING", logger="openagents.llm"):
        n = client.count_tokens("hello")
    assert n == max(1, len("hello") // 4)


@pytest.mark.asyncio
async def test_retry_options_mapped_to_litellm(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    retry = LLMRetryOptions(max_attempts=3, retry_on_connection_errors=True)
    client = LiteLLMClient(model="bedrock/foo", retry_options=retry)
    await client.generate(messages=[{"role": "user", "content": "x"}])

    assert captured["num_retries"] == 2
    rp = captured["retry_policy"]
    assert isinstance(rp, lc_module.litellm.RetryPolicy)
    assert rp.TimeoutErrorRetries == 2
    assert rp.RateLimitErrorRetries == 2
    assert rp.AuthenticationErrorRetries == 0
    assert rp.BadRequestErrorRetries == 0


@pytest.mark.asyncio
async def test_retry_options_without_connection_retries_omits_retry_policy(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    retry = LLMRetryOptions(max_attempts=5, retry_on_connection_errors=False)
    client = LiteLLMClient(model="bedrock/foo", retry_options=retry)
    await client.generate(messages=[{"role": "user", "content": "x"}])

    assert captured["num_retries"] == 4
    assert "retry_policy" not in captured


@pytest.mark.asyncio
async def test_extra_kwargs_and_api_key_env_fallback(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    monkeypatch.delenv("SOME_MISSING_KEY", raising=False)

    client = LiteLLMClient(
        model="bedrock/foo",
        api_key_env="SOME_MISSING_KEY",
        extra_kwargs={"aws_region_name": "us-east-1"},
    )
    await client.generate(messages=[{"role": "user", "content": "x"}])

    assert captured["aws_region_name"] == "us-east-1"
    assert "api_key" not in captured  # env missing → no api_key forwarded
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py -k "count_tokens or retry_options or extra_kwargs"`
Expected: FAIL (`count_tokens` falls back to base `len//4`; retry mapping not present).

- [ ] **Step 3: Implement `count_tokens` + retry mapping**

Add to `openagents/llm/providers/litellm_client.py`:

```python
def _build_retry_policy_kwargs(retry_options: "LLMRetryOptions | None") -> dict[str, Any]:
    """Translate SDK LLMRetryOptions → LiteLLM kwargs.

    - ``max_attempts - 1`` → ``num_retries``
    - When ``retry_on_connection_errors`` is True, add a structured
      ``litellm.RetryPolicy`` limiting retries to Timeout + RateLimit.
    """
    if retry_options is None:
        return {}
    num_retries = max(int(retry_options.max_attempts) - 1, 0)
    kwargs: dict[str, Any] = {"num_retries": num_retries}
    if retry_options.retry_on_connection_errors and num_retries > 0:
        kwargs["retry_policy"] = litellm.RetryPolicy(
            TimeoutErrorRetries=num_retries,
            RateLimitErrorRetries=num_retries,
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            ContentPolicyViolationErrorRetries=0,
        )
    return kwargs


class LiteLLMClient(LLMClient):
    # ... existing code ...

    def count_tokens(self, text: str) -> int:
        try:
            return int(litellm.token_counter(model=self.model_id, text=text or ""))
        except Exception:
            return super().count_tokens(text or "")
```

Update `_build_kwargs` to merge retry kwargs. Replace the end of `_build_kwargs`:

```python
kwargs.update(self._extra_kwargs)
kwargs.update(_build_retry_policy_kwargs(self._retry_options))
return kwargs
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/llm/providers/test_litellm_client.py`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
rtk git add openagents/llm/providers/litellm_client.py tests/unit/llm/providers/test_litellm_client.py
rtk git commit -m "feat(llm): LiteLLMClient count_tokens and retry policy mapping"
```

---

## Task 8: Registry Integration + Kwargs Whitelist Helper + ImportError Test

**Files:**
- Modify: `openagents/llm/registry.py`
- Modify: `tests/unit/llm/test_registry.py`

**Rationale:** Wire `provider="litellm"` through `create_llm_client`, enforce the kwargs whitelist at the boundary, and test the ImportError path.

- [ ] **Step 1: Add failing tests to `tests/unit/llm/test_registry.py`**

Append to `D:\Project\openagent-python-sdk\tests\unit\llm\test_registry.py`:

```python
def test_create_llm_client_litellm_routes_to_litellm_client():
    import pytest
    _ = pytest.importorskip("litellm")
    from openagents.llm.providers.litellm_client import LiteLLMClient
    config = LLMOptions(
        provider="litellm",
        model="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    )
    client = create_llm_client(config)
    assert isinstance(client, LiteLLMClient)
    assert client.provider_name == "litellm:bedrock"


def test_create_llm_client_litellm_forwards_whitelisted_kwargs():
    import pytest
    _ = pytest.importorskip("litellm")
    config = LLMOptions(
        provider="litellm",
        model="bedrock/foo",
        aws_region_name="us-east-1",  # whitelisted extra
    )
    client = create_llm_client(config)
    assert client._extra_kwargs.get("aws_region_name") == "us-east-1"


def test_create_llm_client_litellm_drops_non_whitelisted_kwargs(caplog):
    import pytest
    _ = pytest.importorskip("litellm")
    config = LLMOptions(
        provider="litellm",
        model="bedrock/foo",
        fallbacks=["some-model"],  # blacklisted: must not forward
        callbacks=["sentinel"],    # blacklisted
    )
    with caplog.at_level("WARNING", logger="openagents.llm"):
        client = create_llm_client(config)
    assert "fallbacks" not in client._extra_kwargs
    assert "callbacks" not in client._extra_kwargs


def test_create_llm_client_litellm_raises_config_error_when_package_missing(monkeypatch):
    import pytest
    _ = pytest.importorskip("litellm")
    from openagents.llm.providers import litellm_client as lc_module
    from openagents.errors.exceptions import ConfigError

    monkeypatch.setattr(lc_module, "litellm", None)
    config = LLMOptions(provider="litellm", model="bedrock/foo")
    with pytest.raises(ConfigError) as excinfo:
        create_llm_client(config)
    assert "pip install" in str(excinfo.value)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `rtk uv run pytest -q tests/unit/llm/test_registry.py -k "litellm"`
Expected: FAIL with `ConfigError: Unsupported llm.provider: 'litellm'` (current behavior).

- [ ] **Step 3: Add the registry branch + helper**

Edit `D:\Project\openagent-python-sdk\openagents\llm\registry.py`:

Add import near the top (keep module lazy import inside the branch if possible — since `LiteLLMClient` import itself triggers the `try: import litellm` guard, a direct top-level import is fine as long as its module body doesn't fail when `litellm` is absent; our module already handles `ImportError: litellm = None`):

```python
import logging
```

Add module-level helper at bottom of file:

```python
logger = logging.getLogger("openagents.llm")


def _extract_litellm_kwargs(llm: LLMOptions) -> dict:
    """Filter ``LLMOptions`` extras against LiteLLM whitelist; warn on drops."""
    from openagents.llm.providers.litellm_client import _FORWARDABLE_KWARGS

    allowed: dict = {}
    known_fields = set(LLMOptions.model_fields.keys())
    for key, value in (llm.model_dump(exclude=known_fields, exclude_none=True) or {}).items():
        if key in _FORWARDABLE_KWARGS:
            allowed[key] = value
        else:
            logger.warning(
                "Unknown litellm kwarg '%s' in LLMOptions; ignored. "
                "Add to whitelist in litellm_client.py if needed.",
                key,
            )
    return allowed
```

Add the new branch in `create_llm_client` before the final `raise`:

```python
    if provider == "litellm":
        from openagents.llm.providers.litellm_client import LiteLLMClient
        return LiteLLMClient(
            model=llm.model or "",
            api_base=llm.api_base,
            api_key_env=llm.api_key_env,
            timeout_ms=llm.timeout_ms,
            default_temperature=llm.temperature,
            max_tokens=llm.max_tokens or 1024,
            pricing=llm.pricing,
            retry_options=llm.retry,
            extra_headers=extra_headers,
            extra_kwargs=_extract_litellm_kwargs(llm),
        )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `rtk uv run pytest -q tests/unit/llm/test_registry.py`
Expected: all tests PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `rtk uv run pytest -q`
Expected: all tests PASS, coverage `>=92%`.

- [ ] **Step 6: Commit**

```bash
rtk git add openagents/llm/registry.py tests/unit/llm/test_registry.py
rtk git commit -m "feat(llm): registry routes provider=litellm with whitelisted extras"
```

---

## Task 9: Full-Suite Verification + Ruff Lint

**Files:** none modified

**Rationale:** Final gate before docs. Confirm green tests, coverage floor, and lint.

- [ ] **Step 1: Run full test suite**

Run: `rtk uv run pytest -q`
Expected: 0 failures.

- [ ] **Step 2: Run coverage report**

Run: `rtk uv run coverage run -m pytest -q && rtk uv run coverage report`
Expected: `TOTAL` line shows `>=92%`.

- [ ] **Step 3: Run ruff**

Run: `rtk uv run ruff check openagents tests`
Expected: `All checks passed!`

- [ ] **Step 4: (No-op commit if anything was auto-fixed by ruff; otherwise skip)**

If `ruff check --fix` was needed, commit the fixes:
```bash
rtk git add -u
rtk git commit -m "style: ruff autofix after litellm provider addition"
```
Otherwise, proceed.

---

## Task 10: Documentation Updates

**Files:**
- Modify: `docs/configuration.md` + `docs/configuration.en.md`
- Modify: `docs/developer-guide.md` + `docs/developer-guide.en.md`
- Modify: `README.md` + `README.zh-CN.md`

**Rationale:** Surface the new provider to users. Ship docs together with the feature.

- [ ] **Step 1: Read current docs structure**

```bash
rtk grep -n "openai_compatible" D:/Project/openagent-python-sdk/docs/configuration.md
rtk grep -n "openai_compatible" D:/Project/openagent-python-sdk/docs/configuration.en.md
rtk grep -n "openai_compatible" D:/Project/openagent-python-sdk/docs/developer-guide.md
rtk grep -n "openai_compatible" D:/Project/openagent-python-sdk/docs/developer-guide.en.md
rtk grep -n "openai_compatible\|Provider" D:/Project/openagent-python-sdk/README.md
```
Locate the sections that list current providers.

- [ ] **Step 2: Edit `docs/configuration.md` — add "LiteLLM Provider(可选)" section**

Insert after the `openai_compatible` provider subsection:

````markdown
### LiteLLM Provider(可选)

`provider: "litellm"` 通过 [LiteLLM](https://docs.litellm.ai) 对接**非 OpenAI 协议**的后端:AWS Bedrock、Google Vertex AI、Gemini 原生 API、Cohere、Azure OpenAI deployment 等。**如果后端已经是 OpenAI 兼容协议,优先使用 `openai_compatible`**,更轻量。

安装:
```bash
uv pip install "io-openagent-sdk[litellm]"
```

Bedrock 示例:
```json
{
  "llm": {
    "provider": "litellm",
    "model": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    "aws_region_name": "us-east-1",
    "max_tokens": 4096,
    "pricing": {"input": 3.0, "output": 15.0}
  }
}
```

Vertex 示例:
```json
{
  "llm": {
    "provider": "litellm",
    "model": "vertex_ai/gemini-1.5-pro",
    "vertex_project": "my-gcp-project",
    "vertex_location": "us-central1"
  }
}
```

Gemini 原生示例:
```json
{
  "llm": {
    "provider": "litellm",
    "model": "gemini/gemini-1.5-pro",
    "api_key_env": "GEMINI_API_KEY"
  }
}
```

**透传白名单**(其他 extra 字段会被忽略并告警):
`aws_region_name` · `aws_access_key_id` · `aws_secret_access_key` · `aws_session_token` · `aws_profile_name` · `vertex_project` · `vertex_location` · `vertex_credentials` · `azure_deployment` · `api_version` · `seed` · `top_p` · `parallel_tool_calls` · `response_format`

**不支持的 LiteLLM 特性**(本 SDK 不接入):router、fallback、budget manager、内置缓存、success/failure callbacks。这些属于产品层语义,用户需要的话请在应用代码里自行组合。

**凭证**:`api_key_env` 给了就读环境变量塞到 `api_key`;没给则**由 LiteLLM 自行从 AWS/GCP 标准环境变量链读取凭证**(如 `AWS_ACCESS_KEY_ID`、`GOOGLE_APPLICATION_CREDENTIALS` 等)。
````

- [ ] **Step 3: Edit `docs/configuration.en.md` — add same section in English**

```markdown
### LiteLLM Provider (Optional)

`provider: "litellm"` reaches **non-OpenAI protocol** backends through [LiteLLM](https://docs.litellm.ai): AWS Bedrock, Google Vertex AI, native Gemini, Cohere, Azure OpenAI deployments. **If your backend already speaks the OpenAI protocol, prefer `openai_compatible`** — it's lighter.

Install:
```bash
uv pip install "io-openagent-sdk[litellm]"
```

Bedrock example:
```json
{
  "llm": {
    "provider": "litellm",
    "model": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    "aws_region_name": "us-east-1",
    "max_tokens": 4096,
    "pricing": {"input": 3.0, "output": 15.0}
  }
}
```

Vertex example:
```json
{
  "llm": {
    "provider": "litellm",
    "model": "vertex_ai/gemini-1.5-pro",
    "vertex_project": "my-gcp-project",
    "vertex_location": "us-central1"
  }
}
```

Native Gemini example:
```json
{
  "llm": {
    "provider": "litellm",
    "model": "gemini/gemini-1.5-pro",
    "api_key_env": "GEMINI_API_KEY"
  }
}
```

**Forwarded kwargs whitelist** (other extras are dropped with a warning):
`aws_region_name`, `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`, `aws_profile_name`, `vertex_project`, `vertex_location`, `vertex_credentials`, `azure_deployment`, `api_version`, `seed`, `top_p`, `parallel_tool_calls`, `response_format`.

**Unsupported LiteLLM features** (intentionally omitted): router, fallback, budget manager, built-in cache, success/failure callbacks — these are product-layer concerns and belong in your app code.

**Credentials:** if `api_key_env` is set the SDK reads that env and passes `api_key=...`. Otherwise LiteLLM reads its own standard env chain (`AWS_ACCESS_KEY_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, etc.).
```

- [ ] **Step 4: Edit `docs/developer-guide.md` and `docs/developer-guide.en.md`**

Find the provider list and add one row for `litellm`:
- CN: `| litellm | 可选 extra。访问 Bedrock / Vertex / Gemini 等非 OpenAI 协议后端 |`
- EN: `| litellm | Optional extra. Reaches Bedrock / Vertex / Gemini and other non-OpenAI backends. |`

(Exact column format depends on what `rtk grep` in Step 1 reveals. Match existing style.)

- [ ] **Step 5: Edit `README.md` and `README.zh-CN.md`**

Find the provider support table/list and add a row/bullet for LiteLLM, in both files. Also add a one-line release note near the top under recent changes / unreleased.

- [ ] **Step 6: Verify docs render cleanly (optional)**

If there's a local docs preview: run it. Otherwise skim the markdown for broken headings.

- [ ] **Step 7: Run tests once more to ensure docs didn't break anything**

Run: `rtk uv run pytest -q tests/unit/test_repository_layout.py`
Expected: PASS (this test checks docs layout per `test_repository_layout.py`).

- [ ] **Step 8: Commit**

```bash
rtk git add docs/configuration.md docs/configuration.en.md docs/developer-guide.md docs/developer-guide.en.md README.md README.zh-CN.md
rtk git commit -m "docs: add LiteLLM optional provider documentation"
```

---

## Post-Implementation Checklist

- [ ] All 10 tasks completed and committed
- [ ] `rtk uv run pytest -q` green
- [ ] `rtk uv run coverage report` ≥92%
- [ ] `rtk uv run ruff check openagents tests` clean
- [ ] `litellm` appears in `[project.optional-dependencies]` as `litellm`, `dev`, `all`
- [ ] `openagents/llm/providers/litellm_client.py` in `[tool.coverage.report].omit`
- [ ] Spec at `docs/superpowers/specs/2026-04-22-litellm-provider-integration-design.md` matches implementation
- [ ] Fresh user can do `uv pip install "io-openagent-sdk[litellm]"`, set `provider: "litellm"`, and run against Bedrock/Vertex/Gemini

---

## Risks & Mitigations Summary

| Risk | Mitigation |
| --- | --- |
| LiteLLM API field rename on upgrade | Pin `>=1.50.0`; all LiteLLM field access centralized in `litellm_client.py` |
| Process-level telemetry lockdown surprises users | Docstring warns; tests assert the assignments |
| Whitelist misses a common kwarg | Warning logged on drop with actionable hint (`"Add to whitelist in litellm_client.py"`) |
| Coverage regression in CI without litellm extra | File in `coverage.report.omit`; tests `pytest.importorskip("litellm")` |
| Spec/Impl drift | This plan's tests encode the spec's 16-test coverage clause verbatim |
