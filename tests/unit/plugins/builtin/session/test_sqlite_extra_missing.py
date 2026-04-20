"""When the 'sqlite' extra is not installed, construction must surface a hint."""

from __future__ import annotations

import pytest

from openagents.errors.exceptions import PluginLoadError
from openagents.plugins.builtin.session import sqlite_backed


def test_missing_aiosqlite_raises_with_install_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(sqlite_backed, "_HAS_AIOSQLITE", False)
    with pytest.raises(PluginLoadError) as excinfo:
        sqlite_backed.SqliteSessionManager(config={"db_path": str(tmp_path / "agent.db")})
    err = excinfo.value
    assert "aiosqlite" in str(err)
    assert err.hint is not None
    assert "sqlite" in err.hint.lower()
