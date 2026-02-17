"""Theme stylesheets for the main window."""
from __future__ import annotations

from pathlib import Path

THEMES_DIR = Path(__file__).resolve().parent


def load_theme(name: str = "dark") -> str:
    """Load a theme QSS file by name. Returns the stylesheet string."""
    path = THEMES_DIR / f"{name}.qss"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
