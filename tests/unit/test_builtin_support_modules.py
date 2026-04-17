from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
from openagents.plugins.builtin.followup.basic import BasicFollowupResolver
from openagents.plugins.builtin.response_repair.basic import BasicResponseRepairPolicy
from openagents.utils.hotreload import ConfigWatcher, HotReloadServer


class _DummyRuntime:
    def __init__(self) -> None:
        self.reload_calls = 0

    async def reload(self) -> None:
        self.reload_calls += 1

    async def run(self, *, agent_id: str, session_id: str, input_text: str):
        return f"{agent_id}:{session_id}:{input_text}"

    async def list_agents(self):
        return [{"id": "assistant", "name": "Assistant"}]


@pytest.mark.asyncio
async def test_async_event_bus_emits_to_named_and_wildcard_handlers_and_trims_history():
    bus = AsyncEventBus({"max_history": 2})
    seen: list[tuple[str, object]] = []

    async def async_handler(event):
        seen.append(("async", event.name))

    def wildcard_handler(event):
        seen.append(("wildcard", event.payload.get("value")))

    bus.subscribe("demo", async_handler)
    bus.subscribe("*", wildcard_handler)

    await bus.emit("demo", value=1)
    await bus.emit("other", value=2)
    await bus.emit("demo", value=3)

    history = await bus.get_history()
    filtered = await bus.get_history("demo", limit=1)

    assert seen == [
        ("async", "demo"),
        ("wildcard", 1),
        ("wildcard", 2),
        ("async", "demo"),
        ("wildcard", 3),
    ]
    assert [event.name for event in history] == ["other", "demo"]
    assert [event.payload["value"] for event in filtered] == [3]

    await bus.clear_history()
    assert await bus.get_history() == []


@pytest.mark.asyncio
async def test_basic_followup_resolver_handles_none_abstain_and_resolved_paths():
    resolver = BasicFollowupResolver()

    none_result = await resolver.resolve(
        context=SimpleNamespace(input_text="hello", memory_view={})
    )
    no_history_result = await resolver.resolve(
        context=SimpleNamespace(input_text="what did you do", memory_view={})
    )
    malformed_result = await resolver.resolve(
        context=SimpleNamespace(
            input_text="what did you just do",
            memory_view={"history": ["bad-item"]},
        )
    )
    resolved = await resolver.resolve(
        context=SimpleNamespace(
            input_text="你刚干了什么",
            memory_view={
                "history": [
                    {
                        "input": "scan the repository",
                        "output": "done",
                        "tool_results": [
                            {"tool_id": "read_file"},
                            {"tool_id": "ripgrep"},
                            {"tool_id": ""},
                        ],
                    }
                ]
            },
        )
    )

    assert none_result is None
    assert no_history_result is not None and no_history_result.status == "abstain"
    assert malformed_result is not None and malformed_result.status == "abstain"
    assert resolved is not None and resolved.status == "resolved"
    assert "scan the repository" in (resolved.output or "")
    assert resolved.metadata == {"tool_ids": ["read_file", "ripgrep"]}


@pytest.mark.asyncio
async def test_basic_followup_resolver_abstains_when_history_has_no_action_details():
    resolver = BasicFollowupResolver()

    result = await resolver.resolve(
        context=SimpleNamespace(
            input_text="上一轮干了什么",
            memory_view={"history": [{}]},
        )
    )

    assert result is not None
    assert result.status == "abstain"
    assert "enough action detail" in (result.reason or "")


@pytest.mark.asyncio
async def test_basic_response_repair_policy_reports_diagnostic_metadata():
    policy = BasicResponseRepairPolicy()
    context = SimpleNamespace(
        input_text="x" * 140,
        tools={"read_file": object()},
        memory_view={"history": [{"input": "old"}]},
    )
    messages = [
        {"role": "assistant", "content": [{"type": "tool_result", "content": "ok"}]},
    ]

    result = await policy.repair_empty_response(
        context=context,
        messages=messages,
        assistant_content=[],
        stop_reason=None,
        retries=2,
    )

    assert result is not None
    assert result.status == "error"
    assert "LLM returned an empty response" in (result.reason or "")
    assert "stop_reason=<none>" in (result.reason or "")
    assert result.metadata == {
        "stop_reason": "<none>",
        "retries": 2,
        "history_items": 1,
        "recent_tool_result": True,
    }


