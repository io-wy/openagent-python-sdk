"""WP1: PluginLoadError for unknown plugin types includes a near-match hint."""

from __future__ import annotations

import pytest

from openagents.config.schema import MemoryRef
from openagents.errors.exceptions import PluginLoadError
from openagents.plugins.loader import load_memory_plugin


def test_unknown_memory_type_includes_did_you_mean():
    ref = MemoryRef(type="bufer")
    with pytest.raises(PluginLoadError) as ei:
        load_memory_plugin(ref)
    text = str(ei.value)
    assert "Unknown memory plugin type" in text
    assert "bufer" in text
    assert "buffer" in text
    assert "Did you mean" in text


def test_unknown_memory_type_includes_available_plugins_when_no_close_match():
    ref = MemoryRef(type="completely_off_the_wall_xyzzy")
    with pytest.raises(PluginLoadError) as ei:
        load_memory_plugin(ref)
    text = str(ei.value)
    assert "Available memory plugins" in text
    # Available list should include some known builtin
    assert "buffer" in text


def test_no_type_or_impl_raises_with_format_hint():
    ref = MemoryRef()  # nothing set
    with pytest.raises(PluginLoadError) as ei:
        load_memory_plugin(ref)
    text = str(ei.value)
    assert "type" in text
    assert "impl" in text
    assert "hint:" in text


def test_hint_attribute_is_set_on_unknown_type():
    ref = MemoryRef(type="bufer")
    with pytest.raises(PluginLoadError) as ei:
        load_memory_plugin(ref)
    assert ei.value.hint is not None
    assert "buffer" in ei.value.hint
