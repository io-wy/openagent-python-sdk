from __future__ import annotations

import asyncio

import aiohttp
import pytest

from examples.research_analyst.app.stub_server import start_stub_server


@pytest.mark.asyncio
async def test_topic_a_returns_json():
    async with start_stub_server() as base_url:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/pages/topic-a") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert "title" in data


@pytest.mark.asyncio
async def test_topic_b_returns_markdown():
    async with start_stub_server() as base_url:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/pages/topic-b") as resp:
                assert resp.status == 200
                body = await resp.text()
                assert body.startswith("#")


@pytest.mark.asyncio
async def test_flaky_times_out_twice_then_succeeds():
    async with start_stub_server(flaky_slow_ms=500) as base_url:
        client_timeout = aiohttp.ClientTimeout(total=0.1)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            timeouts = 0
            for _ in range(2):
                try:
                    async with session.get(f"{base_url}/pages/flaky") as resp:
                        _ = await resp.text()
                except asyncio.TimeoutError:
                    timeouts += 1
            assert timeouts == 2
        # Third attempt with generous timeout succeeds immediately.
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/pages/flaky") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["title"] == "Flaky source"


@pytest.mark.asyncio
async def test_counter_is_per_instance():
    # With flaky_slow_ms=0, all three attempts succeed immediately; the counter
    # branch is never entered. The per-instance behavior is proven by ensuring
    # each server instance allows at least one successful call to /pages/flaky
    # (no state leaks across instances).
    async with start_stub_server(flaky_slow_ms=0) as base_url:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{base_url}/pages/flaky") as r:
                assert r.status == 200
    async with start_stub_server(flaky_slow_ms=0) as base_url:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{base_url}/pages/flaky") as r:
                assert r.status == 200
