from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from examples.pptx_generator.state import DeckProject
from examples.pptx_generator.wizard.env import EnvDoctorWizardStep
from openagents.utils.env_doctor import CheckResult, CheckStatus, EnvironmentReport


def _project():
    return DeckProject(slug="x", created_at=datetime.now(timezone.utc), stage="env")


@pytest.mark.asyncio
async def test_all_ok_transitions_to_research():
    doctor = MagicMock()
    doctor.run = AsyncMock(
        return_value=EnvironmentReport(
            checks=[CheckResult(name="python", status=CheckStatus.OK, detail="3.12")],
            missing_required=[],
            missing_optional=[],
            auto_fixable=[],
        )
    )
    step = EnvDoctorWizardStep(doctor=doctor)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    assert project.stage == "research"


@pytest.mark.asyncio
async def test_missing_required_key_prompts_and_persists(monkeypatch):
    doctor = MagicMock()
    doctor.run = AsyncMock(
        return_value=EnvironmentReport(
            checks=[
                CheckResult(name="LLM_API_KEY", status=CheckStatus.MISSING, detail="not set", get_url="https://example")
            ],
            missing_required=["LLM_API_KEY"],
            missing_optional=[],
            auto_fixable=[],
        )
    )
    doctor.persist_env = MagicMock(return_value="/tmp/.env")
    monkeypatch.setattr("examples.pptx_generator.wizard.env.Wizard.password", AsyncMock(return_value="sk-xxx"))
    step = EnvDoctorWizardStep(doctor=doctor)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    doctor.persist_env.assert_called_once()
    args = doctor.persist_env.call_args
    assert args.args[0] == "LLM_API_KEY"
    assert args.args[1] == "sk-xxx"


@pytest.mark.asyncio
async def test_missing_required_empty_password_aborts(monkeypatch):
    doctor = MagicMock()
    doctor.run = AsyncMock(
        return_value=EnvironmentReport(
            checks=[CheckResult(name="LLM_API_KEY", status=CheckStatus.MISSING, detail="")],
            missing_required=["LLM_API_KEY"],
            missing_optional=[],
            auto_fixable=[],
        )
    )
    monkeypatch.setattr("examples.pptx_generator.wizard.env.Wizard.password", AsyncMock(return_value=""))
    step = EnvDoctorWizardStep(doctor=doctor)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "aborted"
    doctor.persist_env.assert_not_called()


@pytest.mark.asyncio
async def test_missing_required_non_key_asks_confirm(monkeypatch):
    doctor = MagicMock()
    doctor.run = AsyncMock(
        return_value=EnvironmentReport(
            checks=[
                CheckResult(
                    name="markitdown",
                    status=CheckStatus.MISSING,
                    detail="not on PATH",
                    fix_hint="pip install markitdown",
                )
            ],
            missing_required=["markitdown"],
            missing_optional=[],
            auto_fixable=[],
        )
    )
    monkeypatch.setattr("examples.pptx_generator.wizard.env.Wizard.confirm", AsyncMock(return_value=False))
    step = EnvDoctorWizardStep(doctor=doctor)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "aborted"


@pytest.mark.asyncio
async def test_missing_optional_user_declines(monkeypatch):
    doctor = MagicMock()
    doctor.run = AsyncMock(
        return_value=EnvironmentReport(
            checks=[CheckResult(name="TAVILY_API_KEY", status=CheckStatus.MISSING, detail="")],
            missing_required=[],
            missing_optional=["TAVILY_API_KEY"],
            auto_fixable=[],
        )
    )
    monkeypatch.setattr("examples.pptx_generator.wizard.env.Wizard.confirm", AsyncMock(return_value=False))
    step = EnvDoctorWizardStep(doctor=doctor)
    project = _project()
    result = await step.render(console=None, project=project)
    assert result.status == "completed"
    doctor.persist_env.assert_not_called()
