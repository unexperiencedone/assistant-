"""Where captured material lands before anyone decides what it is for.

One folder, flat, with timestamped names, so a recording from the phone and a screen
capture from the laptop sit side by side and sort into the order they happened. Nothing
in here is ever deleted automatically: footage is the one thing you cannot re-shoot.
"""

from __future__ import annotations

import time
from pathlib import Path

STAMP = "%Y%m%d-%H%M%S"


def folder(base: Path) -> Path:
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    return base


def new_path(base: Path, kind: str, suffix: str) -> Path:
    """A fresh, non-colliding path: `screen-20260922-193000.mp4`."""
    stamp = time.strftime(STAMP)
    path = folder(base) / f"{kind}-{stamp}{suffix}"
    count = 2
    while path.exists():
        path = folder(base) / f"{kind}-{stamp}-{count}{suffix}"
        count += 1
    return path


def recent(base: Path, limit: int = 10) -> list[Path]:
    """Newest first. Used by "edit the last thing I recorded"."""
    files = [p for p in folder(base).iterdir() if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def latest(base: Path, suffixes: tuple[str, ...] = (".mp4", ".mkv", ".mov", ".jpg", ".png")) -> Path | None:
    for path in recent(base, limit=50):
        if path.suffix.lower() in suffixes:
            return path
    return None


def describe(path: Path) -> str:
    """A spoken line about one file: name and size, no path read out loud."""
    try:
        size = path.stat().st_size
    except OSError:
        return f"{path.name}, which I can no longer find"
    if size >= 1024 * 1024:
        return f"{path.name}, {size / (1024 * 1024):.1f} megabytes"
    return f"{path.name}, {max(1, size // 1024)} kilobytes"
