# tests/unit/test_pptx_templates.py
from pathlib import Path

TEMPLATE_DIR = Path("examples/pptx_generator/templates")
TEMPLATE_NAMES = ["cover", "agenda", "content", "transition", "closing"]


def test_templates_exist():
    for name in TEMPLATE_NAMES:
        assert (TEMPLATE_DIR / f"{name}.js").exists(), f"missing {name}.js"


def test_templates_export_createSlide():
    for name in TEMPLATE_NAMES:
        text = (TEMPLATE_DIR / f"{name}.js").read_text(encoding="utf-8")
        assert "createSlide" in text
        assert "module.exports" in text


def test_cover_consumes_title_slot():
    text = (TEMPLATE_DIR / "cover.js").read_text(encoding="utf-8")
    assert "slots.title" in text


def test_content_supports_block_kinds():
    text = (TEMPLATE_DIR / "content.js").read_text(encoding="utf-8")
    for kind in ("bullets", "two_column", "callout"):
        assert kind in text
