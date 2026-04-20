"""WP1: ConfigLoadError for env var / file errors carries helpful hints."""

from __future__ import annotations

import json

import pytest

from openagents.config.loader import load_config
from openagents.errors.exceptions import ConfigLoadError


def test_missing_config_file_includes_hint(tmp_path):
    fake = tmp_path / "no.json"
    with pytest.raises(ConfigLoadError) as ei:
        load_config(fake)
    text = str(ei.value)
    assert "does not exist" in text
    assert "absolute path" in text or "repo root" in text
    assert ei.value.hint is not None


def test_unset_env_var_includes_dotenv_hint(tmp_path, monkeypatch):
    cfg = tmp_path / "agent.json"
    cfg.write_text(
        json.dumps(
            {
                "runtime": {"type": "default"},
                "agents": [
                    {
                        "id": "a",
                        "memory": {"type": "buffer"},
                        "pattern": {"type": "react"},
                        "llm": {"provider": "anthropic", "model": "x", "api_key": "${MY_TEST_VAR_THAT_IS_NEVER_SET}"},
                    }
                ],
            }
        )
    )
    monkeypatch.delenv("MY_TEST_VAR_THAT_IS_NEVER_SET", raising=False)
    with pytest.raises(ConfigLoadError) as ei:
        load_config(cfg)
    text = str(ei.value)
    assert "MY_TEST_VAR_THAT_IS_NEVER_SET" in text
    assert ei.value.hint is not None
    assert ".env" in ei.value.hint or "shell" in ei.value.hint


def test_invalid_json_includes_jq_hint(tmp_path):
    cfg = tmp_path / "agent.json"
    cfg.write_text("{ this is not json")
    with pytest.raises(ConfigLoadError) as ei:
        load_config(cfg)
    text = str(ei.value)
    assert "Invalid JSON" in text
    assert ei.value.hint is not None
    assert "jq" in ei.value.hint or "syntax" in ei.value.hint


def test_directory_not_a_file_includes_hint(tmp_path):
    with pytest.raises(ConfigLoadError) as ei:
        load_config(tmp_path)
    assert "not a file" in str(ei.value)
    assert ei.value.hint is not None
