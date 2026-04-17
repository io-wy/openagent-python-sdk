from __future__ import annotations

import shutil
import sys
from uuid import uuid4
from pathlib import Path

import pytest


SKILL_SRC = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "openagent-agent-builder"
    / "src"
)

if str(SKILL_SRC) not in sys.path:
    sys.path.insert(0, str(SKILL_SRC))


_TMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp" / "pytest-local"


@pytest.fixture
def tmp_path() -> Path:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_openagents_logging() -> None:
    """Reset the 'openagents' logger after every test.

    Any code path that invokes Runtime.from_config/from_dict with
    ``logging.auto_configure: true`` installs handlers on the
    'openagents' logger and flips ``propagate`` to False. Without a
    reset, those side effects leak into unrelated tests and break
    ``caplog``-based assertions on warnings.
    """
    yield
    from openagents.observability.logging import reset_logging

    reset_logging()
