"""Tests for TypedConfigPluginMixin."""

from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel, Field

from openagents.interfaces.typed_config import TypedConfigPluginMixin


class _PluginBase:
    """Simulates a plugin ABC accepting (config=..., capabilities=...)."""

    def __init__(self, *, config: dict | None = None, capabilities: set | None = None):
        self.config = config or {}
        self.capabilities = capabilities or set()


class _SamplePlugin(TypedConfigPluginMixin, _PluginBase):
    class Config(BaseModel):
        name: str = "default"
        retries: int = 3
        items: list[str] = Field(default_factory=list)

    def __init__(self, config: dict | None = None):
        super().__init__(config=config or {}, capabilities=set())
        self._init_typed_config()


def test_known_fields_populate_cfg():
    plugin = _SamplePlugin(config={"name": "alice", "retries": 5})
    assert plugin.cfg.name == "alice"
    assert plugin.cfg.retries == 5
    assert plugin.cfg.items == []


def test_defaults_applied_when_missing():
    plugin = _SamplePlugin(config={})
    assert plugin.cfg.name == "default"
    assert plugin.cfg.retries == 3
    assert plugin.cfg.items == []


def test_none_config_uses_defaults():
    plugin = _SamplePlugin(config=None)
    assert plugin.cfg.name == "default"


def test_unknown_keys_warn_but_dont_raise(caplog):
    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        plugin = _SamplePlugin(config={"name": "bob", "unknown_key": 1, "other": "x"})
    # Plugin still constructs and known keys are populated
    assert plugin.cfg.name == "bob"
    # Warning emitted
    assert any(
        "unknown config keys" in record.message
        and "_SamplePlugin" in record.message
        and "unknown_key" in record.message
        and "other" in record.message
        for record in caplog.records
    )


def test_known_only_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        _SamplePlugin(config={"name": "alice"})
    assert not any("unknown config keys" in r.message for r in caplog.records)


def test_multiple_instances_are_independent():
    p1 = _SamplePlugin(config={"name": "a"})
    p2 = _SamplePlugin(config={"name": "b", "retries": 7})
    assert p1.cfg.name == "a"
    assert p2.cfg.name == "b"
    assert p1.cfg.retries == 3
    assert p2.cfg.retries == 7


def test_unknown_keys_sorted_in_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="openagents.interfaces.typed_config"):
        _SamplePlugin(config={"zebra": 1, "alpha": 2, "name": "x"})
    record = next(r for r in caplog.records if "unknown config keys" in r.message)
    # The list should be sorted (alpha before zebra)
    msg = record.message
    assert msg.index("alpha") < msg.index("zebra")


def test_validation_error_propagates():
    class _StrictPlugin(TypedConfigPluginMixin, _PluginBase):
        class Config(BaseModel):
            count: int

        def __init__(self, config: dict | None = None):
            super().__init__(config=config or {}, capabilities=set())
            self._init_typed_config()

    # Missing required field raises pydantic ValidationError
    with pytest.raises(Exception):  # pydantic.ValidationError
        _StrictPlugin(config={})
