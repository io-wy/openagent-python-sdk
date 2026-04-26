"""HTTP operation tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib import request

from pydantic import BaseModel

from openagents.interfaces.tool import ToolPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin


class HttpRequestTool(TypedConfigPluginMixin, ToolPlugin):
    """Make HTTP request.

    What: synchronous urllib HTTP client run on a worker thread; supports
    GET/POST/PUT/DELETE/PATCH with JSON or raw body.
    Usage: ``{"id": "http", "type": "http_request", "config": {"timeout": 30}}``; invoke with
    ``{"url": "...", "method": "GET", "headers": {...}, "body": ...}``.
    Depends on: stdlib ``urllib.request``; pair with ``network_allowlist`` execution policy.
    """

    # Conservative default: any HTTP method may have side effects.
    # Read-only GET agents can subclass and override to True if needed.
    durable_idempotent = False

    class Config(BaseModel):
        timeout: int = 30

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._init_typed_config()
        self._timeout = self.cfg.timeout

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        headers = params.get("headers", {})
        body = params.get("body")
        timeout = params.get("timeout", self._timeout)

        if not url:
            raise ValueError("'url' parameter is required")
        if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            raise ValueError(f"Unsupported method: {method}")

        def _make_request():
            req_headers = dict(headers)
            data = None

            if body is not None:
                if isinstance(body, dict):
                    data = json.dumps(body).encode("utf-8")
                    req_headers.setdefault("Content-Type", "application/json")
                else:
                    data = str(body).encode("utf-8")

            req = request.Request(url, data=data, headers=req_headers, method=method)

            try:
                with request.urlopen(req, timeout=timeout) as resp:
                    response_body = resp.read().decode("utf-8")
                    return {
                        "url": url,
                        "method": method,
                        "status": resp.status,
                        "headers": dict(resp.headers),
                        "body": response_body,
                        "success": 200 <= resp.status < 300,
                    }
            except Exception as e:
                return {
                    "url": url,
                    "method": method,
                    "error": str(e),
                    "success": False,
                }

        try:
            return await asyncio.to_thread(_make_request)
        except Exception as e:
            raise RuntimeError(f"HTTP request failed: {e}")
