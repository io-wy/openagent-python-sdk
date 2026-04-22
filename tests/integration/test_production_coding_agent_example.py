from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

import openagents.llm.registry as llm_registry
from examples.production_coding_agent.app.benchmark import run_benchmark
from openagents.interfaces.runtime import RunRequest
from openagents.llm.base import LLMClient
from openagents.runtime.runtime import Runtime


class _DeterministicCodingLLM(LLMClient):
    def __init__(self, *, empty_on: str | None = None):
        self.calls = 0
        self.empty_on = empty_on

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        response_format: dict | None = None,
    ) -> str:
        _ = (model, temperature, max_tokens, tools, tool_choice, response_format)
        self.calls += 1
        user_text = ""
        for item in reversed(messages):
            if item.get("role") == "user":
                user_text = item.get("content", "")
                break

        if self.empty_on and f"PURPOSE: {self.empty_on}" in user_text:
            return ""

        if "PURPOSE: planning" in user_text:
            return json.dumps(
                {
                    "objective": "Investigate API client configuration flow",
                    "search_patterns": ["API_BASE_URL", "load_settings", "timeout"],
                    "target_files": ["app/config.py", "app/service.py", "tests/test_service.py"],
                    "deliverables": ["delivery-report.md", "patch-plan.md"],
                    "success_criteria": [
                        "Identify likely hotspot",
                        "Produce a delivery report",
                        "Produce actionable next steps",
                    ],
                    "risks_to_check": ["Missing validation", "Weak tests"],
                },
                ensure_ascii=False,
            )

        if "PURPOSE: project_blueprint" in user_text:
            return json.dumps(
                {
                    "project_name": "todo-cli-app",
                    "package_name": "todo_cli_app",
                    "project_type": "python_cli",
                    "summary": "A complete Python CLI project for task tracking.",
                    "goals": [
                        "Scaffold a runnable Python project",
                        "Provide a CLI entrypoint",
                        "Ship tests and documentation",
                    ],
                    "generated_files": [
                        "README.md",
                        "pyproject.toml",
                        "src/todo_cli_app/__init__.py",
                        "src/todo_cli_app/service.py",
                        "src/todo_cli_app/cli.py",
                        "tests/test_service.py",
                        ".gitignore",
                    ],
                    "verification_commands": ["python -m py_compile src/**/*.py", "pytest -q"],
                },
                ensure_ascii=False,
            )

        if "PURPOSE: delivery" in user_text:
            return json.dumps(
                {
                    "summary": "已完成仓库检查，并生成可执行的交付报告与补丁计划。",
                    "root_cause": "当前实现依赖环境输入，但配置边界和回归验证说明不够集中。",
                    "recommended_changes": [
                        "集中收敛 API client 配置入口",
                        "补充对空 key 与基础配置边界的测试",
                        "在交付说明中明确运行与回退策略",
                    ],
                    "tests_to_run": ["pytest -q", "针对配置边界的定向回归测试"],
                    "risks": ["后续变更可能引入新的配置分叉", "文档与代码可能再次漂移"],
                    "next_steps": ["评审补丁计划", "实施最小修改", "跑定向验证"],
                },
                ensure_ascii=False,
            )

        return json.dumps({"type": "final", "content": "unexpected"})


def _outputs_root() -> Path:
    return Path("examples/production_coding_agent/outputs")


def _memory_root() -> Path:
    return Path("examples/production_coding_agent/.agent_memory")


def _generated_projects_root() -> Path:
    return _outputs_root() / "generated_projects"


def _cleanup_generated_files() -> None:
    for path in (
        _outputs_root() / "task-brief.json",
        _outputs_root() / "delivery-report.md",
        _outputs_root() / "patch-plan.md",
        _outputs_root() / "verification-report.md",
    ):
        if path.exists():
            path.unlink(missing_ok=True)
    if _generated_projects_root().exists():
        shutil.rmtree(_generated_projects_root())
    if _memory_root().exists():
        shutil.rmtree(_memory_root())


