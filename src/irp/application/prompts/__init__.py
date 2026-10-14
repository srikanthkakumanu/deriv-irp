"""Prompt templates (data is injected at runtime; nothing is hardcoded from fixtures)."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")
