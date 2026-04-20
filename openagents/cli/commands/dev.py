"""``openagents dev`` — hot-reload wrapper around ``Runtime.reload()``.

Watches the config file for changes and calls
:meth:`openagents.runtime.runtime.Runtime.reload` on each burst of file
events. Uses :mod:`watchdog` when available; degrades cleanly to a
polling loop (``--poll-interval SECONDS``, default ``1.0``) otherwise.

Invariants preserved from ``CLAUDE.md``:

* ``Runtime.reload()`` re-parses config and invalidates LLM clients for
  changed agents, but does NOT hot-swap top-level ``runtime`` / ``session``
  / ``events`` plugins. ``dev`` therefore does not attempt to do so
  either — a change that would require top-level swap still needs a
  full process restart.

``--no-watch`` performs exactly one ``reload()`` and exits, which is the
mode tests use to exercise the debounce + reload wiring without a file
watcher.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openagents.cli._exit import EXIT_OK, EXIT_VALIDATION
from openagents.cli._fallback import require_or_hint
from openagents.config.loader import load_config
from openagents.errors.exceptions import ConfigError
from openagents.runtime.runtime import Runtime

_DEBOUNCE_MS = 150


def _reload_with_log(runtime: Runtime, *, stderr=None) -> None:
    """Call ``Runtime.reload()`` and log success / failure to stderr.

    ``Runtime.reload`` is ``async`` on the real class but tests inject
    non-coroutine stubs; tolerate both. *stderr* is resolved at call time
    when omitted so that pytest's ``capsys`` (which rewraps
    ``sys.stderr`` per test) still sees the log line.
    """
    if stderr is None:
        stderr = sys.stderr
    try:
        result = runtime.reload()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except ConfigError as exc:
        stderr.write(f"[reload skipped] {type(exc).__name__}: {exc}\n")
        return
    except Exception as exc:  # pragma: no cover - defensive
        stderr.write(f"[reload failed] {type(exc).__name__}: {exc}\n")
        return
    stderr.write("[reload] runtime reloaded\n")


def _debounced(runtime: Runtime, *, debounce_ms: int, stderr=None) -> callable:  # type: ignore[valid-type]
    """Return a function that collapses multiple fires within *debounce_ms*.

    Uses a single :class:`threading.Timer` whose deadline is reset on
    every call, so a burst of filesystem events (save/replace/truncate)
    results in exactly one :func:`_reload_with_log` call once the burst
    settles.
    """
    lock = threading.Lock()
    state: dict[str, Any] = {"timer": None}

    def _fire() -> None:
        _reload_with_log(runtime, stderr=stderr)

    def _schedule() -> None:
        with lock:
            existing = state["timer"]
            if existing is not None:
                existing.cancel()
            t = threading.Timer(debounce_ms / 1000.0, _fire)
            t.daemon = True
            state["timer"] = t
            t.start()

    return _schedule


def _watch_with_watchdog(path: Path, runtime: Runtime, *, debounce_ms: int, stderr=None) -> None:
    if stderr is None:
        stderr = sys.stderr
    from watchdog.events import FileSystemEventHandler  # type: ignore
    from watchdog.observers import Observer  # type: ignore

    schedule = _debounced(runtime, debounce_ms=debounce_ms, stderr=stderr)

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: D401 - watchdog callback
            target_path = Path(getattr(event, "src_path", "") or "")
            if target_path.resolve() == path.resolve():
                schedule()

    observer = Observer()
    observer.schedule(_Handler(), str(path.parent), recursive=False)
    observer.start()
    stderr.write(f"[watch] watching {path} (press Ctrl+C to exit)\n")
    stop_event = threading.Event()

    def _sigint(_sig, _frame) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _sigint)
    except ValueError:  # pragma: no cover - not on main thread
        pass
    try:
        while not stop_event.is_set():
            time.sleep(0.1)
    finally:
        observer.stop()
        observer.join(timeout=2.0)


def _watch_polling(
    path: Path,
    runtime: Runtime,
    *,
    poll_interval: float,
    stderr=None,
) -> None:
    if stderr is None:
        stderr = sys.stderr
    stderr.write(f"[watch] watchdog not installed; polling {path} every {poll_interval}s (Ctrl+C to exit)\n")
    last_mtime: float | None = None
    try:
        last_mtime = path.stat().st_mtime
    except OSError:
        pass
    try:
        while True:
            time.sleep(poll_interval)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if last_mtime is None or mtime != last_mtime:
                last_mtime = mtime
                _reload_with_log(runtime, stderr=stderr)
    except KeyboardInterrupt:
        return


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "dev",
        help="hot-reload runtime on config change",
        description="Watch a config file and call Runtime.reload() on each change.",
    )
    p.add_argument("path", help="path to an agent.json")
    p.add_argument(
        "--poll-interval",
        dest="poll_interval",
        type=float,
        default=1.0,
        help="polling interval in seconds when watchdog isn't available (default: 1.0)",
    )
    p.add_argument(
        "--no-watch",
        action="store_true",
        help="call Runtime.reload() once and exit (for tests / one-shot smoke)",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        load_config(path)
    except ConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    try:
        runtime = Runtime.from_config(path)
    except ConfigError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    try:
        if args.no_watch:
            _reload_with_log(runtime)
            return EXIT_OK

        watchdog = importlib.util.find_spec("watchdog")
        if watchdog is not None and require_or_hint("watchdog") is not None:
            _watch_with_watchdog(path, runtime, debounce_ms=_DEBOUNCE_MS)
        else:
            _watch_polling(path, runtime, poll_interval=args.poll_interval)
        return EXIT_OK
    finally:
        try:
            asyncio.run(runtime.close())
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
