"""Config file watcher for hot reload."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable


class ConfigWatcher:
    """Watch config file for changes and trigger reload.

    Usage:
        watcher = ConfigWatcher(runtime, config_path)
        await watcher.start()

        # Later, stop watching
        await watcher.stop()
    """

    def __init__(
        self,
        runtime: Any,
        config_path: str | Path,
        *,
        poll_interval: float = 1.0,  # Check every second
    ):
        self.runtime = runtime
        self.config_path = Path(config_path)
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._last_mtime: float = 0

    async def start(self) -> None:
        """Start watching for config changes."""
        if self._task is not None:
            return  # Already running

        # Get initial mtime
        if self.config_path.exists():
            self._last_mtime = self.config_path.stat().st_mtime

        self._task = asyncio.create_task(self._watch_loop())
        print(f"[ConfigWatcher] Started watching {self.config_path}")

    async def stop(self) -> None:
        """Stop watching."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            print(f"[ConfigWatcher] Stopped watching {self.config_path}")

    async def _watch_loop(self) -> None:
        """Main watch loop."""
        while True:
            try:
                await asyncio.sleep(self.poll_interval)

                if not self.config_path.exists():
                    continue

                current_mtime = self.config_path.stat().st_mtime
                if current_mtime > self._last_mtime:
                    self._last_mtime = current_mtime
                    print(f"[ConfigWatcher] Config changed, reloading...")
                    await self.runtime.reload()
                    print(f"[ConfigWatcher] Reload complete")

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[ConfigWatcher] Error: {e}")


class HotReloadServer:
    """HTTP server with hot reload support.

    Usage:
        server = HotReloadServer(runtime, config_path, host="0.0.0.0", port=8080)
        await server.start()
    """

    def __init__(
        self,
        runtime: Any,
        config_path: str | Path,
        *,
        host: str = "0.0.0.0",
        port: int = 8080,
    ):
        self.runtime = runtime
        self.config_path = Path(config_path)
        self.host = host
        self.port = port
        self._watcher: ConfigWatcher | None = None
        self._server: Any = None
        self._web_module: Any = None  # Store aiohttp web module for handlers

    async def start(self) -> None:
        """Start the server with config watching.

        Uses ``aiohttp.AppRunner`` + ``TCPSite`` so ``start()`` returns
        immediately after the server is bound. ``web._run_app`` was
        previously used here but it blocks until the server stops,
        which makes ``start()`` impossible to await in any program that
        wants to do anything else.
        """
        # Start config watcher
        self._watcher = ConfigWatcher(self.runtime, self.config_path)
        await self._watcher.start()

        # Try to use aiohttp if available, otherwise simple fallback
        try:
            from aiohttp import web
        except ImportError:
            print(f"[HotReloadServer] aiohttp not available, running in CLI mode")
            print(f"[HotReloadServer] Config file: {self.config_path}")
            print(f"[HotReloadServer] Hot reload enabled - edit config to trigger reload")
            return

        self._web_module = web  # Store for use in handlers
        app = web.Application()
        app.router.add_post("/run", self._handle_run)
        app.router.add_post("/reload", self._handle_reload)
        app.router.add_get("/agents", self._handle_list_agents)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=self.host, port=self.port)
        await site.start()
        self._server = runner

    async def _handle_run(self, request: Any) -> Any:
        """Handle /run endpoint."""
        data = await request.json()
        agent_id = data.get("agent_id", "qa_assistant")
        session_id = data.get("session_id", "default")
        input_text = data.get("input", "")

        result = await self.runtime.run(
            agent_id=agent_id,
            session_id=session_id,
            input_text=input_text,
        )

        return self._web_module.json_response({"result": result})

    async def _handle_reload(self, request: Any) -> Any:
        """Handle /reload endpoint."""
        await self.runtime.reload()
        return self._web_module.json_response({"status": "reloaded"})

    async def _handle_list_agents(self, request: Any) -> Any:
        """Handle /agents endpoint."""
        agents = await self.runtime.list_agents()
        return self._web_module.json_response({"agents": agents})

    async def stop(self) -> None:
        """Stop the server."""
        if self._watcher:
            await self._watcher.stop()
        if self._server is not None:
            await self._server.cleanup()
            self._server = None
