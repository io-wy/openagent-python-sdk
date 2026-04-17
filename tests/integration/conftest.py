"""Integration test setup.

Several integration tests load ``examples/*/agent.json`` directly via
``Runtime.from_config``. Those configs reference ``${LLM_API_BASE}`` /
``${LLM_API_KEY}`` / ``${LLM_MODEL}`` so the real example can swap providers
through ``.env`` files. Tests stub the LLM client (no live HTTP), but the
config loader still resolves env-var placeholders at parse time.

Provide stub fallbacks here so tests run in any shell. ``setdefault`` means
real values from a developer's shell or CI secrets always win.
"""

from __future__ import annotations

import os

os.environ.setdefault("LLM_API_BASE", "http://stub.invalid")
os.environ.setdefault("LLM_API_KEY", "stub-key")
os.environ.setdefault("LLM_MODEL", "stub-model")
