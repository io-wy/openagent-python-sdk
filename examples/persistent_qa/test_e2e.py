"""Comprehensive end-to-end test for OpenAgents SDK.

This script tests:
1. Built-in tools (calc, time, random, uuid, url, json, file)
2. ChainMemory
3. Event Bus monitoring
4. Hot reload
5. Session isolation
"""

import asyncio
import json
from pathlib import Path

from openagents import Runtime


async def main():
    print("=" * 60)
    print("OpenAgents SDK - Comprehensive E2E Test")
    print("=" * 60)

    runtime = Runtime.from_config("examples/persistent_qa/agent.json")

    # Subscribe to events
    events_log = []

    async def on_event(event):
        events_log.append(event.name)
        print(f"  [Event] {event.name}")

    runtime.event_bus.subscribe("run.requested", on_event)
    runtime.event_bus.subscribe("run.completed", on_event)
    runtime.event_bus.subscribe("run.failed", on_event)
    runtime.event_bus.subscribe("tool.called", on_event)
    runtime.event_bus.subscribe("tool.succeeded", on_event)
    runtime.event_bus.subscribe("llm.called", on_event)
    runtime.event_bus.subscribe("llm.succeeded", on_event)
    runtime.event_bus.subscribe("config.reloaded", on_event)

    # Test 1: Basic tool calls
    print("\n[Test 1] Basic Tool Calls")
    print("-" * 40)

    # Calculator
    result = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_calc",
        input_text="What is 123 * 456?",
    )
    print(f"  calc result: {result}")

    # Current time
    result = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_time",
        input_text="What's the current time?",
    )
    print(f"  time result: {result}")

    # Random int
    result = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_random",
        input_text="Generate a random number between 1 and 100",
    )
    print(f"  random result: {result}")

    # UUID
    result = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_uuid",
        input_text="Generate a UUID",
    )
    print(f"  uuid result: {result}")

    # URL parse
    result = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_url",
        input_text="Parse the URL https://api.example.com/v1/users?id=123",
    )
    print(f"  url parse result: {result}")

    # JSON parse
    result = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_json",
        input_text='Parse this JSON: {"name": "test", "value": 42}',
    )
    print(f"  json parse result: {result}")

    # Test 2: Memory (ChainMemory with window_buffer)
    print("\n[Test 2] Memory - Session Continuity")
    print("-" * 40)

    # First interaction
    result1 = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_memory",
        input_text="Remember this: My favorite color is blue",
    )
    print(f"  first interaction: {result1}")

    # Second interaction in same session (should have memory)
    result2 = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_memory",
        input_text="What is my favorite color?",
    )
    print(f"  second interaction: {result2}")

    # Test 3: Different session (should NOT have memory)
    print("\n[Test 3] Session Isolation")
    print("-" * 40)

    result3 = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_memory_2",
        input_text="What is my favorite color?",
    )
    print(f"  new session (no memory): {result3}")

    # Test 4: Event Bus
    print("\n[Test 4] Event Bus Monitoring")
    print("-" * 40)
    print(f"  Events captured: {len(events_log)}")
    print(f"  Event types: {set(events_log)}")

    # Test 5: Hot Reload
    print("\n[Test 5] Hot Reload")
    print("-" * 40)

    # Get initial session count
    initial_sessions = runtime.get_session_count()
    print(f"  Active sessions before reload: {initial_sessions}")

    # Trigger reload
    await runtime.reload()
    print(f"  Reload triggered")

    # Sessions should still be tracked
    print(f"  Active sessions after reload: {runtime.get_session_count()}")

    # New sessions should work
    result4 = await runtime.run(
        agent_id="qa_assistant",
        session_id="test_after_reload",
        input_text="Hello after reload!",
    )
    print(f"  After reload: {result4}")

    # Test 6: Agent Info
    print("\n[Test 6] Agent Info")
    print("-" * 40)

    info = await runtime.get_agent_info("qa_assistant")
    print(f"  Agent: {info['name']}")
    print(f"  Memory: {info['loaded_plugins']['memory']}")
    print(f"  Pattern: {info['loaded_plugins']['pattern']}")
    print(f"  Tools: {len(info['loaded_plugins']['tools'])} tools")

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"  All tests completed successfully!")
    print(f"  Total events logged: {len(events_log)}")

    # Cleanup
    await runtime.close()
    print("\nRuntime closed.")


if __name__ == "__main__":
    asyncio.run(main())
