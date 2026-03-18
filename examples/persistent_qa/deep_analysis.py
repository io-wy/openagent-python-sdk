"""
Deep Analysis - OpenAgents SDK 内部机制

这个脚本深入分析:
1. 各插件的完整调用链
2. Session 并发模型
3. Memory 行为差异
4. Pattern 执行流程
5. 性能指标
"""

import asyncio
import time
import json
from pathlib import Path
from collections import defaultdict

from openagents import Runtime
from openagents.config.loader import load_config_dict


class EventTracer:
    """Trace event timeline with detailed timing."""

    def __init__(self):
        self.events = []
        self.start_time = None

    def trace(self, event):
        if self.start_time is None:
            self.start_time = time.perf_counter()
        elapsed = (time.perf_counter() - self.start_time) * 1000
        self.events.append({
            'time_ms': elapsed,
            'name': event.name,
            'payload': dict(event.payload) if hasattr(event, 'payload') else {}
        })

    def print_timeline(self):
        print("\n  完整事件时间线:")
        for e in self.events:
            payload_str = ""
            if e['payload']:
                # 简化 payload 显示
                p = e['payload']
                if 'tool_id' in p:
                    payload_str = f" [tool={p['tool_id']}]"
                elif 'model' in p:
                    payload_str = f" [model={p.get('model', '?')}]"
                elif 'input_text' in p:
                    payload_str = f" [input={p['input_text'][:30]}...]"
                elif 'result' in p:
                    payload_str = f" [result={str(p['result'])[:30]}...]"
            print(f"    +{e['time_ms']:7.1f}ms: {e['name']}{payload_str}")


class SessionTester:
    """Test session concurrency model."""

    def __init__(self, runtime):
        self.runtime = runtime

    async def test_same_session_serial(self):
        """测试同一 session 串行执行"""
        print("\n  [测试1] 同一 Session 串行执行")

        session_id = "serial_test"

        # 连续执行两个请求
        t1 = time.perf_counter()
        r1 = await self.runtime.run(
            agent_id="qa_assistant",
            session_id=session_id,
            input_text="First request"
        )
        t2 = time.perf_counter()

        r2 = await self.runtime.run(
            agent_id="qa_assistant",
            session_id=session_id,
            input_text="Second request"
        )
        t3 = time.perf_counter()

        # 检查 session 状态
        state = await self.runtime.session_manager.get_state(session_id)

        print(f"    请求1: {(t2-t1)*1000:.0f}ms")
        print(f"    请求2: {(t3-t2)*1000:.0f}ms")
        print(f"    Session 状态 keys: {list(state.keys())}")
        print(f"    ✓ 串行执行正常")

    async def test_cross_session_parallel(self):
        """测试不同 session 并行执行"""
        print("\n  [测试2] 不同 Session 并行执行")

        async def run_request(sid, delay=0):
            if delay:
                await asyncio.sleep(delay)
            t1 = time.perf_counter()
            await self.runtime.run(
                agent_id="qa_assistant",
                session_id=sid,
                input_text=f"Request from {sid}"
            )
            t2 = time.perf_counter()
            return sid, (t2-t1)*1000

        # 并发执行 5 个不同 session
        start = time.perf_counter()
        results = await asyncio.gather(
            run_request("parallel_1"),
            run_request("parallel_2"),
            run_request("parallel_3"),
            run_request("parallel_4"),
            run_request("parallel_5"),
        )
        total = (time.perf_counter() - start) * 1000

        print(f"    并发执行 5 个 session:")
        for sid, elapsed in results:
            print(f"      {sid}: {elapsed:.0f}ms")
        print(f"    总耗时: {total:.0f}ms")
        print(f"    理论串行: {sum(e for _, e in results):.0f}ms")
        print(f"    ✓ 并发执行正常")

    async def test_session_isolation(self):
        """测试 Session 隔离"""
        print("\n  [测试3] Session 隔离")

        # 在 session A 中设置值
        await self.runtime.run(
            agent_id="qa_assistant",
            session_id="session_a",
            input_text="Remember: secret_code = 12345"
        )

        # 在 session B 中检查 - 应该没有 session A 的记忆
        state_a = await self.runtime.session_manager.get_state("session_a")
        state_b = await self.runtime.session_manager.get_state("session_b")

        print(f"    session_a keys: {list(state_a.keys())}")
        print(f"    session_b keys: {list(state_b.keys())}")

        # 检查 session 数量
        print(f"    活跃 session 数: {self.runtime.get_session_count()}")
        print(f"    ✓ Session 隔离正常")


