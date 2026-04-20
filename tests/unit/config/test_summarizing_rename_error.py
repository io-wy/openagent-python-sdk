"""Task 27: legacy 'summarizing' context_assembler raises targeted migration error."""

from __future__ import annotations

import pytest

from openagents.config.loader import load_config_dict
from openagents.errors.exceptions import PluginLoadError
from openagents.plugins.loader import load_agent_plugins


def _minimal_payload(context_type: str) -> dict:
    return {
        "version": "1.0",
        "agents": [
            {
                "id": "a",
                "name": "x",
                "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                "pattern": {"type": "react"},
                "llm": {"provider": "mock", "model": "m"},
                "tools": [],
                "context_assembler": {"type": context_type},
                "runtime": {
                    "max_steps": 8,
                    "step_timeout_ms": 1000,
                    "session_queue_size": 10,
                    "event_queue_size": 10,
                },
            }
        ],
    }


def test_legacy_summarizing_context_assembler_rejected_with_migration_guidance():
    cfg = load_config_dict(_minimal_payload("summarizing"))
    with pytest.raises(PluginLoadError) as excinfo:
        load_agent_plugins(cfg.agents[0])
    msg = str(excinfo.value)
    assert "renamed to 'truncating'" in msg
    assert "0.3.0" in msg


def test_new_truncating_name_works():
    cfg = load_config_dict(_minimal_payload("truncating"))
    plugins = load_agent_plugins(cfg.agents[0])
    assert type(plugins.context_assembler).__name__ == "TruncatingContextAssembler"
