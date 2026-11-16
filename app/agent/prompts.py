"""The prompts of the two LLM steps. The reader prompt carries the generated menu (so it always matches the library)."""
from __future__ import annotations

from pathlib import Path

from app.menu import menu_text

PROMPT_DIR = Path(__file__).parent / "prompts"
SKILL = Path(__file__).parent / "skills" / "cad_engineer.md"


def skill_text() -> str:
    """The CAD engineer skill (app/agent/skills/cad_engineer.md): how to read a drawing, recognise the part family, choose the features and
    use the tools. Both agents read it: the one that writes the build list and the SolidWorks agent."""
    return SKILL.read_text(encoding="utf-8") if SKILL.is_file() else ""


def reader_system() -> str:
    return skill_text() + "\n\n" + (PROMPT_DIR / "reader.md").read_text(encoding="utf-8").replace("{{MENU}}", menu_text())


def repair_system() -> str:
    return (PROMPT_DIR / "repair.md").read_text(encoding="utf-8")
