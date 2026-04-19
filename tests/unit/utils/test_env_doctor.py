from __future__ import annotations

import pytest

from openagents.utils.env_doctor import (
    CheckStatus,
    CliBinaryCheck,
    EnvironmentDoctor,
    EnvVarCheck,
    PythonVersionCheck,
)


@pytest.mark.asyncio
async def test_python_version_ok():
    check = PythonVersionCheck(min_version="3.8")
    result = await check.check()
    assert result.status == CheckStatus.OK


@pytest.mark.asyncio
async def test_python_version_outdated():
    check = PythonVersionCheck(min_version="99.0")
    result = await check.check()
    assert result.status == CheckStatus.OUTDATED
    assert "3." in result.detail


@pytest.mark.asyncio
async def test_env_var_check_missing(monkeypatch):
    monkeypatch.delenv("FOO_X1", raising=False)
    check = EnvVarCheck(
        name="FOO_X1", required=True,
        description="my var", get_url="https://get.example",
    )
    result = await check.check()
    assert result.status == CheckStatus.MISSING
    assert result.get_url == "https://get.example"


@pytest.mark.asyncio
async def test_env_var_check_present(monkeypatch):
    monkeypatch.setenv("FOO_X2", "v")
    check = EnvVarCheck(name="FOO_X2", required=True, description="", get_url=None)
    result = await check.check()
    assert result.status == CheckStatus.OK


@pytest.mark.asyncio
async def test_cli_binary_check_missing(monkeypatch):
    # Fake binary that never resolves on PATH
    check = CliBinaryCheck(
        name="definitely-not-real-binary-xyz",
        install_hint="pip install xyz",
        get_url=None,
    )
    result = await check.check()
    assert result.status == CheckStatus.MISSING


@pytest.mark.asyncio
async def test_doctor_aggregates(monkeypatch):
    monkeypatch.setenv("Y1", "v")
    monkeypatch.delenv("Y2", raising=False)
    doctor = EnvironmentDoctor(
        checks=[
            EnvVarCheck(name="Y1", required=True, description="", get_url=None),
            EnvVarCheck(name="Y2", required=True, description="", get_url=None),
            EnvVarCheck(name="Y3", required=False, description="", get_url=None),
        ],
        dotenv_paths=[],
    )
    report = await doctor.run()
    assert "Y2" in report.missing_required
    assert "Y3" in report.missing_optional
    assert "Y1" not in report.missing_required


def test_persist_env_writes_dotenv(tmp_path):
    doctor = EnvironmentDoctor(checks=[], dotenv_paths=[tmp_path / ".env"])
    path = doctor.persist_env("TEST_KEY", "value with = and space", level="project")
    assert path == tmp_path / ".env"
    text = path.read_text(encoding="utf-8")
    assert "TEST_KEY=" in text
    assert "value with = and space" in text


def test_persist_env_overwrites_existing_key(tmp_path):
    p = tmp_path / ".env"
    p.write_text("TEST_KEY=old\nOTHER=keep\n", encoding="utf-8")
    doctor = EnvironmentDoctor(checks=[], dotenv_paths=[p])
    doctor.persist_env("TEST_KEY", "new", level="project")
    lines = p.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("TEST_KEY=new") for line in lines)
    assert any(line.startswith("OTHER=keep") for line in lines)
