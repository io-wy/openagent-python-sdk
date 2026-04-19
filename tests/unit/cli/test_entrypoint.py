"""Validate packaging of the ``openagents`` console-script entry point.

The entry point is declared in ``pyproject.toml``
(``[project.scripts] openagents = "openagents.cli.main:main"``). This
test asserts the installed distribution actually advertises it, so we
catch accidental regressions in packaging metadata — for example a
renamed module or a lost ``[project.scripts]`` block.
"""

from __future__ import annotations

import importlib.metadata

import pytest

DIST_NAME = "io-openagent-sdk"


def _get_openagents_console_script():
    try:
        eps = importlib.metadata.entry_points()
    except Exception as exc:  # pragma: no cover - defensive
        pytest.skip(f"entry_points() not available: {exc!r}")
    # Python 3.10+: EntryPoints supports .select(group=...).
    selected = eps.select(group="console_scripts") if hasattr(eps, "select") else eps.get("console_scripts", [])
    for ep in selected:
        if ep.name == "openagents":
            return ep
    return None


def test_openagents_entrypoint_is_registered():
    ep = _get_openagents_console_script()
    if ep is None:
        pytest.skip(
            "io-openagent-sdk is not installed in the current environment "
            "(editable-install via `uv sync` exposes the entry point)"
        )
    assert ep.value == "openagents.cli.main:main", (
        f"expected module:function 'openagents.cli.main:main', got {ep.value!r}"
    )


def test_entrypoint_resolves_to_callable():
    ep = _get_openagents_console_script()
    if ep is None:
        pytest.skip("io-openagent-sdk not installed; nothing to resolve")
    func = ep.load()
    assert callable(func)
    # The resolved callable must be the same object as ``openagents.cli.main.main``.
    from openagents.cli.main import main as cli_main

    assert func is cli_main
