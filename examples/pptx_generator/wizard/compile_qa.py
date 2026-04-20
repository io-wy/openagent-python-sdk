"""Stage 7 wizard step — 4 sub-step compile + QA with loopback."""

from __future__ import annotations

import contextlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagents.cli.wizard import StepResult, Wizard

from ..state import DeckProject, SlideIR
from ._qa_scan import QAReport, scan_placeholders

SubStepStatus = str  # "pending" | "running" | "ok" | "skipped" | "failed"


@dataclass
class CompileQAWizardStep:
    shell_tool: Any
    output_root: Path
    templates_dir: Path
    title: str = "compile"
    description: str = "Render JS, run PptxGenJS, QA via MarkItDown + placeholder scan."

    async def render(self, console: Any, project: DeckProject) -> StepResult:
        out_dir = Path(self.output_root) / project.slug / "slides"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "output").mkdir(exist_ok=True)

        steps: dict[str, SubStepStatus] = {
            "write": "pending",
            "npm install": "pending",
            "node compile.js": "pending",
            "qa scan": "pending",
        }

        def mark(name: str, status: SubStepStatus) -> None:
            steps[name] = status
            if console is not None:
                with contextlib.suppress(Exception):
                    self._print_status(console, steps)

        # 1. Write JS files
        mark("write", "running")
        for slide in project.slides:
            self._write_slide_file(out_dir, slide)
        (out_dir / "compile.js").write_text(self._compile_script(project), encoding="utf-8")
        mark("write", "ok")

        # 2. npm install (skipped if node_modules exists)
        node_modules = out_dir / "node_modules" / "pptxgenjs"
        pkg = out_dir / "package.json"
        if not pkg.exists():
            pkg.write_text(
                json.dumps(
                    {
                        "name": f"deck-{project.slug}",
                        "private": True,
                        "dependencies": {"pptxgenjs": "^3.12.0"},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        if node_modules.exists():
            mark("npm install", "skipped")
        else:
            mark("npm install", "running")
            r = await self.shell_tool.invoke(
                {"command": ["npm", "install"], "cwd": str(out_dir)},
                context=None,
            )
            mark("npm install", "ok" if _exit_ok(r) else "failed")

        # 3. node compile.js
        mark("node compile.js", "running")
        r = await self.shell_tool.invoke(
            {"command": ["node", "compile.js"], "cwd": str(out_dir)},
            context=None,
        )
        compile_ok = _exit_ok(r)
        mark("node compile.js", "ok" if compile_ok else "failed")

        # 4. markitdown → QA scan
        pptx = out_dir / "output" / "presentation.pptx"
        md_path = out_dir / "output" / "presentation.md"
        markitdown_ran = False
        md_text: str | None = None
        mark("qa scan", "running")
        if shutil.which("markitdown") is not None and compile_ok:
            r = await self.shell_tool.invoke(
                {"command": ["markitdown", str(pptx), "-o", str(md_path)]},
                context=None,
            )
            if _exit_ok(r) and md_path.exists():
                md_text = md_path.read_text(encoding="utf-8")
                markitdown_ran = True

        qa_report = await scan_placeholders(
            md_text,
            md_path=str(md_path) if markitdown_ran else None,
            shell_tool=self.shell_tool,
            markitdown_ran=markitdown_ran,
        )
        mark("qa scan", "ok" if not qa_report.matches else "failed")

        issue = (not compile_ok) or qa_report.matches
        if issue:
            action = await self._prompt_loopback(qa_report, compile_ok)
            if action == "go back to slides":
                project.stage = "slides"
                return StepResult(status="retry")
            if action == "abort":
                return StepResult(status="aborted")

        project.stage = "done"
        return StepResult(status="completed")

    async def _prompt_loopback(self, qa: QAReport, compile_ok: bool) -> str:
        choices: list[str] = []
        if not compile_ok:
            choices.append("go back to slides")
        bad_idx = sorted({m.slide_index for m in qa.matches if m.slide_index})
        for idx in bad_idx:
            choices.append(f"hand-edit slide {idx}")
        if "go back to slides" not in choices:
            choices.append("go back to slides")
        choices += ["accept and finish", "abort"]
        return await Wizard.select("QA issues detected. Next action?", choices=choices, default=choices[0])

    @staticmethod
    def _print_status(console: Any, steps: dict[str, SubStepStatus]) -> None:
        from rich.table import Table

        table = Table(title="Compile / QA")
        table.add_column("Step")
        table.add_column("Status")
        palette = {
            "pending": "dim",
            "running": "cyan",
            "ok": "green",
            "skipped": "yellow",
            "failed": "red",
        }
        for name, status in steps.items():
            color = palette.get(status, "white")
            table.add_row(name, f"[{color}]{status}[/{color}]")
        console.print(table)

    def _write_slide_file(self, out_dir: Path, slide: SlideIR) -> None:
        filename = f"slide-{slide.index:02d}.js"
        path = out_dir / filename
        if slide.type == "freeform" and slide.freeform_js:
            path.write_text(slide.freeform_js, encoding="utf-8")
            return
        template = (self.templates_dir / f"{slide.type}.js").read_text(encoding="utf-8")
        content = (
            "const base = (function() {\n"
            "  var module = { exports: {} };\n"
            f"{template}\n"
            "  return module.exports;\n"
            "})();\n"
            f"const slots = {json.dumps(slide.slots, ensure_ascii=False)};\n"
            "function createSlide(pres, theme) { return base.createSlide(pres, theme, slots); }\n"
            "module.exports = { createSlide };\n"
        )
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _compile_script(project: DeckProject) -> str:
        theme_palette = project.theme.palette.model_dump() if project.theme else {}
        title = project.intent.topic if project.intent else "Deck"
        requires = "\n".join(f'  require("./slide-{s.index:02d}.js").createSlide(pres, theme);' for s in project.slides)
        return (
            'const pptxgen = require("pptxgenjs");\n\n'
            "async function main() {\n"
            "  const pres = new pptxgen();\n"
            '  pres.layout = "LAYOUT_16x9";\n'
            f"  pres.title = {json.dumps(title)};\n"
            f"  const theme = {json.dumps(theme_palette)};\n"
            f"{requires}\n"
            '  await pres.writeFile({ fileName: "./output/presentation.pptx" });\n'
            "}\n\n"
            "main().catch((err) => { console.error(err); process.exitCode = 1; });\n"
        )


def _exit_ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    code = result.get("exit_code")
    return code is None or code == 0
