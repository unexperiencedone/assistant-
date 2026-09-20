"""Builds and incrementally refreshes the index of apps, files and folders.

Incremental passes compare each directory's mtime with the stored one. NTFS
updates a directory's mtime when entries are added, removed or renamed inside
it, so unchanged directories are not re-listed; only their known subfolders are
visited. A full pass (every `full_rescan_hours`) also catches in-place file
edits and prunes anything that disappeared.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .apps import discover_apps
from .db import Database

BATCH = 2000
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT

DEFAULT_EXCLUDES = {
    "node_modules", ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".gradle", ".idea", ".vs", ".cache",
    "$recycle.bin", "system volume information", "appdata", "site-packages", "windowsapps",
}


def default_roots() -> list[Path]:
    home = Path.home()
    names = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive", "source", "repos", "projects", "code"]
    roots = [home / n for n in names if (home / n).is_dir()]
    # OneDrive redirection: skip OneDrive children already covered by the OneDrive root.
    return _dedupe_nested(roots)


def _dedupe_nested(paths: list[Path]) -> list[Path]:
    resolved = sorted({p.resolve() for p in paths}, key=lambda p: len(str(p)))
    kept: list[Path] = []
    for p in resolved:
        if not any(p == k or k in p.parents for k in kept):
            kept.append(p)
    return kept


@dataclass
class IndexStats:
    apps: int = 0
    files: int = 0
    folders: int = 0
    dirs_listed: int = 0
    dirs_skipped: int = 0
    removed: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.apps} apps, {self.files} files, {self.folders} folders indexed "
                f"({self.dirs_listed} dirs listed, {self.dirs_skipped} unchanged, {self.removed} removed) "
                f"in {self.seconds:.1f}s")


class Indexer:
    def __init__(self, db: Database, roots: list[Path], excludes: set[str], max_depth: int, index_hidden: bool) -> None:
        self.db = db
        self.roots = _dedupe_nested(roots) if roots else default_roots()
        self.excludes = {e.lower() for e in excludes}
        self.max_depth = max_depth
        self.index_hidden = index_hidden

    # -- apps -------------------------------------------------------------------
    def index_apps(self) -> int:
        apps = discover_apps()
        if not apps:
            return 0
        now = max(int(time.time()), int(self.db.get_meta("last_app_pass") or 0) + 1)
        self.db.set_meta("last_app_pass", str(now))
        with self.db.connect() as conn:
            conn.executemany(
                """INSERT INTO items (kind, name, path, seen_at) VALUES ('app', ?, ?, ?)
                   ON CONFLICT (path) DO UPDATE SET name = excluded.name, seen_at = excluded.seen_at""",
                [(a.name, a.target, now) for a in apps],
            )
            conn.execute("DELETE FROM items WHERE kind = 'app' AND seen_at < ?", (now,))
        self.db.set_meta("apps_indexed_at", str(now))
        return len(apps)

    # -- files ----------------------------------------------------------------
    def index_files(self, full: bool = False, progress: Callable[[str], None] | None = None) -> IndexStats:
        stats = IndexStats()
        started = time.time()
        # Strictly increasing even when two passes start within the same second.
        pass_id = max(int(started), int(self.db.get_meta("last_pass") or 0) + 1)
        self.db.set_meta("last_pass", str(pass_id))
        conn = self.db.connect()
        known_dirs = {} if full else dict(conn.execute("SELECT path, mtime FROM dirs").fetchall())
        pending: list[tuple] = []

        def flush() -> None:
            if pending:
                with conn:
                    conn.executemany(
                        """INSERT INTO items (kind, name, path, parent, ext, size, mtime, seen_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT (path) DO UPDATE SET
                               name = excluded.name, size = excluded.size,
                               mtime = excluded.mtime, seen_at = excluded.seen_at""",
                        pending,
                    )
                pending.clear()

        for root in self.roots:  # roots are searchable folders too ("open downloads")
            try:
                pending.append(("folder", root.name, str(root), str(root.parent), None, None, root.stat().st_mtime, pass_id))
            except OSError:
                pass
        # Breadth-first: during a first cold index, a search finds things in Documents
        # before the walk disappears into some deep folder.
        stack: deque[tuple[Path, int]] = deque((root, 0) for root in self.roots)
        while stack:
            directory, depth = stack.popleft()
            dir_key = str(directory)
            try:
                dir_mtime = directory.stat().st_mtime
            except OSError:
                continue

            if not full and known_dirs.get(dir_key) == dir_mtime:
                # Unchanged listing: recurse into known subfolders without re-listing.
                flush()
                stats.dirs_skipped += 1
                with conn:
                    conn.execute("UPDATE items SET seen_at = ? WHERE parent = ?", (pass_id, dir_key))
                if depth < self.max_depth:
                    for (sub,) in conn.execute("SELECT path FROM items WHERE parent = ? AND kind = 'folder'", (dir_key,)):
                        stack.append((Path(sub), depth + 1))
                continue

            stats.dirs_listed += 1
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                stats.errors.append(f"{directory}: {exc.strerror}")
                continue

            for entry in entries:
                name = entry.name
                if not self.index_hidden and name.startswith((".", "~$")):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if name.lower() in self.excludes:
                            continue
                        st = entry.stat(follow_symlinks=False)
                        if getattr(st, "st_file_attributes", 0) & _REPARSE_POINT:
                            continue  # junctions like "My Music" loop back or deny access
                        pending.append(("folder", name, entry.path, dir_key, None, None, st.st_mtime, pass_id))
                        stats.folders += 1
                        if depth < self.max_depth:
                            stack.append((Path(entry.path), depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        ext = os.path.splitext(name)[1].lower() or None
                        pending.append(("file", name, entry.path, dir_key, ext, st.st_size, st.st_mtime, pass_id))
                        stats.files += 1
                except OSError:
                    continue
            if len(pending) >= BATCH:
                flush()
                if progress:
                    progress(f"Indexed {stats.files} files...")

            flush()
            with conn:
                # Anything this directory used to contain but no longer does.
                gone = [r[0] for r in conn.execute(
                    "SELECT path FROM items WHERE parent = ? AND seen_at < ?", (dir_key, pass_id))]
                for path in gone:
                    stats.removed += self._delete_tree(conn, path)
                conn.execute("INSERT OR REPLACE INTO dirs (path, mtime, scanned_at) VALUES (?, ?, ?)",
                             (dir_key, dir_mtime, pass_id))

        flush()
        stats.seconds = time.time() - started
        self.db.set_meta("files_indexed_at", str(pass_id))
        if full:
            self.db.set_meta("full_indexed_at", str(pass_id))
        return stats

    @staticmethod
    def _delete_tree(conn, path: str) -> int:
        """Delete an item and, for folders, everything under it (range scan on the path index)."""
        sep = "\\" if "\\" in path else "/"
        lo, hi = path + sep, path + chr(ord(sep) + 1)
        n = conn.execute("DELETE FROM items WHERE path = ? OR (path >= ? AND path < ?)", (path, lo, hi)).rowcount
        conn.execute("DELETE FROM dirs WHERE path = ? OR (path >= ? AND path < ?)", (path, lo, hi))
        return n
