"""Public logging configuration for the 'openagents.*' namespace."""

from __future__ import annotations

import logging

from openagents.observability.config import (
    LoggingConfig,
    load_from_env,
    merge_env_overrides,
)
from openagents.observability.filters import (
    LevelOverrideFilter,
    PrefixFilter,
    RedactFilter,
)

_LOGGER_ROOT = "openagents"
_OBS_LOGGER = logging.getLogger("openagents.observability.logging")
_OVERRIDDEN_LOGGERS: set[str] = set()


def configure(config: LoggingConfig | None = None) -> None:
    """Install handlers/filters on the 'openagents' logger tree.

    Idempotent. Safe to call from Runtime.reload(). Never touches the
    root logger or any logger outside the 'openagents.*' namespace.

    Raises RichNotInstalledError when config.pretty=True but rich is missing.
    """
    if config is None:
        config = load_from_env() or LoggingConfig()
    config = merge_env_overrides(config)

    _warn_on_foreign_loggers(config)
    reset_logging()

    root = logging.getLogger(_LOGGER_ROOT)
    root.setLevel(_name_to_level(config.level))
    # Prevent double-emission when an application also configures root handlers.
    root.propagate = False

    # Per-logger level overrides must be applied on the named loggers themselves;
    # without this Python's logger gate drops records below the root level before
    # they ever reach the handler-side filters.
    for name, level_name in config.per_logger_levels.items():
        if name != _LOGGER_ROOT and not name.startswith(_LOGGER_ROOT + "."):
            continue
        logging.getLogger(name).setLevel(_name_to_level(level_name))
        _OVERRIDDEN_LOGGERS.add(name)

    handler = _build_handler(config)
    handler.addFilter(PrefixFilter(include=config.include_prefixes, exclude=config.exclude_prefixes))
    if config.per_logger_levels:
        handler.addFilter(LevelOverrideFilter(config.per_logger_levels))
    if config.redact_keys:
        handler.addFilter(RedactFilter(keys=config.redact_keys, max_value_length=config.max_value_length))
    root.addHandler(handler)


def configure_from_env() -> None:
    """Build a LoggingConfig from OPENAGENTS_LOG_* env vars, then configure()."""
    cfg = load_from_env() or LoggingConfig()
    configure(cfg)


def reset_logging() -> None:
    """Restore the openagents logger to its pre-configure() state.

    Removes handlers tagged ``_openagents_installed=True``, restores
    ``propagate`` to True, clears the root level back to NOTSET, and
    resets any child-logger levels that a prior configure() set via
    ``per_logger_levels``. Third-party handlers (no tag) are left untouched.
    """
    root = logging.getLogger(_LOGGER_ROOT)
    to_remove = [h for h in root.handlers if getattr(h, "_openagents_installed", False)]
    for handler in to_remove:
        root.removeHandler(handler)
    root.propagate = True
    root.setLevel(logging.NOTSET)
    for name in list(_OVERRIDDEN_LOGGERS):
        logging.getLogger(name).setLevel(logging.NOTSET)
    _OVERRIDDEN_LOGGERS.clear()


def _warn_on_foreign_loggers(config: LoggingConfig) -> None:
    foreign = [
        name for name in config.per_logger_levels if name != _LOGGER_ROOT and not name.startswith(_LOGGER_ROOT + ".")
    ]
    for name in foreign:
        _OBS_LOGGER.warning(
            "logger '%s' outside 'openagents.*' namespace is ignored (library etiquette)",
            name,
        )


def _build_handler(config: LoggingConfig) -> logging.Handler:
    if config.pretty:
        from openagents.observability._rich import make_rich_handler

        return make_rich_handler(
            stream=config.stream,
            show_time=config.show_time,
            show_path=config.show_path,
        )
    import sys

    stream = sys.stderr if config.stream == "stderr" else sys.stdout
    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s"))
    handler._openagents_installed = True  # type: ignore[attr-defined]
    return handler


def _name_to_level(name: str) -> int:
    return getattr(logging, name.upper(), logging.INFO)
