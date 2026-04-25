"""Human-readable, file-backed long-term memory plugin."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openagents.interfaces.capabilities import (
    MEMORY_INJECT,
    MEMORY_RETRIEVE,
    MEMORY_WRITEBACK,
)
from openagents.interfaces.memory import MemoryPlugin
from openagents.interfaces.typed_config import TypedConfigPluginMixin

_ENTRY_RE = re.compile(
    r"^### (?P<id>[\w-]+) · (?P<ts>[\dTZ:+\-.]+)\s*\n"
    r"\*\*Rule:\*\* (?P<rule>.*?)\n"
    r"\*\*Why:\*\* (?P<why>.*?)\n",
    re.MULTILINE | re.DOTALL,
)


class MarkdownMemory(TypedConfigPluginMixin, MemoryPlugin):
    """Human-readable, file-backed long-term memory.

    What:
        Persists user goals, feedback, decisions, references as markdown
        files under ``memory_dir``. Injects each section as a list of entries
        into ``context.memory_view``. Writeback reads
        ``context.state['_pending_memory_writes']`` and appends entries.
    Usage:
        ``{"type": "markdown_memory", "config": {
            "memory_dir": "~/.config/openagents/memory"}}``.
    Depends on:
        Plain filesystem IO; no network. Sections default to
        ``["user_goals", "user_feedback", "decisions", "references"]``.
    """

    class Config(BaseModel):
        memory_dir: str = "~/.config/openagents/memory"
        max_chars_per_section: int = 2000
        sections: list[str] = Field(
            default_factory=lambda: [
                "user_goals",
                "user_feedback",
                "decisions",
                "references",
            ]
        )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            config=config or {},
            capabilities={MEMORY_INJECT, MEMORY_WRITEBACK, MEMORY_RETRIEVE},
        )
        self._init_typed_config()
        self._dir = Path(self.cfg.memory_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---- public API (app-side) -------------------------------------
    def capture(self, category: str, rule: str, reason: str) -> str:
        """Append an entry to the given section file and refresh the index.

        Returns the generated entry ID (8-char hex).
        """
        section = category if category in self.cfg.sections else "user_feedback"
        entry_id = uuid.uuid4().hex[:8]
        timestamp = datetime.now(timezone.utc).isoformat()
        rule_flat = rule.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        reason_flat = (reason or "(no reason given)").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        block = f"### {entry_id} · {timestamp}\n**Rule:** {rule_flat}\n**Why:** {reason_flat}\n\n"
        path = self._dir / f"{section}.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)
        self._refresh_index()
        return entry_id

    def forget(self, entry_id: str) -> bool:
        """Remove the entry with the given ID from whichever section contains it.

        Returns True if the entry was found and removed, False otherwise.
        """
        for section in self.cfg.sections:
            path = self._dir / f"{section}.md"
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            new_content, count = re.subn(
                rf"### {re.escape(entry_id)} · .*?(?=\n### |\Z)",
                "",
                content,
                flags=re.DOTALL,
            )
            if count:
                path.write_text(new_content.lstrip(), encoding="utf-8")
                self._refresh_index()
                return True
        return False

    def list_entries(self, section: str) -> list[dict[str, Any]]:
        """Return all parsed entries for a section (no char budget applied)."""
        return self._parse(section)

    # ---- plugin lifecycle ------------------------------------------
    async def inject(self, context: Any) -> None:
        for section in self.cfg.sections:
            context.memory_view[section] = self._parse(section, max_chars=self.cfg.max_chars_per_section)

    async def compact(self, context: Any) -> None:
        """No-op: MarkdownMemory manages size via _parse max_chars budget."""

    async def writeback(self, context: Any) -> None:
        pending = context.state.get("_pending_memory_writes") or []
        if not pending:
            return
        for entry in pending:
            self.capture(
                category=entry.get("category", "user_feedback"),
                rule=str(entry.get("rule", "")),
                reason=str(entry.get("reason", "")),
            )
        context.state["_pending_memory_writes"] = []

    async def retrieve(self, query: str, context: Any) -> list[dict[str, Any]]:
        q = query.lower()
        out: list[dict[str, Any]] = []
        for section in self.cfg.sections:
            for entry in self._parse(section):
                if q in entry["rule"].lower() or q in entry["reason"].lower():
                    entry_copy = dict(entry)
                    entry_copy["section"] = section
                    out.append(entry_copy)
        return out[:20]

    # ---- helpers ----------------------------------------------------
    def _parse(self, section: str, *, max_chars: int | None = None) -> list[dict[str, Any]]:
        path = self._dir / f"{section}.md"
        if not path.exists():
            return []
        content = path.read_text(encoding="utf-8")
        entries = [
            {
                "id": m.group("id"),
                "timestamp": m.group("ts"),
                "rule": m.group("rule").strip(),
                "reason": m.group("why").strip(),
            }
            for m in _ENTRY_RE.finditer(content)
        ]
        if max_chars is None:
            return entries
        # Keep most-recent entries within char budget (newest entries kept first)
        kept: list[dict[str, Any]] = []
        total = 0
        for entry in reversed(entries):
            size = len(entry["rule"]) + len(entry["reason"])
            if total + size > max_chars and kept:
                break
            kept.append(entry)
            total += size
        kept.reverse()
        return kept

    def _refresh_index(self) -> None:
        lines = ["# Memory Index\n"]
        for section in self.cfg.sections:
            path = self._dir / f"{section}.md"
            count = len(self._parse(section)) if path.exists() else 0
            lines.append(f"- [{section}]({section}.md) — {count} entries")
        (self._dir / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