@pytest.mark.asyncio
async def test_production_coding_agent_generates_delivery_artifacts(monkeypatch):
    _cleanup_generated_files()
    client = _DeterministicCodingLLM()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)
    runtime = Runtime.from_config("examples/production_coding_agent/agent.json")

    result = await runtime.run(
        agent_id="production-coding-agent",
        session_id="prod-coding-artifacts",
        input_text="Investigate the API client configuration flow and produce a delivery report.",
    )

    assert result["summary"] == "已完成仓库检查，并生成可执行的交付报告与补丁计划。"
    assert len(result["artifacts"]) == 4
    assert (_outputs_root() / "task-brief.json").exists()
    assert (_outputs_root() / "delivery-report.md").exists()
    assert (_outputs_root() / "patch-plan.md").exists()
    assert (_outputs_root() / "verification-report.md").exists()

    report = (_outputs_root() / "delivery-report.md").read_text(encoding="utf-8")
    assert "Delivery Report" in report
    assert "app/config.py" in report
    verification = (_outputs_root() / "verification-report.md").read_text(encoding="utf-8")
    assert "Verification Report" in verification
    assert client.calls == 2

    state = await runtime.session_manager.get_state("prod-coding-artifacts")
    assert len(state.get("coding_journal", [])) == 1
    assert state["coding_journal"][0]["artifacts"]

    await runtime.close()
    _cleanup_generated_files()


@pytest.mark.asyncio
async def test_production_coding_agent_followup_is_resolved_locally(monkeypatch):
    _cleanup_generated_files()
    client = _DeterministicCodingLLM()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)
    runtime = Runtime.from_config("examples/production_coding_agent/agent.json")

    await runtime.run(
        agent_id="production-coding-agent",
        session_id="prod-coding-followup",
        input_text="Inspect the repository and prepare a report.",
    )
    calls_after_first_run = client.calls

    followup = await runtime.run(
        agent_id="production-coding-agent",
        session_id="prod-coding-followup",
        input_text="你刚干了什么",
    )

    assert client.calls == calls_after_first_run
    assert "上一轮我处理了一个仓库任务" in followup
    assert "输出产物" in followup

    await runtime.close()
    _cleanup_generated_files()


@pytest.mark.asyncio
async def test_production_coding_agent_persists_coding_memory(monkeypatch):
    _cleanup_generated_files()
    client = _DeterministicCodingLLM()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)
    runtime = Runtime.from_config("examples/production_coding_agent/agent.json")

    await runtime.run(
        agent_id="production-coding-agent",
        session_id="prod-coding-memory",
        input_text="Inspect config flow and record the outcome.",
    )
    await runtime.close()

    memory_files = list(_memory_root().glob("*.json"))
    assert len(memory_files) == 1
    data = json.loads(memory_files[0].read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
    assert "summary" in data[-1]
    assert "artifacts" in data[-1]

    _cleanup_generated_files()


@pytest.mark.asyncio
async def test_production_coding_agent_surfaces_repair_diagnostic(monkeypatch):
    _cleanup_generated_files()
    client = _DeterministicCodingLLM(empty_on="planning")
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)
    runtime = Runtime.from_config("examples/production_coding_agent/agent.json")

    result = await runtime.run_detailed(
        request=RunRequest(
            agent_id="production-coding-agent",
            session_id="prod-coding-repair",
            input_text="Inspect config flow with an empty provider response.",
        )
    )

    assert result.stop_reason == "failed"
    assert "coding-delivery orchestration" in (result.error_details.message if result.error_details else "")
    assert "LLM returned an empty response" in (result.error_details.message if result.error_details else "")

    await runtime.close()
    _cleanup_generated_files()


@pytest.mark.asyncio
async def test_production_coding_agent_benchmark_harness_passes():
    results = await run_benchmark()

    assert len(results) == 3
    assert all(item.passed for item in results)


@pytest.mark.asyncio
async def test_production_coding_agent_can_generate_complete_project(monkeypatch):
    _cleanup_generated_files()
    client = _DeterministicCodingLLM()
    monkeypatch.setattr(llm_registry, "create_llm_client", lambda llm: client)
    runtime = Runtime.from_config("examples/production_coding_agent/agent.json")

    result = await runtime.run(
        agent_id="production-coding-agent",
        session_id="prod-coding-project",
        input_text="Create a complete Python CLI project for task tracking with tests, pyproject, and README.",
    )

    assert result["mode"] == "project_generation"
    assert result["project_name"] == "todo-cli-app"
    assert "src/todo_cli_app/cli.py" in result["generated_files"]
    project_root = Path(result["project_root"])
    assert (project_root / "pyproject.toml").exists()
    assert (project_root / "README.md").exists()
    assert (project_root / "src" / "todo_cli_app" / "cli.py").exists()
    assert (project_root / "tests" / "test_service.py").exists()

    package_root = project_root / "src"
    sys.path.insert(0, str(package_root))
    try:
        from todo_cli_app.service import build_message  # type: ignore import-not-found

        assert "demo" in build_message("demo")
    finally:
        sys.path.pop(0)

    await runtime.close()
    _cleanup_generated_files()
