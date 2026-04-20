from __future__ import annotations

import asyncio
import importlib

import pytest

from openagents.plugins.builtin.events.async_event_bus import AsyncEventBus
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


def test_async_event_bus_warns_on_unknown_config_keys(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        AsyncEventBus({"totally_unknown": 1})

    assert any(
        "unknown config keys" in r.message and "AsyncEventBus" in r.message and "totally_unknown" in r.message
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
async def test_hotreload_server_falls_back_without_aiohttp_and_stops_cleanly(tmp_path, monkeypatch):
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
