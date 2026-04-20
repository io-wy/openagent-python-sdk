from __future__ import annotations

import openagents
from openagents.interfaces.runtime import RunRequest, RunResult
from openagents.runtime import sync as sync_module
from openagents.runtime.runtime import Runtime


class _FakeRuntime:
    def __init__(self, marker: str):
        self.marker = marker
        self.run_sync_calls: list[tuple[str, str, str, object | None]] = []
        self.run_detailed_calls: list[RunRequest] = []

    def run_sync(self, *, agent_id: str, session_id: str, input_text: str, deps=None):
        self.run_sync_calls.append((agent_id, session_id, input_text, deps))
        return f"{self.marker}:{agent_id}:{session_id}:{input_text}"

    async def run_detailed(self, *, request: RunRequest) -> RunResult:
        self.run_detailed_calls.append(request)
        return RunResult(run_id=request.run_id, final_output=f"{self.marker}:{request.input_text}")


def test_run_agent_path_variants_delegate_to_runtime(monkeypatch):
    fake = _FakeRuntime("path")
    monkeypatch.setattr(sync_module.Runtime, "from_config", classmethod(lambda cls, path: fake))
    deps = {"token": "abc"}

    result = sync_module.run_agent(
        "agent.json",
        agent_id="assistant",
        session_id="sync-path",
        input_text="hello",
        deps=deps,
    )
    detailed = sync_module.run_agent_detailed(
        "agent.json",
        agent_id="assistant",
        session_id="sync-path-detailed",
        input_text="hello",
        deps=deps,
    )

    assert result == "path:assistant:sync-path:hello"
    assert isinstance(detailed, RunResult)
    assert detailed.final_output == "path:hello"
    assert len(fake.run_sync_calls) == 1
    assert len(fake.run_detailed_calls) == 1
    assert fake.run_sync_calls[0][3] is deps
    assert fake.run_detailed_calls[0].deps is deps


def test_run_agent_config_variants_delegate_to_runtime(monkeypatch):
    fake_config = object()
    fake = _FakeRuntime("config")

    def _runtime_factory(config):
        assert config is fake_config
        return fake

    monkeypatch.setattr(sync_module, "Runtime", _runtime_factory)

    result = sync_module.run_agent_with_config(
        fake_config,
        agent_id="assistant",
        session_id="sync-config",
        input_text="hello",
        deps={"token": "cfg"},
    )
    detailed = sync_module.run_agent_detailed_with_config(
        fake_config,
        agent_id="assistant",
        session_id="sync-detailed",
        input_text="hello",
        deps={"token": "cfg"},
    )

    assert result == "config:assistant:sync-config:hello"
    assert isinstance(detailed, RunResult)
    assert detailed.final_output == "config:hello"


def test_run_agent_with_dict_uses_runtime_from_dict(monkeypatch):
    fake = _FakeRuntime("dict")
    payload = {"version": "1.0", "agents": []}
    seen: dict[str, object] = {}

    def _from_dict(cls, incoming):
        seen["payload"] = incoming
        return fake

    monkeypatch.setattr(
        sync_module.Runtime,
        "from_dict",
        classmethod(_from_dict),
    )

    result = sync_module.run_agent_with_dict(
        payload,
        agent_id="assistant",
        session_id="sync-dict",
        input_text="hello",
        deps={"token": "dict"},
    )

    assert seen["payload"] is payload
    assert result == "dict:assistant:sync-dict:hello"


def test_runtime_from_dict_builds_runtime_from_python_payload():
    runtime = Runtime.from_dict(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "assistant",
                    "name": "Assistant",
                    "memory": {"type": "buffer"},
                    "pattern": {"type": "react"},
                    "tools": [],
                }
            ],
        }
    )

    assert isinstance(runtime, Runtime)
    assert runtime._config.agents[0].id == "assistant"
    assert runtime.session_manager is not None
    assert runtime.event_bus is not None


def test_openagents_exports_sync_runtime_helpers():
    assert callable(openagents.run_agent_detailed)
    assert callable(openagents.run_agent_detailed_with_config)
    assert callable(openagents.run_agent_with_dict)