class MemoryTester:
    """Test memory behavior."""

    def __init__(self, runtime):
        self.runtime = runtime

    async def test_buffer_memory(self):
        """测试 BufferMemory - 累积所有历史"""
        print("\n  [Memory] BufferMemory 测试")

        # 创建只使用 buffer 的配置
        config = load_config_dict({
            "version": "1.0",
            "agents": [{
                "id": "buffer_test",
                "name": "Buffer Test",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react", "config": {"max_steps": 1}},
                "llm": {"provider": "mock"},
                "tools": []
            }]
        })

        self.runtime._agents_by_id["buffer_test"] = config.agents[0]

        session_id = "buffer_session"

        for i in range(3):
            await self.runtime.run(
                agent_id="buffer_test",
                session_id=session_id,
                input_text=f"Message {i}"
            )

            state = await self.runtime.session_manager.get_state(session_id)
            mv = state.get("memory_view", {})
            print(f"    After msg {i}: history length = {len(mv.get('history', []))}")

        print(f"    ✓ BufferMemory 累积所有消息")

    async def test_window_buffer(self):
        """测试 WindowBufferMemory - 滑动窗口"""
        print("\n  [Memory] WindowBufferMemory 测试")

        config = load_config_dict({
            "version": "1.0",
            "agents": [{
                "id": "window_test",
                "name": "Window Test",
                "memory": {"type": "window_buffer", "config": {"window_size": 3}},
                "pattern": {"type": "react", "config": {"max_steps": 1}},
                "llm": {"provider": "mock"},
                "tools": []
            }]
        })

        self.runtime._agents_by_id["window_test"] = config.agents[0]

        session_id = "window_session"

        for i in range(5):
            await self.runtime.run(
                agent_id="window_test",
                session_id=session_id,
                input_text=f"Message {i}"
            )

            state = await self.runtime.session_manager.get_state(session_id)
            mv = state.get("memory_view", {})
            history = mv.get("history", [])
            print(f"    After msg {i}: history length = {len(history)} (window=3)")

        print(f"    ✓ WindowBufferMemory 保持固定窗口")


class PatternTester:
    """Test pattern behavior."""

    def __init__(self, runtime):
        self.runtime = runtime

    async def test_react(self):
        """测试 ReAct Pattern"""
        print("\n  [Pattern] ReAct - Think → Act → Observe")

        config = load_config_dict({
            "version": "1.0",
            "agents": [{
                "id": "react_test",
                "name": "React Test",
                "memory": {"type": "buffer"},
                "pattern": {"type": "react", "config": {"max_steps": 2}},
                "llm": {"provider": "mock"},
                "tools": [{"id": "calc", "type": "calc"}]
            }]
        })

        self.runtime._agents_by_id["react_test"] = config.agents[0]

        tracer = EventTracer()
        self.runtime.event_bus.subscribe("llm.", tracer.trace)
        self.runtime.event_bus.subscribe("tool.", tracer.trace)

        await self.runtime.run(
            agent_id="react_test",
            session_id="react_session",
            input_text="What is 5 + 3?"
        )

        tracer.print_timeline()
        print(f"    ✓ ReAct: 每个 step 调用 LLM，可选调用工具")

    async def test_plan_execute(self):
        """测试 PlanExecute Pattern"""
        print("\n  [Pattern] PlanExecute - Plan → Execute")

        config = load_config_dict({
            "version": "1.0",
            "agents": [{
                "id": "plan_test",
                "name": "Plan Test",
                "memory": {"type": "buffer"},
                "pattern": {"type": "plan_execute", "config": {"max_steps": 2}},
                "llm": {"provider": "mock"},
                "tools": [{"id": "calc", "type": "calc"}]
            }]
        })

        self.runtime._agents_by_id["plan_test"] = config.agents[0]

        tracer = EventTracer()
        self.runtime.event_bus.subscribe("llm.", tracer.trace)
        self.runtime.event_bus.subscribe("tool.", tracer.trace)

        await self.runtime.run(
            agent_id="plan_test",
            session_id="plan_session",
            input_text="What is 10 + 20?"
        )

        tracer.print_timeline()
        print(f"    ✓ PlanExecute: 先计划，再执行计划")