def test_basic_followup_resolver_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        BasicFollowupResolver({"totally_unknown": 1})

    assert any(
        "unknown config keys" in r.message
        and "BasicFollowupResolver" in r.message
        and "totally_unknown" in r.message
        for r in caplog.records
    )


def test_basic_response_repair_policy_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        BasicResponseRepairPolicy({"totally_unknown": 1})

    assert any(
        "unknown config keys" in r.message
        and "BasicResponseRepairPolicy" in r.message
        and "totally_unknown" in r.message
        for r in caplog.records
    )


def test_async_event_bus_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        AsyncEventBus({"totally_unknown": 1})

    assert any(
        "unknown config keys" in r.message
        and "AsyncEventBus" in r.message
        and "totally_unknown" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_config_watcher_detects_file_changes_and_can_be_stopped(tmp_path):
    config_path = tmp_path / "agent.json"
    config_path.write_text("{}", encoding="utf-8")
    runtime = _DummyRuntime()
    watcher = ConfigWatcher(runtime, config_path, poll_interval=0.01)

    await watcher.start()
    await asyncio.sleep(0.03)
    config_path.write_text('{"updated": true}', encoding="utf-8")
    await asyncio.sleep(0.05)
    await watcher.stop()

    assert runtime.reload_calls >= 1
    assert watcher._task is None


@pytest.mark.asyncio
async def test_hotreload_server_falls_back_without_aiohttp_and_stops_cleanly(
    tmp_path, monkeypatch
):
    # Force the ``from aiohttp import web`` inside HotReloadServer.start
    # to raise ImportError so we exercise the fallback (CLI-mode) branch
    # rather than starting an actual HTTP server.
    import sys

    monkeypatch.setitem(sys.modules, "aiohttp", None)

    config_path = tmp_path / "agent.json"
    config_path.write_text("{}", encoding="utf-8")
    runtime = _DummyRuntime()
    server = HotReloadServer(runtime, config_path)

    await server.start()
    assert server._watcher is not None
    assert server._server is None  # fallback path leaves _server unset

    await server.stop()
    assert server._watcher._task is None


def test_build_helpers_load_dotenv_and_apply_openai_env_overrides(monkeypatch, tmp_path):
    build_module = importlib.import_module("openagents.utils.build")
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "# comment\nOPENAI_MODEL=file-model\nOPENAI_BASE_URL=https://example.invalid/v1\nKEEP=from-file\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("KEEP", "already-set")
    build_module.load_dotenv(dotenv_path)

    assert build_module.os.environ["OPENAI_MODEL"] == "file-model"
    assert build_module.os.environ["OPENAI_BASE_URL"] == "https://example.invalid/v1"
    assert build_module.os.environ["KEEP"] == "already-set"

    config_path = tmp_path / "agent.json"
    config_path.write_text(
        '{"agents":[{"llm":{"provider":"openai_compatible","model":"fallback-model","api_base":"https://fallback"}}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env-base/v1")
    captured: dict[str, object] = {}

    class _RuntimeRecorder:
        def __init__(self, config):
            captured["config"] = config

    monkeypatch.setattr(build_module, "Runtime", _RuntimeRecorder)
    monkeypatch.setattr(build_module, "load_config_dict", lambda payload: payload)

    build_module.build_runtime(config_path)

    payload = captured["config"]
    assert isinstance(payload, dict)
    assert payload["agents"][0]["llm"]["model"] == "env-model"
    assert payload["agents"][0]["llm"]["api_base"] == "https://env-base/v1"
    assert payload["agents"][0]["llm"]["api_key_env"] == "OPENAI_API_KEY"
