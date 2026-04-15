from __future__ import annotations

import sys
from pathlib import Path


SKILL_SRC = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "openagent-agent-builder"
    / "src"
)

if str(SKILL_SRC) not in sys.path:
    sys.path.insert(0, str(SKILL_SRC))
