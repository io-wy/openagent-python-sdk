from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel


class CheckStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    OUTDATED = "outdated"
    ERROR = "error"


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    detail: str
    fix_hint: str | None = None
    get_url: str | None = None


class EnvironmentReport(BaseModel):
    checks: list[CheckResult]
    missing_required: list[str]
    missing_optional: list[str]
    auto_fixable: list[str]


class EnvironmentCheck(Protocol):
    name: str
    required: bool

    async def check(self) -> CheckResult: ...


@dataclass
class PythonVersionCheck:
    min_version: str = "3.10"
    name: str = "python"
    required: bool = True

    async def check(self) -> CheckResult:
        actual = f"{sys.version_info.major}.{sys.version_info.minor}"
        need = tuple(int(x) for x in self.min_version.split("."))
        have = (sys.version_info.major, sys.version_info.minor)
        if have >= need:
            return CheckResult(name=self.name, status=CheckStatus.OK, detail=actual)
        return CheckResult(
            name=self.name,
            status=CheckStatus.OUTDATED,
            detail=f"have {actual}, need >= {self.min_version}",
            fix_hint="Upgrade Python via your package manager or pyenv.",
        )


@dataclass
class NodeVersionCheck:
    min_version: str = "18"
    name: str = "node"
    required: bool = True

    async def check(self) -> CheckResult:
        try:
            out = await asyncio.to_thread(
                subprocess.check_output,
                ["node", "--version"],
                text=True,
                timeout=5,
            )
            out = out.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return CheckResult(
                name=self.name,
                status=CheckStatus.MISSING,
                detail="node not on PATH",
                fix_hint="Install Node.js >= 18",
                get_url="https://nodejs.org/",
            )
        m = re.match(r"v(\d+)", out)
        have = int(m.group(1)) if m else 0
        need = int(self.min_version)
        if have >= need:
            return CheckResult(name=self.name, status=CheckStatus.OK, detail=out)
        return CheckResult(
            name=self.name,
            status=CheckStatus.OUTDATED,
            detail=f"have {out}, need >= {self.min_version}",
            fix_hint="Upgrade Node.js",
            get_url="https://nodejs.org/",
        )


@dataclass
class NpmCheck:
    name: str = "npm"
    required: bool = True

    async def check(self) -> CheckResult:
        path = shutil.which("npm")
        if path:
            return CheckResult(name=self.name, status=CheckStatus.OK, detail=path)
        return CheckResult(
            name=self.name,
            status=CheckStatus.MISSING,
            detail="npm not on PATH",
            fix_hint="Install Node.js (ships with npm)",
            get_url="https://nodejs.org/",
        )


@dataclass
class CliBinaryCheck:
    name: str
    install_hint: str
    get_url: str | None = None
    required: bool = True

    async def check(self) -> CheckResult:
        path = shutil.which(self.name)
        if path:
            return CheckResult(name=self.name, status=CheckStatus.OK, detail=path)
        return CheckResult(
            name=self.name,
            status=CheckStatus.MISSING,
            detail=f"{self.name} not on PATH",
            fix_hint=self.install_hint,
            get_url=self.get_url,
        )


@dataclass
class EnvVarCheck:
    name: str
    required: bool
    description: str
    get_url: str | None

    async def check(self) -> CheckResult:
        if os.environ.get(self.name):
            return CheckResult(name=self.name, status=CheckStatus.OK, detail="set")
        return CheckResult(
            name=self.name,
            status=CheckStatus.MISSING,
            detail=self.description or "not set",
            fix_hint=f"export {self.name}=...",
            get_url=self.get_url,
        )


class EnvironmentDoctor:
    """Aggregates environment checks and guides interactive fixes."""

    def __init__(
        self,
        checks: list[EnvironmentCheck],
        dotenv_paths: list[Path],
    ) -> None:
        self._checks = checks
        self._dotenv_paths = [Path(p) for p in dotenv_paths]

    async def run(self) -> EnvironmentReport:
        results: list[CheckResult] = []
        missing_required: list[str] = []
        missing_optional: list[str] = []
        for check in self._checks:
            result = await check.check()
            results.append(result)
            if result.status in (CheckStatus.MISSING, CheckStatus.OUTDATED):
                if getattr(check, "required", True):
                    missing_required.append(check.name)
                else:
                    missing_optional.append(check.name)
        return EnvironmentReport(
            checks=results,
            missing_required=missing_required,
            missing_optional=missing_optional,
            auto_fixable=[],
        )

    def persist_env(
        self,
        key: str,
        value: str,
        level: Literal["user", "project"] = "project",
    ) -> Path:
        if not self._dotenv_paths:
            raise RuntimeError("no dotenv_paths configured")
        if not key or "\n" in key or "\r" in key or "=" in key:
            raise ValueError(f"invalid env key: {key!r}")
        # Newlines and CRs in a value corrupt dotenv files; escape them to literal backslash sequences.
        value = value.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
        path = self._dotenv_paths[0] if level == "project" else self._dotenv_paths[-1]
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
        os.environ[key] = value
        return path

    async def interactive_fix(self, report: EnvironmentReport, console: Any) -> EnvironmentReport:
        """App-side UI glue; deliberately left minimal here so Wizard can own the UI."""
        return report
