"""WP5 backfill: exercise HotReloadServer.start/stop with real aiohttp on port 0."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import openagents.llm.registry as llm_registry
from openagents.llm.providers.mock import MockLLMClient
from openagents.runtime.runtime import Runtime
from openagents.utils.hotreload import ConfigWatcher, HotReloadServer

pytest.importorskip("aiohttp")


def _build_config(tmp_path: Path) -> Path:
    payload = {
        "version": "1.0",
        "agents": [
            {
                "id": "alice",
                "name": "alice",
                "memory": {"impl": "tests.fixtures.custom_plugins.CustomMemory"},
                "pattern": {"impl": "tests.fixtures.custom_plugins.CustomPattern"},
                "llm": {"provider": "mock"},
                "tools": [],
                "runtime": {"max_steps": 4, "step_timeout_ms": 1000},
            },
        ],
    }
    cfg = tmp_path / "agents.json"
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    return cfg


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_hot_reload_server_start_stop_on_port_zero(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    cfg = _build_config(tmp_path)
    runtime = Runtime.from_config(cfg)

    server = HotReloadServer(runtime, cfg, host="127.0.0.1", port=0)
    await server.start()
    try:
        assert server._server is not None
        assert server._web_module is not None
    finally:
        await server.stop()
        # second stop is idempotent
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_config_watcher_double_start_is_noop(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    cfg = _build_config(tmp_path)
    runtime = Runtime.from_config(cfg)
    watcher = ConfigWatcher(runtime, cfg, poll_interval=0.05)
    await watcher.start()
    first = watcher._task
    try:
        await watcher.start()  # second call: should be a no-op
        assert watcher._task is first
    finally:
        await watcher.stop()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_config_watcher_reloads_on_mtime_change(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: MockLLMClient())
    cfg = _build_config(tmp_path)
    runtime = Runtime.from_config(cfg)
    reload_count = 0

    async def _fake_reload():
        nonlocal reload_count
        reload_count += 1

    runtime.reload = _fake_reload

    watcher = ConfigWatcher(runtime, cfg, poll_interval=0.05)
    await watcher.start()
    try:
        # Bump mtime
        await asyncio.sleep(0.1)
        cfg.touch()
        # Wait long enough for the watcher loop to notice
        for _ in range(40):
            await asyncio.sleep(0.05)
            if reload_count > 0:
                break
    finally:
        await watcher.stop()

    assert reload_count >= 1
