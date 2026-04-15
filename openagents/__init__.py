"""OpenAgents SDK package.

Quick Start:
    from openagents import Runtime, load_config
    from openagents import tool, memory, pattern, runtime, session

    # Define a tool
    @tool
    async def my_tool(params, context):
        return {"result": "ok"}

    # Define a memory
    @memory
    class MyMemory:
        async def inject(self, context):
            context.memory_view["history"] = []

        async def writeback(self, context):
            ...

        async def retrieve(self, query, context):
            return []

    # Define a pattern
    @pattern
    class MyPattern:
        async def execute(self):
            ...

    # Use in config
    runtime = Runtime.from_config("agent.json")
"""

from .config.loader import load_config, load_config_dict
from .config.schema import AppConfig
from .plugins.builtin.skills.local import (
    LocalSkillsManager,
)
from .interfaces.skills import SkillsPlugin, SessionSkillSummary
from .decorators import (
    context_assembler,
    event_bus,
    execution_policy,
    followup_resolver,
    get_context_assembler,
    get_event_bus,
    get_execution_policy,
    get_followup_resolver,
    get_memory,
    get_pattern,
    get_response_repair_policy,
    get_runtime,
    get_session,
    get_tool,
    get_tool_executor,
    list_context_assemblers,
    list_event_buses,
    list_execution_policies,
    list_followup_resolvers,
    list_memories,
    list_patterns,
    list_response_repair_policies,
    list_runtimes,
    list_sessions,
    list_tools,
    list_tool_executors,
    memory,
    pattern,
    response_repair_policy,
    runtime,
    session,
    tool,
    tool_executor,
)
from .runtime.runtime import Runtime
from .runtime.sync import (
    run_agent,
    run_agent_detailed,
    run_agent_detailed_with_config,
    run_agent_with_config,
    run_agent_with_dict,
)

__all__ = [
    # Core
    "AppConfig",
    "LocalSkillsManager",
    "Runtime",
    "SkillsPlugin",
    "SessionSkillSummary",
    "load_config",
    "load_config_dict",
    "run_agent",
    "run_agent_detailed",
    "run_agent_detailed_with_config",
    "run_agent_with_config",
    "run_agent_with_dict",
    # Decorators
    "tool",
    "memory",
    "pattern",
    "runtime",
    "session",
    "event_bus",
    "tool_executor",
    "execution_policy",
    "context_assembler",
    "followup_resolver",
    "response_repair_policy",
    # Registry accessors
    "get_tool",
    "get_memory",
    "get_pattern",
    "get_runtime",
    "get_session",
    "get_event_bus",
    "get_tool_executor",
    "get_execution_policy",
    "get_context_assembler",
    "get_followup_resolver",
    "get_response_repair_policy",
    "list_tools",
    "list_memories",
    "list_patterns",
    "list_runtimes",
    "list_sessions",
    "list_event_buses",
    "list_tool_executors",
    "list_execution_policies",
    "list_context_assemblers",
    "list_followup_resolvers",
    "list_response_repair_policies",
]
