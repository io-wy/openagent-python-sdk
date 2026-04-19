"""WP3 stress: 200 concurrent emits via file_logging - inner history complete."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openagents.plugins.builtin.events.file_logging import FileLoggingEventBus


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_high_frequency_emit_no_loss(tmp_path: Path):
    log_path = tmp_path / "events.log"
    n = 200
    bus = FileLoggingEventBus(
        config={
            "log_path": str(log_path),
            "inner": {"type": "async", "config": {"max_history": n + 10}},
            "max_history": n + 10,
        }
    )

    async def _emit(i: int):
        await bus.emit("burst", index=i)

    await asyncio.gather(*[_emit(i) for i in range(n)])

    history = await bus.get_history("burst")
    assert len(history) == n

    # File log should also contain n lines (or fewer if any IO retries
    # were dropped silently - this asserts the success path).
    assert log_path.exists()
    line_count = sum(1 for _ in log_path.read_text(encoding="utf-8").splitlines())
    assert line_count == n
