"""Tests for the public openagents.plugins.loader.load_plugin API."""

from __future__ import annotations

import warnings

import pytest

from openagents.config.schema import MemoryRef
from openagents.errors.exceptions import PluginLoadError
from openagents.plugins.loader import _load_plugin, load_plugin


def test_load_plugin_returns_builtin_memory_instance():
    plugin = load_plugin("memory", MemoryRef(type="buffer"))
    assert type(plugin).__name__ == "BufferMemory"


def test_load_plugin_with_required_methods_succeeds():
    plugin = load_plugin(
        "memory",
        MemoryRef(type="buffer"),
        required_methods=("inject", "writeback"),
    )
    assert callable(plugin.inject)
    assert callable(plugin.writeback)


def test_load_plugin_unknown_type_raises_plugin_load_error():
    with pytest.raises(PluginLoadError, match="Unknown memory plugin"):
        load_plugin("memory", MemoryRef(type="this_does_not_exist"))


def test_deprecated_alias_emits_deprecation_warning_and_returns_same_instance():
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        plugin = _load_plugin("memory", MemoryRef(type="buffer"))

    assert type(plugin).__name__ == "BufferMemory"
    deprecations = [w for w in recorded if issubclass(w.category, DeprecationWarning)]
    assert any("_load_plugin is deprecated" in str(w.message) for w in deprecations), (
        f"Expected DeprecationWarning, got: {[str(w.message) for w in recorded]}"
    )


def test_deprecated_alias_and_public_api_produce_equivalent_classes():
    public = load_plugin("memory", MemoryRef(type="buffer"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        deprecated = _load_plugin("memory", MemoryRef(type="buffer"))
    assert type(public) is type(deprecated)


def test_load_plugin_passes_required_methods_through():
    # Required-methods check fires for class-based plugins; verify a
    # missing-method case raises CapabilityError equivalently.
    from openagents.errors.exceptions import CapabilityError

    with pytest.raises(CapabilityError, match="must implement"):
        load_plugin(
            "memory",
            MemoryRef(type="buffer"),
            required_methods=("totally_made_up_method",),
        )
