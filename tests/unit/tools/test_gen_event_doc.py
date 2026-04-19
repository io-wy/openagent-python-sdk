"""WP5 backfill: cover openagents.tools.gen_event_doc.write_doc / main."""

from __future__ import annotations

from pathlib import Path

from openagents.tools.gen_event_doc import main, render_doc, write_doc


def test_write_doc_creates_file(tmp_path: Path):
    target = tmp_path / "subdir" / "out.md"
    write_doc(target)
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert body == render_doc()


def test_main_with_explicit_out_writes_and_returns_zero(tmp_path: Path, capsys):
    target = tmp_path / "via-main.md"
    rc = main(["--out", str(target)])
    assert rc == 0
    assert target.exists()
    captured = capsys.readouterr()
    assert "wrote" in captured.out
    assert str(target) in captured.out


def test_main_default_target_path_returns_repo_docs(tmp_path: Path, monkeypatch):
    """Smoke: argparse default resolves to docs/event-taxonomy.md (don't actually overwrite)."""
    # Just import-check the helper; we don't write to repo from a test.
    from openagents.tools.gen_event_doc import _default_target

    target = _default_target()
    assert target.name == "event-taxonomy.md"
    assert target.parent.name == "docs"
