"""Internal loguru helpers.

All ``loguru`` imports live here behind import-time guards. Public callers
(``observability.configure()``) use ``install_sinks``/``remove_installed_sinks``
and receive ``LoguruNotInstalledError`` if loguru is missing.

Library etiquette: we install N sinks on the global ``loguru.logger`` but
(a) every sink filter requires ``record['extra']['_openagents'] is True``
so user-installed sinks never see our records and vice versa, and
(b) ``remove_installed_sinks()`` only removes the IDs we recorded — we
never call the no-arg ``logger.remove()``, which would clear everything.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from openagents.observability.config import LoguruSinkConfig
from openagents.observability.errors import LoguruNotInstalledError
from openagents.observability.filters import _LOGRECORD_STD_ATTRS

_INSTALLED_SINK_IDS: list[int] = []


def _require_loguru() -> Any:
    """Return ``loguru.logger`` or raise ``LoguruNotInstalledError`` with pip hint."""
    try:
        from loguru import logger
    except ImportError as exc:
        raise LoguruNotInstalledError() from exc
    return logger


def _sink_filter(cfg_filter_include: list[str] | None) -> Callable[[dict], bool]:
    """Build a per-sink filter: accept only records this library tagged.

    The optional ``cfg_filter_include`` applies a second-pass logger-name
    prefix test so callers can further narrow a sink to e.g. LLM-related
    records only.
    """

    def _f(record: dict) -> bool:
        extra = record["extra"]
        if extra.get("_openagents") is not True:
            return False
        if cfg_filter_include is None:
            return True
        name = extra.get("_oa_name", "")
        return any(name == p or name.startswith(p + ".") for p in cfg_filter_include)

    return _f


def install_sinks(sinks: list[LoguruSinkConfig]) -> None:
    """Install one loguru sink per config entry. Record their IDs.

    Atomicity: if any sink's ``logger.add()`` raises, all sinks
    successfully added within this call are removed before the exception
    propagates. Pre-existing sinks from prior ``install_sinks`` calls are
    untouched.
    """
    logger = _require_loguru()
    batch: list[int] = []
    try:
        for cfg in sinks:
            if cfg.target == "stderr":
                target: Any = sys.stderr
            elif cfg.target == "stdout":
                target = sys.stdout
            else:
                target = cfg.target  # string path → loguru auto-creates file sink
            kwargs: dict[str, Any] = {
                "level": cfg.level,
                "filter": _sink_filter(cfg.filter_include),
                "enqueue": cfg.enqueue,
                "serialize": cfg.serialize,
            }
            if cfg.format is not None:
                kwargs["format"] = cfg.format
            if cfg.colorize is not None:
                kwargs["colorize"] = cfg.colorize
            if cfg.rotation is not None:
                kwargs["rotation"] = cfg.rotation
            if cfg.retention is not None:
                kwargs["retention"] = cfg.retention
            if cfg.compression is not None:
                kwargs["compression"] = cfg.compression
            sink_id = logger.add(target, **kwargs)
            batch.append(sink_id)
        _INSTALLED_SINK_IDS.extend(batch)
    except Exception:
        for sid in batch:
            try:
                logger.remove(sid)
            except ValueError:
                pass
        raise


def remove_installed_sinks() -> None:
    """Remove only the sinks we installed.

    Never calls the no-arg ``logger.remove()`` — that would wipe sinks the
    user's application registered directly.
    """
    try:
        from loguru import logger
    except ImportError:
        _INSTALLED_SINK_IDS.clear()
        return
    for sid in _INSTALLED_SINK_IDS:
        try:
            logger.remove(sid)
        except ValueError:
            pass  # already removed externally; ignore
    _INSTALLED_SINK_IDS.clear()


class _LoguruInterceptHandler(logging.Handler):
    """Forwards stdlib ``LogRecord`` to ``loguru.logger``.

    Follows loguru's official InterceptHandler pattern:
    - level name → loguru level, with fallback to the numeric level for
      user-defined levels that loguru doesn't know by name
    - dynamic frame-walk depth so ``{name}:{function}:{line}`` points at
      the business caller, not this handler
    - non-standard LogRecord attributes are harvested as extras and
      forwarded via ``bind(**extras)``, so data shaped by ``RedactFilter``
      (and similar filters) reaches ``serialize=True`` JSON sinks
    """

    def __init__(self) -> None:
        super().__init__()
        self._openagents_installed = True  # align with reset_logging cleanup tag
        self._logger = _require_loguru()  # fail loud at handler construction

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 1. Level name → loguru level, with numeric fallback
            try:
                level: Any = self._logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            # 2. Dynamic depth: walk up from this emit() frame until we're out
            # of the stdlib logging module. Uses the canonical loguru
            # InterceptHandler pattern from the official README, which works
            # regardless of how many intermediate handle/callHandlers frames
            # are present.
            import inspect

            frame = inspect.currentframe()
            depth = 0
            while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
                frame = frame.f_back
                depth += 1

            # 3. Harvest non-standard extras
            extras: dict[str, Any] = {}
            for key, value in record.__dict__.items():
                if key.startswith("_") or key in _LOGRECORD_STD_ATTRS:
                    continue
                extras[key] = value
            extras["_openagents"] = True
            extras["_oa_name"] = record.name

            self._logger.bind(**extras).opt(
                depth=depth,
                exception=record.exc_info,
            ).log(level, record.getMessage())
        except Exception:
            self.handleError(record)
