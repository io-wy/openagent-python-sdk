"""WP1: PluginLoadError for malformed impl path includes format guidance."""

from __future__ import annotations

import pytest

from openagents.config.schema import MemoryRef
from openagents.errors.exceptions import PluginLoadError
from openagents.plugins.loader import load_memory_plugin


def test_invalid_impl_path_no_dot_returns_format_hint():
    # impl without a dot has no module separator
    ref = MemoryRef(impl="bareword")
    with pytest.raises(PluginLoadError) as ei:
        load_memory_plugin(ref)
    text = str(ei.value)
    assert "Invalid impl path" in text
    assert "module.path" in text
    assert ei.value.hint is not None


def test_missing_symbol_in_module_returns_helpful_hint():
    # Real module, fake symbol
    ref = MemoryRef(impl="openagents.plugins.builtin.memory.buffer.NoSuchSymbol")
    with pytest.raises(PluginLoadError) as ei:
        load_memory_plugin(ref)
    text = str(ei.value)
    assert "no symbol" in text
    assert "NoSuchSymbol" in text
    assert "spelling" in (ei.value.hint or "")
