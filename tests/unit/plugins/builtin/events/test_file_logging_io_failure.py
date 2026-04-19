"""WP3 stress: file_logging IO failure doesn't break inner bus delivery."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_file_write_failure_does_not_break_inner(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "events.log"
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log_path),
            "inner": {"type": "async"},
        }
    )

    real_open = builtins.open

    def _failing_open(*args, **kwargs):
        # Fail every append-mode open against our log file
        if args and str(args[0]) == str(log_path):
            mode = kwargs.get("mode") or (args[1] if len(args) > 1 else "r")
            if "a" in mode:
                raise OSError("disk gone")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", _failing_open)

    received = []

    bus.subscribe("ping", lambda evt: received.append(evt.name))

    for i in range(5):
        await bus.emit("ping", value=i)

    # Inner bus delivered all events to subscribers
    assert received == ["ping"] * 5
    # Inner history is intact
    history = await bus.get_history("ping")
    assert len(history) == 5
