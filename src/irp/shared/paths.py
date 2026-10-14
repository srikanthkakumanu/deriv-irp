"""Path display helpers (pure)."""
from __future__ import annotations

from pathlib import Path


def display_path(path: str | Path, base: str | Path | None = None) -> str:
    """Path relative to ``base`` (default: current working directory) when it lies inside it,
    otherwise the absolute path. Always uses forward slashes."""
    absolute = Path(path).resolve()
    root = Path(base).resolve() if base is not None else Path.cwd().resolve()
    try:
        return absolute.relative_to(root).as_posix()
    except ValueError:
        return absolute.as_posix()


def split_section(name: str) -> tuple[str, str | None]:
    """'incident_analysis.json#stage2.INC-001' -> ('incident_analysis.json', 'stage2.INC-001')."""
    base, sep, section = name.partition("#")
    return base, (section if sep else None)


__all__ = ["display_path", "split_section"]
