"""In-process stub HTTP server for the research-analyst example."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiohttp import web

_TOPIC_A = {
    "title": "Topic A — Overview",
    "summary": "Topic A is the first sample corpus used by the research-analyst example.",
    "keywords": ["alpha", "baseline", "demo"],
    "sections": [
        {"heading": "Definition", "body": "Topic A is a fixture payload."},
        {"heading": "Usage", "body": "Used by the integration test."},
    ],
}

_TOPIC_B = "# Topic B\n\nTopic B lives in a markdown fixture so the agent exercises mixed content types.\n"

_FLAKY_OK = {"title": "Flaky source", "summary": "Returned after two 503 attempts."}


class _Flaky:
    def __init__(self) -> None:
        self.calls = 0


async def _topic_a(request: web.Request) -> web.Response:
    return web.json_response(_TOPIC_A)


async def _topic_b(request: web.Request) -> web.Response:
    return web.Response(text=_TOPIC_B, content_type="text/markdown")


def _flaky_handler(state: _Flaky, slow_ms: int = 500):
    async def _handler(request: web.Request) -> web.Response:
        state.calls += 1
        if state.calls <= 2:
            # Simulate an upstream hang: sleep longer than the executor timeout
            # so SafeToolExecutor raises ToolTimeoutError and RetryToolExecutor
            # actually retries. A plain 503 response would not cause a retry,
            # because HttpRequestTool swallows HTTP error codes internally.
            await asyncio.sleep(slow_ms / 1000)
        return web.json_response(_FLAKY_OK)

    return _handler


@asynccontextmanager
async def start_stub_server(flaky_slow_ms: int = 500) -> AsyncIterator[str]:
    """Start the stub server on 127.0.0.1:0 and yield its base URL.

    Each invocation produces a fresh ``_Flaky`` counter, so tests and demos
    see a deterministic two-failure-then-success sequence without needing to
    reset any global state.
    """
    state = _Flaky()
    app = web.Application()
    app.add_routes(
        [
            web.get("/pages/topic-a", _topic_a),
            web.get("/pages/topic-b", _topic_b),
            web.get("/pages/flaky", _flaky_handler(state, slow_ms=flaky_slow_ms)),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[attr-defined]
    port = sockets[0].getsockname()[1] if sockets else 0
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()
