"""Find duplicate files without hashing everything.

Three narrowing passes, each only over the survivors of the last:
  1. group by exact size            (free: from the index or one stat)
  2. hash first + last 64 KB        (catches most non-duplicates cheaply)
  3. full BLAKE2b hash, 1 MB chunks (proof)
Hashes are cached in SQLite keyed by path and validated by size + mtime, so a
second run over the same folder is nearly instant. Nothing is ever deleted here.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import Database

EDGE = 64 * 1024
CHUNK = 1024 * 1024

log = logging.getLogger("nova.duplicates")


@dataclass
class DuplicateGroup:
    size: int
    paths: list[str]

    @property
    def wasted(self) -> int:
        return self.size * (len(self.paths) - 1)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def parse_size(text: str) -> int:
    text = text.strip().upper().replace(" ", "")
    for unit, mult in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10), ("B", 1)):
        if text.endswith(unit):
            return int(float(text[: -len(unit)]) * mult)
    return int(text)


class DuplicateFinder:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.skipped = 0  # unreadable files in the last scan

    def find(
        self,
        root: Path,
        min_size: int = 1024,
        use_index: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> list[DuplicateGroup]:
        self.skipped = 0  # files that couldn't be read: "none found" shouldn't hide them
        by_size: dict[int, list[str]] = defaultdict(list)
        for path, size in self._files(root, min_size, use_index):
            by_size[size].append(path)
        candidates = {s: p for s, p in by_size.items() if len(p) > 1}
        if progress:
            progress(f"{sum(map(len, candidates.values()))} files share a size with another file; hashing...")

        groups: list[DuplicateGroup] = []
        for size, paths in candidates.items():
            by_partial = self._group(paths, size, full=False)
            for same_partial in by_partial:
                # Small files: the partial hash already covered the whole file.
                confirmed = [same_partial] if size <= 2 * EDGE else self._group(same_partial, size, full=True)
                groups += [DuplicateGroup(size, sorted(g)) for g in confirmed]
        groups.sort(key=lambda g: g.wasted, reverse=True)
        return groups

    # -- helpers ----------------------------------------------------------------
    def _files(self, root: Path, min_size: int, use_index: bool):
        root_s = str(root.resolve())
        conn = self.db.connect()
        if use_index and conn.execute("SELECT 1 FROM dirs WHERE path = ?", (root_s,)).fetchone():
            sep = "\\" if "\\" in root_s else "/"
            yield from conn.execute(
                "SELECT path, size FROM items WHERE kind = 'file' AND size >= ? AND path >= ? AND path < ?",
                (min_size, root_s + sep, root_s + chr(ord(sep) + 1)),
            )
            return
        for dirpath, dirnames, filenames in os.walk(root_s):
            dirnames[:] = [d for d in dirnames if d.lower() not in {"node_modules", ".git", "__pycache__", ".venv"}]
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    size = os.stat(path, follow_symlinks=False).st_size
                except OSError:
                    self.skipped += 1
                    continue
                if size >= min_size:
                    yield path, size

    def _group(self, paths: list[str], size: int, full: bool) -> list[list[str]]:
        buckets: dict[str, list[str]] = defaultdict(list)
        for path in paths:
            digest = self._hash(path, size, full)
            if digest:
                buckets[digest].append(path)
        return [g for g in buckets.values() if len(g) > 1]

    def _hash(self, path: str, size: int, full: bool) -> str | None:
        try:
            st = os.stat(path)
        except OSError:
            self.skipped += 1
            return None
        if st.st_size != size:
            return None  # changed since indexing
        column = "full" if full else "partial"
        conn = self.db.connect()
        row = conn.execute(f"SELECT {column} FROM hashes WHERE path = ? AND size = ? AND mtime = ?",
                           (path, size, st.st_mtime)).fetchone()
        if row and row[0]:
            return row[0]
        try:
            digest = _full_hash(path) if full else _edge_hash(path, size)
        except OSError as exc:
            self.skipped += 1
            log.warning("couldn't read %s: %s", path, exc)
            return None
        with conn:
            # A cached row for an older version of the file is useless: drop it first.
            conn.execute("DELETE FROM hashes WHERE path = ? AND (size != ? OR mtime != ?)", (path, size, st.st_mtime))
            conn.execute(
                f"""INSERT INTO hashes (path, size, mtime, {column}) VALUES (?, ?, ?, ?)
                    ON CONFLICT (path) DO UPDATE SET {column} = excluded.{column}""",
                (path, size, st.st_mtime, digest),
            )
        return digest


def _edge_hash(path: str, size: int) -> str:
    h = hashlib.blake2b(digest_size=20)
    h.update(size.to_bytes(8, "little"))
    with open(path, "rb") as fh:
        h.update(fh.read(EDGE))
        if size > 2 * EDGE:
            fh.seek(-EDGE, os.SEEK_END)
            h.update(fh.read(EDGE))
    return h.hexdigest()


def _full_hash(path: str) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def format_groups(groups: list[DuplicateGroup], limit: int, skipped: int = 0) -> str:
    note = f" ({skipped} files couldn't be read)" if skipped else ""
    if not groups:
        return f"no duplicates{note}"
    total = sum(g.wasted for g in groups)
    lines = [f"{len(groups)} groups, {human_size(total)} reclaimable{note}"]
    for g in groups[:limit]:
        lines.append(f"{human_size(g.size)} x{len(g.paths)} (wasted {human_size(g.wasted)})")
        lines += [f"  {p}" for p in g.paths]
    if len(groups) > limit:
        lines.append(f"... {len(groups) - limit} more groups (use --limit)")
    return "\n".join(lines)
