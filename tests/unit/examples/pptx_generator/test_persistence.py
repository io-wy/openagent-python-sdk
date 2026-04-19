"""Unit tests for examples/pptx_generator/persistence.py."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from examples.pptx_generator.persistence import (
    ProjectCorruptedError,
    backup_path,
    load_project,
    project_path,
    restore_from_backup,
    save_project,
)
from examples.pptx_generator.state import DeckProject


def _mk(slug: str, stage: str = "intent") -> DeckProject:
    return DeckProject(
        slug=slug,
        created_at=datetime(2026, 4, 19, tzinfo=timezone.utc),
        stage=stage,
    )


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    p = _mk("demo")
    save_project(p, root=tmp_path)
    loaded = load_project("demo", root=tmp_path)
    assert loaded.slug == "demo"
    assert loaded.stage == "intent"


def test_first_save_creates_no_backup(tmp_path: Path) -> None:
    p = _mk("demo")
    save_project(p, root=tmp_path)
    assert not backup_path("demo", root=tmp_path).exists()


def test_second_save_rotates_backup(tmp_path: Path) -> None:
    save_project(_mk("demo", "intent"), root=tmp_path)
    save_project(_mk("demo", "env"), root=tmp_path)
    assert backup_path("demo", root=tmp_path).exists()
    assert load_project("demo", root=tmp_path).stage == "env"


def test_backup_holds_prior_content(tmp_path: Path) -> None:
    save_project(_mk("demo", "intent"), root=tmp_path)
    save_project(_mk("demo", "theme"), root=tmp_path)
    restored = restore_from_backup("demo", root=tmp_path)
    assert restored.stage == "intent"


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_project("ghost", root=tmp_path)


def test_invalid_json_raises_corrupted(tmp_path: Path) -> None:
    path = project_path("demo", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json {", encoding="utf-8")
    with pytest.raises(ProjectCorruptedError) as exc:
        load_project("demo", root=tmp_path)
    assert "invalid JSON" in exc.value.detail


def test_schema_violation_raises_corrupted(tmp_path: Path) -> None:
    path = project_path("demo", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"slug": "demo"}', encoding="utf-8")
    with pytest.raises(ProjectCorruptedError) as exc:
        load_project("demo", root=tmp_path)
    assert "schema validation failed" in exc.value.detail


def test_restore_missing_backup_raises(tmp_path: Path) -> None:
    save_project(_mk("demo"), root=tmp_path)
    with pytest.raises(FileNotFoundError):
        restore_from_backup("demo", root=tmp_path)


def test_restore_after_corruption(tmp_path: Path) -> None:
    save_project(_mk("demo", "intent"), root=tmp_path)
    save_project(_mk("demo", "theme"), root=tmp_path)

    project_path("demo", root=tmp_path).write_text("junk", encoding="utf-8")
    with pytest.raises(ProjectCorruptedError):
        load_project("demo", root=tmp_path)

    restored = restore_from_backup("demo", root=tmp_path)
    assert restored.stage == "intent"


def test_atomic_write_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_replace(src: str, dst: str) -> None:
        raise OSError("boom")

    monkeypatch.setattr(os, "replace", fake_replace)
    with pytest.raises(OSError):
        save_project(_mk("demo"), root=tmp_path)
    # target file was never created because os.replace raised before the final rename
    assert not project_path("demo", root=tmp_path).exists()
