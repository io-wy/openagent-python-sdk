"""Network and URL related tools."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from openagents.interfaces.tool import ToolPlugin


class URLParseTool(ToolPlugin):
    """Parse URL into components.

    What: ``urlparse`` wrapper exposing scheme/netloc/host/port/path/query/fragment.
    Usage: ``{"id": "url_parse", "type": "url_parse"}``; invoke with ``{"url": "https://..."}``.
    Depends on: stdlib ``urllib.parse``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        url = params.get("url", "")
        if not url:
            raise ValueError("'url' parameter is required")

        try:
            parsed = urlparse(url)
            return {
                "scheme": parsed.scheme,
                "netloc": parsed.netloc,
                "hostname": parsed.hostname,
                "port": parsed.port,
                "path": parsed.path,
                "params": parsed.params,
                "query": parsed.query,
                "fragment": parsed.fragment,
                "username": parsed.username,
                "password": parsed.password,
            }
        except Exception as e:
            raise ValueError(f"Invalid URL: {e}")


class URLBuildTool(ToolPlugin):
    """Build URL from components.

    What: assemble ``scheme://host/path?query#fragment``; minimal string concat (no encoding).
    Usage: ``{"id": "url_build", "type": "url_build"}``; invoke with
    ``{"scheme": "https", "host": "...", "path": "/x"}``.
    Depends on: nothing (stdlib).
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        scheme = params.get("scheme", "https")
        host = params.get("host", "")
        path = params.get("path", "/")
        query = params.get("query", "")
        fragment = params.get("fragment", "")

        if not host:
            raise ValueError("'host' parameter is required")

        # Build URL
        url = f"{scheme}://{host}{path}"
        if query:
            url += f"?{query}"
        if fragment:
            url += f"#{fragment}"

        return {"url": url}


class QueryParamTool(ToolPlugin):
    """Extract/query URL parameters.

    What: parse a URL's query string and ``get`` or ``list`` its parameters.
    Usage: ``{"id": "qparam", "type": "query_param"}``; invoke with ``{"url": "...", "key": "...", "action": "get"}``.
    Depends on: stdlib ``urllib.parse``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        url = params.get("url", "")
        key = params.get("key", None)
        action = params.get("action", "get")  # get, set, list

        if not url:
            raise ValueError("'url' parameter is required")

        try:
            parsed = urlparse(url)
            query_dict = parse_qs(parsed.query)

            if action == "list":
                return {"params": {k: v[0] if len(v) == 1 else v for k, v in query_dict.items()}}

            if action == "get":
                if not key:
                    raise ValueError("'key' parameter required for 'get' action")
                values = query_dict.get(key, [])
                if not values:
                    return {"key": key, "value": None}
                return {"key": key, "value": values[0] if len(values) == 1 else values}

            raise ValueError(f"Unknown action: {action}")

        except Exception as e:
            raise ValueError(f"Failed to parse URL: {e}")


class HostLookupTool(ToolPlugin):
    """Simple host information extraction from URL.

    What: parse host/port/scheme from a URL and report HTTPS flag plus best-guess TLD.
    Usage: ``{"id": "host_lookup", "type": "host_lookup"}``; invoke with ``{"url": "https://..."}``.
    Depends on: stdlib ``urllib.parse``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        url = params.get("url", "")
        if not url:
            raise ValueError("'url' parameter is required")

        try:
            parsed = urlparse(url)
            host = parsed.netloc or parsed.path

            # Try to extract port
            port = None
            hostname = host
            if ":" in host:
                parts = host.rsplit(":", 1)
                hostname = parts[0]
                try:
                    port = int(parts[1])
                except ValueError:
                    pass

            return {
                "host": hostname,
                "port": port,
                "has_https": parsed.scheme == "https",
                "domain": hostname.split(".")[-1] if "." in hostname else hostname,
            }
        except Exception as e:
            raise ValueError(f"Invalid URL: {e}")
