"""Typed plugin configuration helper."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TypedConfigPluginMixin:
    """Mixin that provides typed config validation for plugins.

    Subclasses declare a nested ``Config(BaseModel)`` and the mixin
    validates ``self.config`` into ``self.cfg`` when ``_init_typed_config``
    is invoked from the subclass ``__init__`` (after super().__init__).

    Unknown config keys emit a warning but are not rejected; this is
    a migration safety choice for the 0.3.x line. A future major
    release may switch to ``extra='forbid'``.
    """

    Config: ClassVar[type[BaseModel]]
    cfg: BaseModel

    def _init_typed_config(self) -> None:
        raw = dict(getattr(self, "config", {}) or {})
        config_cls = self.Config
        known = set(config_cls.model_fields.keys())
        unknown = sorted(set(raw.keys()) - known)
        if unknown:
            logger.warning(
                "plugin %s received unknown config keys: %s",
                type(self).__name__,
                unknown,
            )
        self.cfg = config_cls.model_validate(raw)
