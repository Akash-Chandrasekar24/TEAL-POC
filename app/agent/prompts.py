"""The prompts of the two LLM steps. The reader prompt carries the generated menu (so it always matches the library)."""
from __future__ import annotations

from pathlib import Path

from app.menu import menu_text

PROMPT_DIR = Path(__file__).parent / "prompts"


def reader_system() -> str:
    return (PROMPT_DIR / "reader.md").read_text(encoding="utf-8").replace("{{MENU}}", menu_text())


def repair_system() -> str:
    return (PROMPT_DIR / "repair.md").read_text(encoding="utf-8")
