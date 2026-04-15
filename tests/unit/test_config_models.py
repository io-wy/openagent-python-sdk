from __future__ import annotations

from openagents.config.schema import AppConfig, MemoryRef


def test_app_config_model_validate_parses_minimal_payload():
    config = AppConfig.model_validate(
        {
            "version": "1.0",
            "agents": [
                {
                    "id": "assistant",
                    "name": "demo",
                    "memory": {"type": "window_buffer"},
                    "pattern": {"type": "react"},
                    "llm": {"provider": "mock"},
                    "tools": [],
                }
            ],
        }
    )

    assert config.agents[0].memory == MemoryRef(type="window_buffer")


def test_memory_ref_model_validate_defaults_on_error_literal():
    memory = MemoryRef.model_validate({"type": "window_buffer"})

    assert memory.on_error == "continue"
