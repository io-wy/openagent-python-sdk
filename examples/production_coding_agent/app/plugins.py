from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openagents.interfaces.context import ContextAssemblyResult
from openagents.interfaces.followup import FollowupResolution
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.pattern import PatternPlugin
from openagents.interfaces.response_repair import ResponseRepairDecision
from openagents.interfaces.run_context import RunContext

from .protocols import DeliveryEnvelope, ProjectBlueprint, TaskPlan, VerificationEnvelope


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _trim_text(value: str, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _safe_session_id(session_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)


def _slugify(value: str) -> str:
    text = "".join(c.lower() if c.isalnum() else "-" for c in str(value or "generated-project"))
    while "--" in text:
        text = text.replace("--", "-")
    return text.strip("-") or "generated-project"


class CodingMemory(MemoryPlugin):
    """Persistent project memory for the coding agent."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._storage_dir = Path(self.config.get("storage_dir", ".agent_memory"))
        self._max_items = int(self.config.get("max_items", 50))
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def _storage_path(self, session_id: str) -> Path:
        return self._storage_dir / f"{_safe_session_id(session_id)}.json"

    def _load_records(self, session_id: str) -> list[dict[str, Any]]:
        path = self._storage_path(session_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save_records(self, session_id: str, records: list[dict[str, Any]]) -> None:
        path = self._storage_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(records[-self._max_items :], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def inject(self, context: Any) -> None:
        records = self._load_records(context.session_id)
        context.memory_view["coding_history"] = records[-5:]
        if records:
            context.memory_view["last_delivery_summary"] = records[-1].get("summary", "")

    async def writeback(self, context: Any) -> None:
        journal = context.state.get("coding_journal", [])
        latest = journal[-1] if isinstance(journal, list) and journal else {}
        records = self._load_records(context.session_id)
        records.append(
            {
                "timestamp": _now_iso(),
                "input": context.input_text,
                "summary": latest.get("summary", context.state.get("_runtime_last_output", "")),
                "matched_files": latest.get("matched_files", []),
                "artifacts": latest.get("artifacts", []),
                "tool_ids": [item.get("tool_id") for item in context.tool_results if isinstance(item, dict)],
            }
        )
        self._save_records(context.session_id, records)
        context.memory_view["coding_history"] = records[-5:]


class CodingTaskContextAssembler:
    """Prepare a task packet with workspace and session context."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._workspace_root = Path(self.config.get("workspace_root", ".")).resolve(strict=False)
        self._outputs_root = Path(self.config.get("outputs_root", ".")).resolve(strict=False)
        self._brief_path = Path(self.config.get("brief_path", self._workspace_root / "PRODUCT_BRIEF.md"))
        self._max_messages = int(self.config.get("max_messages", 12))
        self._max_artifacts = int(self.config.get("max_artifacts", 8))
        self._max_workspace_files = int(self.config.get("max_workspace_files", 40))

    def _workspace_manifest(self) -> list[str]:
        files: list[str] = []
        if not self._workspace_root.exists():
            return files
        for path in sorted(self._workspace_root.rglob("*")):
            if path.is_file():
                files.append(str(path.relative_to(self._workspace_root)))
            if len(files) >= self._max_workspace_files:
                break
        return files

    def _task_brief(self) -> str:
        if not self._brief_path.exists():
            return ""
        try:
            return _trim_text(self._brief_path.read_text(encoding="utf-8"), limit=1500)
        except OSError:
            return ""

    async def assemble(
        self, *, request: Any, session_state: dict[str, Any], session_manager: Any
    ) -> ContextAssemblyResult:
        transcript = await session_manager.load_messages(request.session_id)
        artifacts = await session_manager.list_artifacts(request.session_id)
        omitted_messages = max(0, len(transcript) - self._max_messages)
        omitted_artifacts = max(0, len(artifacts) - self._max_artifacts)
        if omitted_messages:
            transcript = transcript[-self._max_messages :]
        if omitted_artifacts:
            artifacts = artifacts[-self._max_artifacts :]
        packet = {
            "workspace_root": str(self._workspace_root),
            "outputs_root": str(self._outputs_root),
            "brief_path": str(self._brief_path),
            "task_brief": self._task_brief(),
            "workspace_manifest": self._workspace_manifest(),
            "recent_artifacts": [item.name for item in artifacts],
            "last_output": session_state.get("_runtime_last_output"),
            "omitted_messages": omitted_messages,
            "omitted_artifacts": omitted_artifacts,
        }
        return ContextAssemblyResult(
            transcript=transcript,
            session_artifacts=artifacts,
            metadata={"assembler": "coding_task", "task_packet": packet},
        )

    async def finalize(self, *, request: Any, session_state: dict[str, Any], session_manager: Any, result: Any) -> Any:
        _ = (request, session_manager)
        session_state["last_result_type"] = type(result).__name__
        return result


class ProductionCodingPattern(PatternPlugin):
    """Production-style coding agent example with explicit planning and delivery.

    With the consolidated seam API, this pattern folds follow-up resolution and
    empty-response repair into ``resolve_followup()`` and
    ``repair_empty_response()`` overrides (see below), rather than delegating
    them to separate ``followup_resolver`` / ``response_repair_policy`` plugins.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self.context: RunContext[Any] | None = None

    async def resolve_followup(self, *, context: RunContext[Any]) -> FollowupResolution | None:
        """Resolve common local follow-up questions from the coding journal."""
        text = str(context.input_text or "").strip().lower()
        markers = ("你刚干了什么", "上一轮做了什么", "刚才做了什么", "what did you do", "what happened last turn")
        if not any(marker in text for marker in markers):
            return None
        journal = context.state.get("coding_journal", [])
        if not isinstance(journal, list) or not journal:
            return FollowupResolution(status="abstain", reason="No coding journal available.")
        latest = journal[-1]
        lines = ["上一轮我处理了一个仓库任务。"]
        if latest.get("summary"):
            lines.append(f"结论摘要：{latest['summary']}")
        if latest.get("matched_files"):
            lines.append(f"重点查看的文件：{', '.join(latest['matched_files'][:5])}")
        if latest.get("artifacts"):
            lines.append(f"输出产物：{', '.join(latest['artifacts'])}")
        return FollowupResolution(status="resolved", output="\n".join(lines), metadata=dict(latest))

    async def repair_empty_response(
        self,
        *,
        context: RunContext[Any],
        messages: list[dict[str, Any]],
        assistant_content: list[dict[str, Any]],
        stop_reason: str | None,
        retries: int,
    ) -> ResponseRepairDecision | None:
        """Return a detailed structured diagnostic when the model goes silent."""
        packet = context.assembly_metadata.get("task_packet", {})
        reason = (
            "LLM returned an empty response during coding-delivery orchestration. "
            f"stop_reason={stop_reason or '<none>'}, retries={retries}, messages={len(messages)}, "
            f"content_blocks={len(assistant_content)}, workspace_root={packet.get('workspace_root', '<unknown>')}, "
            f"input={context.input_text!r}."
        )
        return ResponseRepairDecision(status="error", reason=reason, metadata={"stage": "coding-delivery"})

    async def react(self) -> dict[str, Any]:
        return {"type": "final", "content": "Use execute()."}

    async def execute(self) -> Any:
        ctx = self.context
        assert ctx is not None
        resolution = await self.resolve_followup(context=ctx)
        if resolution is not None:
            if resolution.status == "resolved":
                ctx.state["_runtime_last_output"] = resolution.output
                return resolution.output
            if resolution.status == "error":
                raise RuntimeError(resolution.reason or "follow-up resolution failed")
        packet = ctx.assembly_metadata.get("task_packet", {})
        if self._should_generate_project(ctx.input_text):
            return await self._generate_project(packet)
        plan = await self._build_plan(packet)
        inspection = await self._inspect_workspace(plan, packet)
        delivery = await self._build_delivery(plan, inspection, packet)
        verification = self._build_verification(plan, inspection, delivery)
        outputs_root = Path(packet["outputs_root"])
        outputs_root.mkdir(parents=True, exist_ok=True)
        task_brief_path = await self._write_output(
            outputs_root / "task-brief.json",
            json.dumps(plan.__dict__, ensure_ascii=False, indent=2),
            kind="task_plan",
        )
        report_path = await self._write_output(
            outputs_root / "delivery-report.md",
            self._delivery_report(plan, inspection, delivery),
            kind="delivery_report",
        )
        patch_plan_path = await self._write_output(
            outputs_root / "patch-plan.md",
            self._patch_plan(plan, inspection, delivery),
            kind="patch_plan",
        )
        verification_path = await self._write_output(
            outputs_root / "verification-report.md",
            self._verification_report(verification),
            kind="verification_report",
        )
        matched_files = [item.get("display_path", item["path"]) for item in inspection["read_results"]]
        entries = list(ctx.state.get("coding_journal", []))
        artifact_paths = [task_brief_path, report_path, patch_plan_path, verification_path]
        entries.append(
            {
                "timestamp": _now_iso(),
                "objective": plan.objective,
                "summary": delivery.summary,
                "matched_files": matched_files,
                "artifacts": artifact_paths,
            }
        )
        ctx.state["coding_journal"] = entries[-10:]
        ctx.state["_runtime_last_output"] = delivery.summary
        return {
            "summary": delivery.summary,
            "root_cause": delivery.root_cause,
            "matched_files": matched_files,
            "artifacts": artifact_paths,
            "risks": delivery.risks,
            "next_steps": delivery.next_steps,
            "verification": verification.__dict__,
            "task_packet": packet,
        }

    def _should_generate_project(self, input_text: str) -> bool:
        text = str(input_text or "").lower()
        project_words = ("create", "build", "scaffold", "generate", "make")
        target_words = ("project", "app", "application", "service", "tool", "cli")
        return any(word in text for word in project_words) and any(word in text for word in target_words)

    async def _generate_project(self, packet: dict[str, Any]) -> Any:
        blueprint = await self._build_project_blueprint(packet)
        project_root = Path(packet["outputs_root"]) / "generated_projects" / _slugify(blueprint.project_name)
        created_files = await self._materialize_project(project_root, blueprint)
        summary = f"Generated project '{blueprint.project_name}' with {len(created_files)} files."
        self.context.state["_runtime_last_output"] = summary
        entries = list(self.context.state.get("coding_journal", []))
        entries.append(
            {
                "timestamp": _now_iso(),
                "objective": blueprint.summary,
                "summary": summary,
                "matched_files": [],
                "artifacts": [str(project_root / "README.md"), str(project_root / "pyproject.toml")],
            }
        )
        self.context.state["coding_journal"] = entries[-10:]
        return {
            "mode": "project_generation",
            "summary": summary,
            "project_root": str(project_root),
            "project_name": blueprint.project_name,
            "package_name": blueprint.package_name,
            "generated_files": created_files,
            "artifacts": [str(project_root / "README.md"), str(project_root / "pyproject.toml")],
            "verification_commands": blueprint.verification_commands,
        }

    async def _build_plan(self, packet: dict[str, Any]) -> TaskPlan:
        fallback = self._heuristic_plan(packet)
        payload = await self._call_json(
            purpose="planning",
            body={
                "user_request": self.context.input_text,
                "workspace_manifest": packet.get("workspace_manifest", []),
                "task_brief": packet.get("task_brief", ""),
                "recent_artifacts": packet.get("recent_artifacts", []),
            },
            fallback=fallback.__dict__,
        )
        return TaskPlan(
            objective=str(payload.get("objective", fallback.objective)).strip() or fallback.objective,
            search_patterns=[str(item) for item in payload.get("search_patterns", fallback.search_patterns)[:3]],
            target_files=[str(item) for item in payload.get("target_files", fallback.target_files)[:4]],
            deliverables=[str(item) for item in payload.get("deliverables", fallback.deliverables)[:4]],
            success_criteria=[str(item) for item in payload.get("success_criteria", fallback.success_criteria)[:4]],
            risks_to_check=[str(item) for item in payload.get("risks_to_check", fallback.risks_to_check)[:4]],
        )

    def _heuristic_plan(self, packet: dict[str, Any]) -> TaskPlan:
        manifest = packet.get("workspace_manifest", [])
        return TaskPlan(
            objective=_trim_text(self.context.input_text, limit=180),
            search_patterns=["config", "API_BASE_URL", "timeout"],
            target_files=[item for item in manifest if item.endswith((".py", ".md"))][:3],
            deliverables=["delivery-report.md", "patch-plan.md"],
            success_criteria=[
                "Find the relevant files",
                "Explain the likely root cause",
                "Produce actionable next steps",
            ],
            risks_to_check=["Missing validation", "Unclear ownership", "Insufficient tests"],
        )

    async def _build_project_blueprint(self, packet: dict[str, Any]) -> ProjectBlueprint:
        fallback = self._heuristic_project_blueprint(packet)
        payload = await self._call_json(
            purpose="project_blueprint",
            body={
                "user_request": self.context.input_text,
                "task_brief": packet.get("task_brief", ""),
                "workspace_manifest": packet.get("workspace_manifest", []),
            },
            fallback=fallback.__dict__,
        )
        return ProjectBlueprint(
            project_name=str(payload.get("project_name", fallback.project_name)).strip() or fallback.project_name,
            package_name=str(payload.get("package_name", fallback.package_name)).strip() or fallback.package_name,
            project_type=str(payload.get("project_type", fallback.project_type)).strip() or fallback.project_type,
            summary=str(payload.get("summary", fallback.summary)).strip() or fallback.summary,
            goals=[str(item) for item in payload.get("goals", fallback.goals)[:6]],
            generated_files=[str(item) for item in payload.get("generated_files", fallback.generated_files)],
            verification_commands=[
                str(item) for item in payload.get("verification_commands", fallback.verification_commands)
            ],
        )

    def _heuristic_project_blueprint(self, packet: dict[str, Any]) -> ProjectBlueprint:
        prompt = str(self.context.input_text or "")
        lower = prompt.lower()
        is_cli = "cli" in lower or "command" in lower
        project_name = "generated-cli-app" if is_cli else "generated-service-app"
        package_name = project_name.replace("-", "_")
        project_type = "python_cli" if is_cli else "python_service"
        generated_files = [
            "README.md",
            "pyproject.toml",
            f"src/{package_name}/__init__.py",
            f"src/{package_name}/service.py",
            f"src/{package_name}/cli.py",
            "tests/test_service.py",
            ".gitignore",
        ]
        summary = f"Create a complete {project_type} project from the user request."
        goals = [
            "Scaffold a runnable Python project",
            "Provide package structure and tests",
            "Document how to run and verify the project",
        ]
        verification = ["python -m py_compile src/**/*.py", "pytest -q"]
        _ = packet
        return ProjectBlueprint(
            project_name=project_name,
            package_name=package_name,
            project_type=project_type,
            summary=summary,
            goals=goals,
            generated_files=generated_files,
            verification_commands=verification,
        )

    async def _materialize_project(self, project_root: Path, blueprint: ProjectBlueprint) -> list[str]:
        created: list[str] = []
        files = {
            "README.md": self._project_readme(blueprint),
            "pyproject.toml": self._project_pyproject(blueprint),
            f"src/{blueprint.package_name}/__init__.py": self._project_init(blueprint),
            f"src/{blueprint.package_name}/service.py": self._project_service(blueprint),
            f"src/{blueprint.package_name}/cli.py": self._project_cli(blueprint),
            "tests/test_service.py": self._project_tests(blueprint),
            ".gitignore": "__pycache__/\n.pytest_cache/\n.venv/\n",
        }
        for relative_path, content in files.items():
            target = project_root / relative_path
            await self._write_output(target, content, kind="generated_project_file")
            created.append(target.relative_to(project_root).as_posix())
        return created

    async def _inspect_workspace(self, plan: TaskPlan, packet: dict[str, Any]) -> dict[str, Any]:
        ctx = self.context
        workspace_root = packet["workspace_root"]
        workspace_path = Path(workspace_root).resolve(strict=False)
        files_manifest = await self.call_tool("list_files", {"path": workspace_root, "recursive": True})
        search_tool_id = "ripgrep" if "ripgrep" in ctx.tools else "grep_files"
        matches: list[dict[str, Any]] = []
        for pattern in plan.search_patterns[:3]:
            try:
                result = await self.call_tool(
                    search_tool_id, {"path": workspace_root, "pattern": pattern, "case_sensitive": False}
                )
            except Exception:
                if search_tool_id != "ripgrep" or "grep_files" not in ctx.tools:
                    raise
                result = await self.call_tool(
                    "grep_files", {"path": workspace_root, "pattern": pattern, "case_sensitive": False}
                )
            matches.extend(list(result.get("matches", []))[:8])
        read_candidates: list[str] = [
            str((Path(workspace_root) / path).resolve(strict=False)) for path in plan.target_files
        ]
        for item in matches:
            path = item.get("file")
            if isinstance(path, str) and path:
                resolved = str(Path(path).resolve(strict=False))
                if resolved not in read_candidates:
                    read_candidates.append(resolved)
        read_results = []
        for path in read_candidates[:4]:
            result = await self.call_tool("read_file", {"path": path})
            resolved = Path(result["path"]).resolve(strict=False)
            try:
                display_path = resolved.relative_to(workspace_path).as_posix()
            except ValueError:
                display_path = str(resolved)
            read_results.append(
                {
                    "path": result["path"],
                    "content": _trim_text(result["content"], limit=1200),
                    "size": result.get("size", 0),
                }
            )
            read_results[-1]["display_path"] = display_path
        return {
            "workspace_files": list(files_manifest.get("files", [])),
            "matches": matches[:12],
            "read_results": read_results,
        }

    async def _build_delivery(
        self, plan: TaskPlan, inspection: dict[str, Any], packet: dict[str, Any]
    ) -> DeliveryEnvelope:
        fallback = self._heuristic_delivery(plan, inspection)
        payload = await self._call_json(
            purpose="delivery",
            body={
                "objective": plan.objective,
                "search_patterns": plan.search_patterns,
                "target_files": plan.target_files,
                "inspection": inspection,
                "task_brief": packet.get("task_brief", ""),
            },
            fallback=fallback.__dict__,
        )
        return DeliveryEnvelope(
            summary=str(payload.get("summary", fallback.summary)).strip() or fallback.summary,
            root_cause=str(payload.get("root_cause", fallback.root_cause)).strip() or fallback.root_cause,
            recommended_changes=[
                str(item) for item in payload.get("recommended_changes", fallback.recommended_changes)[:6]
            ],
            tests_to_run=[str(item) for item in payload.get("tests_to_run", fallback.tests_to_run)[:6]],
            risks=[str(item) for item in payload.get("risks", fallback.risks)[:6]],
            next_steps=[str(item) for item in payload.get("next_steps", fallback.next_steps)[:6]],
        )

    def _heuristic_delivery(self, plan: TaskPlan, inspection: dict[str, Any]) -> DeliveryEnvelope:
        matched_files = [item.get("display_path", item["path"]) for item in inspection["read_results"]]
        focus = matched_files[0] if matched_files else "the inspected workspace"
        return DeliveryEnvelope(
            summary=(
                f"Inspected {len(matched_files)} file(s) and prepared a repository delivery report"
                f" for: {plan.objective}"
            ),
            root_cause=(
                f"The likely implementation hotspot is {focus}. "
                "The request needs file-level validation before code changes."
            ),
            recommended_changes=[
                "Tighten validation around the hotspot",
                "Add explicit regression coverage",
                "Document operational fallbacks",
            ],
            tests_to_run=["pytest -q", "targeted regression tests", "manual validation of the reported scenario"],
            risks=["Hidden assumptions in adjacent files", "Missing negative-path coverage"],
            next_steps=["Review the generated report", "Apply a minimal patch", "Run focused validation"],
        )

    def _build_verification(
        self, plan: TaskPlan, inspection: dict[str, Any], delivery: DeliveryEnvelope
    ) -> VerificationEnvelope:
        matched_files = [item.get("display_path", item["path"]) for item in inspection["read_results"]]
        expectations = [
            "All generated delivery artifacts exist",
            "The reported hotspot is reflected in the inspected files",
            "Verification commands cover both regression and smoke validation",
        ]
        if matched_files:
            expectations.append(f"The verification pass should inspect {matched_files[0]}")
        return VerificationEnvelope(
            summary=f"Prepared a verification plan for {plan.objective}",
            commands=delivery.tests_to_run or ["pytest -q"],
            expectations=expectations,
            residual_risks=delivery.risks,
        )

    async def _call_json(self, *, purpose: str, body: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        ctx = self.context
        if ctx.llm_client is None:
            return fallback
        prompt = f"PURPOSE: {purpose}\nReturn JSON only.\nBODY:\n{json.dumps(body, ensure_ascii=False, indent=2)}"
        messages = [
            {"role": "system", "content": self.compose_system_prompt("You are a disciplined coding-delivery planner.")},
            {"role": "user", "content": prompt},
        ]
        raw = await self.call_llm(messages=messages)
        if not str(raw or "").strip():
            decision = await self.repair_empty_response(
                context=ctx,
                messages=messages,
                assistant_content=[],
                stop_reason=None,
                retries=0,
            )
            reason = decision.reason if decision is not None else "LLM returned empty output."
            raise RuntimeError(reason)
        parsed = _safe_json_loads(str(raw))
        return parsed if parsed is not None else fallback

    async def _write_output(self, path: Path, content: str, *, kind: str) -> str:
        await self.call_tool("write_file", {"path": str(path), "content": content, "mode": "w"})
        self.add_artifact(name=path.name, payload=content, kind=kind, metadata={"path": str(path)})
        return str(path)

    def _delivery_report(self, plan: TaskPlan, inspection: dict[str, Any], delivery: DeliveryEnvelope) -> str:
        files = "\n".join(f"- {item.get('display_path', item['path'])}" for item in inspection["read_results"])
        changes = "\n".join(f"- {item}" for item in delivery.recommended_changes)
        tests = "\n".join(f"- {item}" for item in delivery.tests_to_run)
        risks = "\n".join(f"- {item}" for item in delivery.risks)
        return (
            f"# Delivery Report\n\n## Objective\n{plan.objective}\n\n"
            f"## Summary\n{delivery.summary}\n\n"
            f"## Root Cause\n{delivery.root_cause}\n\n"
            f"## Recommended Changes\n{changes}\n\n"
            f"## Tests To Run\n{tests}\n\n"
            f"## Risks\n{risks}\n\n"
            f"## Files Inspected\n{files}\n"
        )

    def _patch_plan(self, plan: TaskPlan, inspection: dict[str, Any], delivery: DeliveryEnvelope) -> str:
        files = "\n".join(f"- {item.get('display_path', item['path'])}" for item in inspection["read_results"])
        changes = "\n".join(f"- {item}" for item in delivery.recommended_changes)
        tests = "\n".join(f"- {item}" for item in delivery.tests_to_run)
        return (
            f"# Patch Plan\n\nObjective: {plan.objective}\n\n"
            f"## Target Files\n{files}\n\n"
            f"## Proposed Change Sequence\n{changes}\n\n"
            f"## Validation\n{tests}\n"
        )

    def _verification_report(self, verification: VerificationEnvelope) -> str:
        return (
            "# Verification Report\n\n"
            f"## Summary\n{verification.summary}\n\n"
            "## Commands\n"
            + "\n".join(f"- {item}" for item in verification.commands)
            + "\n\n## Expectations\n"
            + "\n".join(f"- {item}" for item in verification.expectations)
            + "\n\n## Residual Risks\n"
            + "\n".join(f"- {item}" for item in verification.residual_risks)
            + "\n"
        )

    def _project_readme(self, blueprint: ProjectBlueprint) -> str:
        return (
            f"# {blueprint.project_name}\n\n"
            f"{blueprint.summary}\n\n"
            "## Goals\n"
            + "\n".join(f"- {item}" for item in blueprint.goals)
            + "\n\n## Run\n"
            + f"python -m {blueprint.package_name}.cli --name demo\n"
            + "\n\n## Test\npytest -q\n"
        )

    def _project_pyproject(self, blueprint: ProjectBlueprint) -> str:
        return (
            "[project]\n"
            f'name = "{blueprint.project_name}"\n'
            'version = "0.1.0"\n'
            f'description = "{blueprint.summary}"\n'
            'requires-python = ">=3.11"\n'
            "dependencies = []\n\n"
            "[build-system]\n"
            'requires = ["setuptools>=68.0.0"]\n'
            'build-backend = "setuptools.build_meta"\n\n'
            "[tool.setuptools.packages.find]\n"
            'where = ["src"]\n'
        )

    def _project_init(self, blueprint: ProjectBlueprint) -> str:
        return f'"""Package for {blueprint.project_name}."""\n\n__all__ = ["build_message", "run"]\n'

    def _project_service(self, blueprint: ProjectBlueprint) -> str:
        return (
            "def build_message(name: str) -> str:\n"
            '    clean = (name or "world").strip() or "world"\n'
            f'    return "Hello from {blueprint.project_name}, " + clean + "!"\n\n'
            "def run(name: str) -> str:\n"
            "    return build_message(name)\n"
        )

    def _project_cli(self, blueprint: ProjectBlueprint) -> str:
        return (
            "from __future__ import annotations\n\n"
            "import argparse\n\n"
            f"from {blueprint.package_name}.service import run\n\n"
            "def build_parser() -> argparse.ArgumentParser:\n"
            f'    parser = argparse.ArgumentParser(description="{blueprint.summary}")\n'
            '    parser.add_argument("--name", default="world")\n'
            "    return parser\n\n"
            "def main() -> int:\n"
            "    args = build_parser().parse_args()\n"
            "    print(run(args.name))\n"
            "    return 0\n\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        )

    def _project_tests(self, blueprint: ProjectBlueprint) -> str:
        return (
            "from __future__ import annotations\n\n"
            "import sys\n"
            "from pathlib import Path\n\n"
            'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))\n\n'
            f"from {blueprint.package_name}.service import build_message, run\n\n"
            "def test_build_message_contains_name():\n"
            '    assert "demo" in build_message("demo")\n\n'
            "def test_run_uses_service():\n"
            '    assert run("team").endswith("team!")\n'
        )
