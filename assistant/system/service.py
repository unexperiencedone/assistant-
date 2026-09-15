"""One object for everything local: resolve, open, find, reveal, duplicates, reindex.

Resolution order for "open X" (fastest first):
  1. in-memory hot cache      query -> item you opened last time
  2. query_cache table        same, persisted across restarts
  3. ranked index search      match quality + frecency
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import LocalSettings
from .db import Database
from .duplicates import DuplicateFinder, DuplicateGroup
from .indexer import DEFAULT_EXCLUDES, Indexer, IndexStats
from .ranking import bump, normalize
from .search import Hit, Searcher

MIN_CONFIDENCE = 40.0
HOT_CACHE_SIZE = 256

Log = Callable[[str, str], None]


@dataclass
class OpenResult:
    ok: bool
    message: str
    hit: Hit | None = None
    alternatives: list[Hit] = field(default_factory=list)


class LocalSystem:
    def __init__(self, settings: LocalSettings, project_root: Path, log: Log | None = None) -> None:
        self.settings = settings
        self.log = log or (lambda text, level="info": None)
        db_path = Path(os.path.expandvars(settings.db_path)).expanduser()
        self.db = Database(db_path if db_path.is_absolute() else project_root / db_path)
        self.half_life = settings.half_life_days * 86400
        self.searcher = Searcher(self.db, self.half_life)
        self.indexer = Indexer(
            self.db,
            roots=[Path(os.path.expandvars(r)).expanduser() for r in settings.roots],
            excludes=DEFAULT_EXCLUDES | set(settings.exclude_dirs),
            max_depth=settings.max_depth,
            index_hidden=settings.index_hidden,
        )
        self.duplicate_finder = DuplicateFinder(self.db)
        self._hot: OrderedDict[str, Hit] = OrderedDict()
        self._index_lock = threading.Lock()
        self.last_results: list[Hit] = []

    # -- background indexing -------------------------------------------------------
    def start_background_indexing(self) -> None:
        threading.Thread(target=self._index_loop, name="indexer", daemon=True).start()

    def _index_loop(self) -> None:
        while True:
            try:
                self.refresh_if_stale()
            except Exception as exc:  # never let the indexer take the assistant down
                self.log(f"Indexing failed: {exc}", "warn")
            time.sleep(max(60, self.settings.reindex_minutes * 60))

    def refresh_if_stale(self) -> None:
        now = time.time()
        apps_at = float(self.db.get_meta("apps_indexed_at") or 0)
        files_at = float(self.db.get_meta("files_indexed_at") or 0)
        full_at = float(self.db.get_meta("full_indexed_at") or 0)
        if now - apps_at > self.settings.reindex_minutes * 60:
            self.log(f"Indexed {self.reindex_apps()} apps.", "info")
        if now - files_at > self.settings.reindex_minutes * 60:
            full = now - full_at > self.settings.full_rescan_hours * 3600
            stats = self.reindex_files(full=full)
            self.log(("Full" if full else "Incremental") + f" index: {stats.summary()}", "info")

    def reindex_apps(self) -> int:
        with self._index_lock:
            return self.indexer.index_apps()

    def reindex_files(self, full: bool = False) -> IndexStats:
        with self._index_lock:
            return self.indexer.index_files(full=full)

    # -- search & open --------------------------------------------------------------
    def find(self, query: str, kind: str | None = None, ext: str | None = None,
             under: str | None = None, limit: int = 10) -> list[Hit]:
        hits = self.searcher.search(query, kind=kind, ext=ext, under=under, limit=limit)
        self.last_results = hits
        return hits

    def resolve(self, query: str, kind: str | None = None) -> tuple[Hit | None, list[Hit]]:
        key = f"{kind or '*'}:{normalize(query)}"
        hit = self._hot.get(key) or self._cached_query(key)
        if hit and self._still_exists(hit):
            self._hot[key] = hit
            self._hot.move_to_end(key)
            return hit, []
        hits = self.searcher.search(query, kind=kind, limit=5, prefer_kind="app" if kind is None else None)
        if hits and hits[0].score >= MIN_CONFIDENCE:
            return hits[0], hits[1:]
        return None, hits

    def open(self, query: str, kind: str | None = None, dry_run: bool = False) -> OpenResult:
        hit, alternatives = self.resolve(query, kind)
        if not hit:
            return OpenResult(False, f"No confident match for '{query}'.", None, alternatives)
        if not dry_run:
            try:
                launch(hit.path)
            except OSError as exc:
                return OpenResult(False, f"Couldn't open {hit.name}: {exc}", hit, alternatives)
            self.record_use(hit, query, kind)
        return OpenResult(True, f"Opened {hit.name}.", hit, alternatives)

    def open_hit(self, hit: Hit) -> None:
        launch(hit.path)
        self.record_use(hit)

    def reveal(self, path: str) -> None:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer.exe /select,"{path}"')
        else:
            launch(str(Path(path).parent))

    # -- usage tracking ----------------------------------------------------------------
    def record_use(self, hit: Hit, query: str | None = None, kind: str | None = None) -> None:
        conn = self.db.connect()
        now = int(time.time())
        with conn:
            row = conn.execute("SELECT score, score_at FROM items WHERE id = ?", (hit.id,)).fetchone()
            if not row:
                return
            score, score_at = bump(row["score"], row["score_at"], self.half_life, now=now)
            conn.execute("UPDATE items SET score = ?, score_at = ?, use_count = use_count + 1, last_used = ? WHERE id = ?",
                         (score, score_at, now, hit.id))
            if query:
                key = f"{kind or '*'}:{normalize(query)}"
                conn.execute(
                    """INSERT INTO query_cache (query, item_id, hits, last_used) VALUES (?, ?, 1, ?)
                       ON CONFLICT (query) DO UPDATE SET
                           hits = CASE WHEN item_id = excluded.item_id THEN hits + 1 ELSE 1 END,
                           item_id = excluded.item_id, last_used = excluded.last_used""",
                    (key, hit.id, now),
                )
                self._hot[key] = hit
                self._hot.move_to_end(key)
                while len(self._hot) > HOT_CACHE_SIZE:
                    self._hot.popitem(last=False)

    def record_path(self, path: str) -> Hit | None:
        """Count a use of a path opened some other way (e.g. by an agent), indexing it if new."""
        p = Path(path).expanduser()
        if not p.exists():
            return None
        conn = self.db.connect()
        now = int(time.time())
        kind = "folder" if p.is_dir() else "file"
        st = p.stat()
        with conn:
            conn.execute(
                """INSERT INTO items (kind, name, path, parent, ext, size, mtime, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (path) DO NOTHING""",
                (kind, p.name, str(p), str(p.parent), p.suffix.lower() or None,
                 None if kind == "folder" else st.st_size, st.st_mtime, now),
            )
        row = conn.execute("SELECT id, kind, name, path, size FROM items WHERE path = ?", (str(p),)).fetchone()
        hit = Hit(row["id"], row["kind"], row["name"], row["path"], row["size"], 0.0, 0.0)
        self.record_use(hit)
        return hit

    def top(self, kind: str | None = None, limit: int = 10) -> list[Hit]:
        return self.searcher.top(kind, limit)

    # -- duplicates & stats ---------------------------------------------------------------
    def duplicates(self, root: str, min_size: int = 1024, fresh: bool = False) -> list[DuplicateGroup]:
        return self.duplicate_finder.find(Path(root).expanduser(), min_size=min_size, use_index=not fresh)

    def stats(self) -> dict[str, str | int]:
        conn = self.db.connect()
        counts = dict(conn.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())
        fmt = lambda key: time.strftime("%Y-%m-%d %H:%M", time.localtime(float(self.db.get_meta(key) or 0))) \
            if self.db.get_meta(key) else "never"
        return {
            "apps": counts.get("app", 0), "files": counts.get("file", 0), "folders": counts.get("folder", 0),
            "used": conn.execute("SELECT COUNT(*) FROM items WHERE use_count > 0").fetchone()[0],
            "apps_indexed": fmt("apps_indexed_at"), "files_indexed": fmt("files_indexed_at"),
            "roots": "; ".join(str(r) for r in self.indexer.roots), "db": str(self.db.path),
        }

    # -- internals -----------------------------------------------------------------------
    def _cached_query(self, key: str) -> Hit | None:
        row = self.db.connect().execute(
            """SELECT i.id, i.kind, i.name, i.path, i.size FROM query_cache q JOIN items i ON i.id = q.item_id
               WHERE q.query = ?""", (key,)).fetchone()
        return Hit(row["id"], row["kind"], row["name"], row["path"], row["size"], 0.0, 100.0) if row else None

    def _still_exists(self, hit: Hit) -> bool:
        if hit.kind == "app" and not (len(hit.path) > 2 and hit.path[1] == ":"):
            return True  # shell:AppsFolder / URI targets can't be stat'ed cheaply
        if os.path.exists(hit.path):
            return True
        with self.db.connect() as conn:
            conn.execute("DELETE FROM items WHERE id = ?", (hit.id,))
        return False


def launch(target: str) -> None:
    if sys.platform == "win32":
        os.startfile(target)  # ShellExecute: apps, shell: URIs, ms-settings:, files, folders
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