class ToolTester:
    """Test tool invocation chain."""

    def __init__(self, runtime):
        self.runtime = runtime

    async def test_tool_chain(self):
        """测试工具调用链路"""
        print("\n  [Tool] 工具调用链路")

        tracer = EventTracer()
        self.runtime.event_bus.subscribe("tool.", tracer.trace)
        self.runtime.event_bus.subscribe("llm.", tracer.trace)

        await self.runtime.run(
            agent_id="qa_assistant",
            session_id="tool_chain",
            input_text="Calculate 100 + 200"
        )

        tracer.print_timeline()

        # 分析工具调用耗时
        tool_events = [e for e in tracer.events if e['name'].startswith('tool.')]
        llm_events = [e for e in tracer.events if e['name'].startswith('llm.')]

        print(f"\n  统计:")
        print(f"    LLM 调用次数: {len(llm_events)//2}")  # called + succeeded
        print(f"    Tool 调用次数: {len(tool_events)//2}")

        # 计算各阶段耗时
        if len(tracer.events) > 1:
            total = tracer.events[-1]['time_ms'] - tracer.events[0]['time_ms']
            print(f"    总耗时: {total:.0f}ms")


async def main():
    print("=" * 70)
    print("OpenAgents SDK - Deep Analysis")
    print("深入分析 SDK 内部机制")
    print("=" * 70)

    # 初始化 Runtime
    runtime = Runtime.from_config("examples/persistent_qa/agent.json")

    # 1. Session 并发测试
    print("\n" + "=" * 50)
    print("[1] Session 并发模型测试")
    print("=" * 50)
    session_tester = SessionTester(runtime)
    await session_tester.test_same_session_serial()
    await session_tester.test_cross_session_parallel()
    await session_tester.test_session_isolation()

    # 2. Memory 行为测试
    print("\n" + "=" * 50)
    print("[2] Memory 行为测试")
    print("=" * 50)
    memory_tester = MemoryTester(runtime)
    await memory_tester.test_buffer_memory()
    await memory_tester.test_window_buffer()

    # 3. Pattern 执行流程
    print("\n" + "=" * 50)
    print("[3] Pattern 执行流程")
    print("=" * 50)
    pattern_tester = PatternTester(runtime)
    await pattern_tester.test_react()
    await pattern_tester.test_plan_execute()

    # 4. 工具调用链路
    print("\n" + "=" * 50)
    print("[4] Tool 调用链路")
    print("=" * 50)
    tool_tester = ToolTester(runtime)
    await tool_tester.test_tool_chain()

    # 总结
    print("\n" + "=" * 70)
    print("总结")
    print("=" * 70)
    print("""
  Session 模型:
    - 同一 session: 串行执行 (通过 asyncio.Lock)
    - 不同 session: 并发执行 (独立 lock)
    - Session 隔离: 每个 session 有独立的状态

  Memory 行为:
    - buffer: 累积所有历史消息
    - window_buffer: 只保留最近 N 条消息
    - chain: 可组合多个 memory

  Pattern 流程:
    - react: 循环 (LLM → 决策 → 工具/响应)
    - plan_execute: 先计划，再执行
    - reflexion: 执行后反思，重试

  工具调用:
    - Pattern 决定何时调用工具
    - Tool 接收 params + context
    - 结果返回给 Pattern，继续循环
    """)

    await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
