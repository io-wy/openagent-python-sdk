from __future__ import annotations

import pytest

from openagents.utils.hotreload import HotReloadServer


class _FakeWeb:
    @staticmethod
    def json_response(payload):
        return payload


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _DummyRuntime:
    def __init__(self):
        self.reloaded = False

    async def run(self, *, agent_id: str, session_id: str, input_text: str):
        return f"run:{agent_id}:{session_id}:{input_text}"

    async def reload(self):
        self.reloaded = True

    async def list_agents(self):
        return [{"id": "assistant", "name": "Assistant"}]


@pytest.mark.asyncio
async def test_hotreload_server_handlers_use_stored_web_module(tmp_path):
    server = HotReloadServer(_DummyRuntime(), tmp_path / "agent.json")
    server._web_module = _FakeWeb

    run_response = await server._handle_run(
        _FakeRequest({"agent_id": "assistant", "session_id": "s1", "input": "hello"})
    )
    reload_response = await server._handle_reload(None)
    agents_response = await server._handle_list_agents(None)

    assert run_response == {"result": "run:assistant:s1:hello"}
    assert reload_response == {"status": "reloaded"}
    assert agents_response == {"agents": [{"id": "assistant", "name": "Assistant"}]}
    assert server.runtime.reloaded is True
