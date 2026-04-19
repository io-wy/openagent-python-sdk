from __future__ import annotations

from types import SimpleNamespace

import pytest

from openagents.cli.wizard import StepResult, Wizard


class _DummyStep:
    def __init__(self, title: str, result_status: str):
        self.title = title
        self.description = ""
        self._status = result_status
        self.called = False

    async def render(self, console, project):
        self.called = True
        return StepResult(status=self._status, data={"title": self.title})


@pytest.mark.asyncio
async def test_wizard_runs_all_steps_on_happy_path():
    steps = [_DummyStep("a", "completed"), _DummyStep("b", "completed")]
    project = SimpleNamespace(stage="a")
    wizard = Wizard(steps=steps, project=project)
    outcome = await wizard.run()
    assert outcome == "completed"
    assert all(s.called for s in steps)


@pytest.mark.asyncio
async def test_wizard_stops_on_abort():
    steps = [_DummyStep("a", "aborted"), _DummyStep("b", "completed")]
    project = SimpleNamespace(stage="a")
    wizard = Wizard(steps=steps, project=project)
    outcome = await wizard.run()
    assert outcome == "aborted"
    assert steps[0].called is True
    assert steps[1].called is False


@pytest.mark.asyncio
async def test_wizard_resume_skips_earlier_steps():
    steps = [_DummyStep("a", "completed"), _DummyStep("b", "completed")]
    project = SimpleNamespace(stage="b")
    wizard = Wizard(steps=steps, project=project)
    outcome = await wizard.resume(from_step="b")
    assert outcome == "completed"
    assert steps[0].called is False
    assert steps[1].called is True


@pytest.mark.asyncio
async def test_wizard_retry_reruns_current_step():
    class _RetryThenOk:
        def __init__(self):
            self.title = "x"
            self.description = ""
            self.calls = 0

        async def render(self, console, project):
            self.calls += 1
            return StepResult(status="retry" if self.calls == 1 else "completed")

    step = _RetryThenOk()
    project = SimpleNamespace(stage="x")
    wizard = Wizard(steps=[step], project=project)
    outcome = await wizard.run()
    assert outcome == "completed"
    assert step.calls == 2


@pytest.mark.asyncio
async def test_wizard_aborts_after_max_retries():
    class _AlwaysRetry:
        def __init__(self):
            self.title = "x"
            self.description = ""
            self.calls = 0

        async def render(self, console, project):
            self.calls += 1
            return StepResult(status="retry")

    step = _AlwaysRetry()
    project = SimpleNamespace(stage="x")
    wizard = Wizard(steps=[step], project=project)
    outcome = await wizard.run(max_retries_per_step=3)
    assert outcome == "aborted"
    assert step.calls == 3


@pytest.mark.asyncio
async def test_wizard_resume_unknown_title_raises():
    steps = [_DummyStep("a", "completed")]
    project = SimpleNamespace(stage="a")
    wizard = Wizard(steps=steps, project=project)
    with pytest.raises(ValueError, match="No step with title"):
        await wizard.resume(from_step="nonexistent")
