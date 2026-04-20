import json
import os
from pathlib import Path
from typing import Any

from openagents.config.loader import load_config_dict
from openagents.runtime import Runtime


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_runtime(config_path: Path) -> Runtime:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    agent: dict[str, Any] = payload["agents"][0]
    llm: dict[str, Any] = agent.get("llm", {})
    llm["model"] = os.getenv("OPENAI_MODEL", llm.get("model", "gpt-4o-mini"))
    llm["api_base"] = os.getenv("OPENAI_BASE_URL", llm.get("api_base", ""))
    llm["api_key_env"] = "OPENAI_API_KEY"
    agent["llm"] = llm
    return Runtime(load_config_dict(payload))
