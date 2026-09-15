"""Local computer commands backed by a SQLite index: open apps, find files and
folders, spot duplicates. Ranked by frecency so the things you use most come
back first and start instantly."""

from .service import LocalSystem

__all__ = ["LocalSystem"]
