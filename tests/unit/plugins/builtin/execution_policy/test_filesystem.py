from __future__ import annotations

from types import SimpleNamespace

import pytest

from openagents.interfaces.capabilities import MEMORY_INJECT, MEMORY_RETRIEVE, MEMORY_WRITEBACK
from openagents.interfaces.tool import ToolExecutionRequest, ToolExecutionSpec
from openagents.plugins.builtin.execution_policy.filesystem import (
    FilesystemExecutionPolicy,
    _extract_paths,
    _is_within,
    _normalize_roots,
)
from openagents.plugins.builtin.memory.chain import ChainMemory


class _Memory:
    def __init__(self, name: str, capabilities: set[str]) -> None:
        self.name = name
        self.capabilities = capabilities
        self.calls: list[str] = []

    async def inject(self, context):
        self.calls.append(f"inject:{self.name}")
        context.memory_view.setdefault("seen", []).append(self.name)

    async def writeback(self, context):
        self.calls.append(f"writeback:{self.name}")

    async def retrieve(self, query, context):
        self.calls.append(f"retrieve:{self.name}:{query}")
        return [{"memory": self.name, "query": query}]

    async def close(self):
        self.calls.append(f"close:{self.name}")


@pytest.mark.asyncio
async def test_filesystem_execution_policy_helpers_and_decisions(tmp_path):
    allowed = tmp_path / "allowed"
    blocked = tmp_path / "blocked"
    allowed.mkdir()
    blocked.mkdir()
    allowed_file = allowed / "ok.txt"
    blocked_file = blocked / "no.txt"
    allowed_file.write_text("ok", encoding="utf-8")
    blocked_file.write_text("no", encoding="utf-8")

    roots = _normalize_roots([str(allowed), "", None])  # type: ignore[list-item]
    assert roots == [allowed.resolve(strict=False)]
    assert _extract_paths({"path": str(allowed_file), "cwd": str(allowed)})[0] == allowed_file.resolve(strict=False)
    assert _is_within(allowed_file.resolve(strict=False), roots) is True
    assert _is_within(blocked_file.resolve(strict=False), roots) is False

    policy = FilesystemExecutionPolicy(
        {
            "read_roots": [str(allowed)],
            "write_roots": [str(allowed)],
            "allow_tools": ["read_file", "write_file"],
            "deny_tools": ["delete_file"],
        }
    )

    denied = await policy.evaluate_policy(ToolExecutionRequest(tool_id="delete_file", tool=object(), params={}))
    not_allowed = await policy.evaluate_policy(ToolExecutionRequest(tool_id="grep_files", tool=object(), params={}))
    no_paths = await policy.evaluate_policy(ToolExecutionRequest(tool_id="read_file", tool=object(), params={}))
    read_ok = await policy.evaluate_policy(
        ToolExecutionRequest(
            tool_id="read_file",
            tool=object(),
            params={"path": str(allowed_file)},
            execution_spec=ToolExecutionSpec(reads_files=True),
        )
    )
    read_blocked = await policy.evaluate_policy(
        ToolExecutionRequest(
            tool_id="read_file",
            tool=object(),
            params={"path": str(blocked_file)},
            execution_spec=ToolExecutionSpec(reads_files=True),
        )
    )
    write_blocked = await policy.evaluate_policy(
        ToolExecutionRequest(
            tool_id="write_file",
            tool=object(),
            params={"path": str(blocked_file)},
            execution_spec=ToolExecutionSpec(writes_files=True),
        )
    )

    assert denied.allowed is False and "denied" in denied.reason
    assert not_allowed.allowed is False and "allow_tools" in not_allowed.reason
    assert no_paths.allowed is True and no_paths.metadata == {"policy": "filesystem"}
    assert read_ok.allowed is True
    assert read_blocked.allowed is False and "outside read_roots" in read_blocked.reason
    assert write_blocked.allowed is False and "outside write_roots" in write_blocked.reason

    write_only_policy = FilesystemExecutionPolicy({"write_roots": [str(allowed)]})
    inferred_write_denied = await write_only_policy.evaluate_policy(
        ToolExecutionRequest(tool_id="write_file", tool=object(), params={"path": str(blocked_file)})
    )
    assert inferred_write_denied.allowed is False
    assert "outside write_roots" in inferred_write_denied.reason


@pytest.mark.asyncio
async def test_chain_memory_loads_memories_and_runs_in_expected_order(monkeypatch):
    first = _Memory("first", {MEMORY_INJECT, MEMORY_RETRIEVE})
    second = _Memory("second", {MEMORY_WRITEBACK})
    loaded = [first, second]

    def _fake_load_plugin(kind, ref):
        _ = (kind, ref)
        return loaded.pop(0)

    monkeypatch.setattr("openagents.plugins.loader.load_plugin", _fake_load_plugin)

    memory = ChainMemory(
        {
            "memories": [
                {"type": "buffer"},
                {"type": "window_buffer"},
            ]
        }
    )
    context = SimpleNamespace(memory_view={})

    await memory.inject(context)
    results = await memory.retrieve("hello", context)
    await memory.writeback(context)
    await memory.close()

    assert context.memory_view["seen"] == ["first"]
    assert results == [{"memory": "first", "query": "hello"}]
    assert first.calls == [
        "inject:first",
        "retrieve:first:hello",
        "close:first",
    ]
    assert second.calls == [
        "writeback:second",
        "close:second",
    ]


def test_chain_memory_requires_memories_config():
    with pytest.raises(ValueError, match="requires 'memories' config list"):
        ChainMemory({"memories": []})


def test_filesystem_execution_policy_ignores_unknown_config_keys():
    """Pydantic's default model_validate silently ignores extra keys."""
    policy = FilesystemExecutionPolicy({"deny_tools": ["delete_file"], "totally_unknown": 1})
    assert policy._deny_tools == {"delete_file"}
