"""Optional-dependency detection with a one-shot "install the CLI extras" hint.

Each CLI command that uses an optional extra (Rich, questionary, watchdog,
PyYAML, …) calls :func:`require_or_hint` at entry. The function returns
the imported module on success or ``None`` if the extra is missing. The
first miss in a given process emits a single stderr hint pointing the
user at ``pip install io-openagent-sdk[cli]``; subsequent misses are
silent so pipelines don't get spammed.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from types import ModuleType

_HINT_SHOWN: bool = False

_HINT_MESSAGE = (
    "hint: install io-openagent-sdk[cli] for the full CLI experience (rich output, interactive prompts, hot-reload)"
)


def reset_hint_state() -> None:
    """Test-only: clear the once-per-process latch.

    Exposed so unit tests can assert on the one-shot semantics without
    subprocessing.
    """
    global _HINT_SHOWN
    _HINT_SHOWN = False


def require_or_hint(module_name: str) -> ModuleType | None:
    """Import *module_name* or return ``None`` with a one-shot stderr hint.

    The hint is emitted at most once per process, no matter how many
    different extras are missing. This mirrors the pattern in
    :mod:`openagents.cli.wizard` for ``questionary`` / ``rich`` and
    matches the spec's "graceful fallback" requirement.
    """
    global _HINT_SHOWN
    if importlib.util.find_spec(module_name) is None:
        if not _HINT_SHOWN:
            print(_HINT_MESSAGE, file=sys.stderr)
            _HINT_SHOWN = True
        return None
    return importlib.import_module(module_name)
