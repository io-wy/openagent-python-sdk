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


# ---------- generate() happy path tests ----------

import types  # noqa: E402

from openagents.config.schema import LLMPricing  # noqa: E402


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
    captured: dict = {}

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


# ---------- tool calls + non-streaming exception mapping ----------

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
        function=types.SimpleNamespace(name="search", arguments='{"q": '),  # invalid JSON
    )

    async def fake_acompletion(**kwargs):
        return _fake_response(text="", tool_calls=[tc], finish_reason="tool_calls")

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    resp = await client.generate(messages=[{"role": "user", "content": "x"}])

    assert resp.tool_calls[0].arguments == {}
    assert resp.tool_calls[0].raw_arguments == '{"q": '


def _mk_litellm_exception(exc_class):
    """Construct a LiteLLM exception across its known signature variants.

    LiteLLM 1.x signatures differ per class (e.g. APIError requires
    status_code). Try APIError-shape first, then the common shape.
    """
    name = exc_class.__name__
    if name == "APIError":
        return exc_class(
            status_code=500,
            message="boom",
            llm_provider="bedrock",
            model="bedrock/foo",
        )
    return exc_class(message="boom", llm_provider="bedrock", model="bedrock/foo")


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
    exc_instance = _mk_litellm_exception(exc_class)

    async def fake_acompletion(**kwargs):
        raise exc_instance

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    with pytest.raises(expected_sdk_exc):
        await client.generate(messages=[{"role": "user", "content": "x"}])


# ---------- complete_stream() tests ----------


def _mk_stream_chunk(
    *,
    content_delta: str | None = None,
    tool_call_delta=None,
    usage=None,
    finish_reason=None,
):
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
    exc_instance = _mk_litellm_exception(exc_class)

    async def failing_gen():
        if False:  # pragma: no cover
            yield
        raise exc_instance

    async def fake_acompletion(**kwargs):
        return failing_gen()

    monkeypatch.setattr(lc_module.litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="bedrock/foo")
    collected = []
    async for c in client.complete_stream(messages=[{"role": "user", "content": "x"}]):
        collected.append(c)

    errors = [c for c in collected if c.type == "error"]
    assert len(errors) == 1
    assert errors[0].error_type == expected_error_type


# ---------- count_tokens, retry mapping, api_key_env, extras ----------

from openagents.config.schema import LLMRetryOptions  # noqa: E402


def test_count_tokens_uses_litellm_token_counter(monkeypatch):
    captured: dict = {}

    def fake_counter(**kwargs):
        captured.update(kwargs)
        return 42

    monkeypatch.setattr(lc_module.litellm, "token_counter", fake_counter)
    client = LiteLLMClient(model="bedrock/foo")
    assert client.count_tokens("hello world") == 42
    assert captured == {"model": "bedrock/foo", "text": "hello world"}


def test_count_tokens_fallback_on_exception(monkeypatch):
    def fake_counter(**kwargs):
        raise RuntimeError("no tokenizer")

    monkeypatch.setattr(lc_module.litellm, "token_counter", fake_counter)
    client = LiteLLMClient(model="bedrock/foo")
    n = client.count_tokens("hello")
    assert n == max(1, len("hello") // 4)


@pytest.mark.asyncio
async def test_retry_options_mapped_to_litellm(monkeypatch):
    captured: dict = {}

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
    captured: dict = {}

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
    captured: dict = {}

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
