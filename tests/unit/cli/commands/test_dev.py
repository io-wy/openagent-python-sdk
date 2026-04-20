"""Tests for ``openagents dev``.

Exercises the wiring without starting a long-running file watcher:

* ``--no-watch`` dispatches exactly one ``Runtime.reload()``.
* Config errors bubble up as exit ``2``.
* The debounce helper collapses multiple rapid fires into one call.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openagents.cli.commands import dev as dev_cmd
from openagents.cli.main import main as cli_main


def _valid_agent(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "agent.json"
    cfg_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "agents": [
                    {
                        "id": "a",
                        "name": "x",
                        "memory": {"impl": "tests.fixtures.runtime_plugins.InjectWritebackMemory"},
                        "pattern": {"type": "react", "config": {"max_steps": 1}},
                        "llm": {"provider": "mock", "model": "m"},
                        "tools": [],
                        "runtime": {
                            "max_steps": 1,
                            "step_timeout_ms": 5000,
                            "session_queue_size": 10,
                            "event_queue_size": 10,
                        },
                    }
                ],
            }
        )
    )
    return cfg_path


def test_dev_no_watch_calls_reload_once(tmp_path, capsys, monkeypatch):
    cfg = _valid_agent(tmp_path)
    calls = {"n": 0}

    def _fake_reload(self):  # type: ignore[no-untyped-def]
        calls["n"] += 1

    from openagents.runtime.runtime import Runtime

    monkeypatch.setattr(Runtime, "reload", _fake_reload)
    code = cli_main(["dev", str(cfg), "--no-watch"])
    assert code == 0
    assert calls["n"] == 1
    err = capsys.readouterr().err
    assert "runtime reloaded" in err


def test_dev_bad_config_returns_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    code = cli_main(["dev", str(bad), "--no-watch"])
    assert code == 2


def test_dev_missing_config_returns_2(tmp_path, capsys):
    code = cli_main(["dev", str(tmp_path / "nope.json"), "--no-watch"])
    assert code == 2


def test_reload_with_log_success(monkeypatch, capsys):

    class _Runtime:
        def reload(self) -> None:
            return None

    import io

    stderr = io.StringIO()
    dev_cmd._reload_with_log(_Runtime(), stderr=stderr)
    assert "runtime reloaded" in stderr.getvalue()


def test_reload_with_log_swallows_config_error(monkeypatch):
    from openagents.errors.exceptions import ConfigLoadError

    class _Runtime:
        def reload(self) -> None:
            raise ConfigLoadError("boom")

    import io

    stderr = io.StringIO()
    dev_cmd._reload_with_log(_Runtime(), stderr=stderr)
    assert "[reload skipped]" in stderr.getvalue()
    assert "boom" in stderr.getvalue()


def test_reload_with_log_swallows_unexpected_error():
    class _Runtime:
        def reload(self) -> None:
            raise RuntimeError("nope")

    import io

    stderr = io.StringIO()
    dev_cmd._reload_with_log(_Runtime(), stderr=stderr)
    assert "[reload failed]" in stderr.getvalue()


def test_debounced_collapses_rapid_fires():
    calls = {"n": 0}

    class _Runtime:
        def reload(self) -> None:
            calls["n"] += 1

    import io

    stderr = io.StringIO()
    schedule = dev_cmd._debounced(_Runtime(), debounce_ms=80, stderr=stderr)
    # Fire 10 times rapidly — should result in exactly one reload.
    for _ in range(10):
        schedule()
        time.sleep(0.005)
    # Wait for the debounce window to elapse.
    time.sleep(0.3)
    assert calls["n"] == 1


def test_debounced_allows_second_reload_after_settle():
    calls = {"n": 0}

    class _Runtime:
        def reload(self) -> None:
            calls["n"] += 1

    import io

    stderr = io.StringIO()
    schedule = dev_cmd._debounced(_Runtime(), debounce_ms=60, stderr=stderr)
    schedule()
    time.sleep(0.2)
    schedule()
    time.sleep(0.2)
    assert calls["n"] == 2


def test_watch_polling_reloads_on_mtime_change(tmp_path, capsys, monkeypatch):
    """Drive ``_watch_polling`` with a file whose mtime changes once.

    Runs synchronously on the main thread — we monkey-patch ``time.sleep``
    inside the polling loop so the second tick bumps mtime and the third
    tick raises ``KeyboardInterrupt`` to terminate the loop. This avoids
    leaking a daemon polling thread that would keep stat()-ing files for
    the remainder of the test session.
    """
    cfg = tmp_path / "watched.json"
    cfg.write_text("{}")
    calls = {"n": 0}

    class _Runtime:
        def reload(self) -> None:
            calls["n"] += 1

    import io

    stderr = io.StringIO()
    state = {"tick": 0}

    def _scripted_sleep(interval: float) -> None:
        state["tick"] += 1
        if state["tick"] == 1:
            # First poll cycle: force a distinct mtime so the next iteration
            # sees a change. Some filesystems (FAT, certain NTFS configs)
            # have 1–2s mtime resolution, so bump the stamp explicitly.
            import os as _os

            stat = cfg.stat()
            cfg.write_text('{"updated": true}')
            _os.utime(cfg, (stat.st_mtime + 2, stat.st_mtime + 2))
            return
        # Second tick onward: loop has observed the change, exit cleanly.
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", _scripted_sleep)
    dev_cmd._watch_polling(cfg, _Runtime(), poll_interval=0.05, stderr=stderr)

    assert calls["n"] >= 1


def test_watch_with_watchdog_debounces_bursts(tmp_path):
    pytest.importorskip("watchdog")
    cfg = tmp_path / "burst.json"
    cfg.write_text("{}")

    calls = {"n": 0}

    class _Runtime:
        def reload(self) -> None:
            calls["n"] += 1

    # We bypass the Observer setup (which would block on a real fs watcher)
    # and directly call the scheduler returned by _debounced under a burst.
    import io

    stderr = io.StringIO()
    schedule = dev_cmd._debounced(_Runtime(), debounce_ms=50, stderr=stderr)
    for _ in range(5):
        schedule()
    time.sleep(0.25)
    assert calls["n"] == 1


def test_dev_no_watch_with_unexpected_close_error(tmp_path, capsys, monkeypatch):
    """Exercise the ``best-effort cleanup`` branch in ``run()``."""
    cfg = _valid_agent(tmp_path)
    from openagents.runtime.runtime import Runtime

    async def _bad_close(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("close-failed")

    monkeypatch.setattr(Runtime, "close", _bad_close)
    code = cli_main(["dev", str(cfg), "--no-watch"])
    # close error is swallowed; dev still reports success.
    assert code == 0
