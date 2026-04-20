"""WP3 stress: PermissionError during write propagates; subsequent appends recover."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from openagents.interfaces.session import _TRANSCRIPT_KEY
from openagents.plugins.builtin.session.jsonl_file import JsonlFileSessionManager


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_io_failure_propagates_then_recovers(tmp_path: Path, monkeypatch):
    sid = "io-sess"
    mgr = JsonlFileSessionManager(config={"root_dir": str(tmp_path)})

    real_open = builtins.open
    fail_once = {"done": False}

    def _flaky_open(*args, **kwargs):
        # Only intercept append-mode opens against our session file; the
        # ensure_loaded read path uses "r" and must remain functional.
        mode = kwargs.get("mode") or (args[1] if len(args) > 1 else "r")
        if "a" in mode and not fail_once["done"]:
            fail_once["done"] = True
            raise PermissionError("simulated lock")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", _flaky_open)

    with pytest.raises(PermissionError):
        await mgr.append_message(sid, {"role": "user", "content": "first"})

    # Subsequent append must succeed (open is no longer flaky).
    await mgr.append_message(sid, {"role": "user", "content": "second"})

    # Reload from disk via fresh manager — only the second message should be
    # persisted on disk (the first never wrote a JSONL line).
    mgr2 = JsonlFileSessionManager(config={"root_dir": str(tmp_path)})
    state = await mgr2.get_state(sid)
    transcript = state.get(_TRANSCRIPT_KEY, [])
    contents = [entry["content"] for entry in transcript]
    assert "second" in contents
    # In-memory state of mgr1 may have an inconsistent transcript (we caught the
    # failure after the in-memory list was updated); the on-disk file is the
    # source of truth for this assertion. The first message must not be on
    # disk because the file write was the operation that failed.
    assert "first" not in contents
