from __future__ import annotations

import pytest

from openagents.config.schema import AppConfig, DiagnosticsRef
from openagents.errors.exceptions import PluginLoadError
from openagents.plugins.builtin.diagnostics.null_plugin import NullDiagnosticsPlugin
from openagents.plugins.loader import load_diagnostics_plugin


def _minimal_config_dict(**overrides):
    base = {
        "agents": [
            {
                "id": "a1",
                "name": "Agent",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react"},
            }
        ]
    }
    base.update(overrides)
    return base


def test_app_config_diagnostics_defaults_to_none():
    cfg = AppConfig(**_minimal_config_dict())
    assert cfg.diagnostics is None


def test_app_config_diagnostics_null_type():
    cfg = AppConfig(**_minimal_config_dict(diagnostics={"type": "null"}))
    assert cfg.diagnostics is not None
    assert cfg.diagnostics.type == "null"


def test_app_config_diagnostics_ref_redact_keys_default():
    ref = DiagnosticsRef(type="null")
    assert "api_key" in ref.redact_keys
    assert ref.error_snapshot_last_n == 10


def test_load_diagnostics_plugin_none_returns_null():
    plugin = load_diagnostics_plugin(None)
    assert isinstance(plugin, NullDiagnosticsPlugin)


def test_load_diagnostics_plugin_null_type():
    ref = DiagnosticsRef(type="null")
    plugin = load_diagnostics_plugin(ref)
    assert isinstance(plugin, NullDiagnosticsPlugin)


def test_load_diagnostics_plugin_unknown_type_raises():
    ref = DiagnosticsRef(type="nonexistent")
    with pytest.raises(PluginLoadError):
        load_diagnostics_plugin(ref)


def test_load_runtime_components_injects_diagnostics():
    from openagents.config.schema import EventBusRef, RuntimeRef, SessionRef, SkillsRef
    from openagents.plugins.loader import load_runtime_components

    components = load_runtime_components(
        runtime_ref=RuntimeRef(type="default"),
        session_ref=SessionRef(type="in_memory"),
        events_ref=EventBusRef(type="async"),
        skills_ref=SkillsRef(type="local"),
        diagnostics_ref=DiagnosticsRef(type="null"),
    )
    assert isinstance(components.diagnostics, NullDiagnosticsPlugin)


def test_load_runtime_components_default_diagnostics_when_none():
    from openagents.config.schema import EventBusRef, RuntimeRef, SessionRef, SkillsRef
    from openagents.plugins.loader import load_runtime_components

    components = load_runtime_components(
        runtime_ref=RuntimeRef(type="default"),
        session_ref=SessionRef(type="in_memory"),
        events_ref=EventBusRef(type="async"),
        skills_ref=SkillsRef(type="local"),
    )
    assert isinstance(components.diagnostics, NullDiagnosticsPlugin)
