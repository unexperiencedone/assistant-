"""One object for everything local: resolve, open, find, reveal, duplicates, reindex.

Resolution order for "open X" (fastest first):
  1. query_cache table        query -> item you opened last time, while that choice is
                              still fresh (it decays like everything else)
  2. ranked index search      match quality + frecency
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import LocalSettings
from .db import Database
from .duplicates import DuplicateFinder, DuplicateGroup
from .indexer import DEFAULT_EXCLUDES, Indexer, IndexStats
from .commands import MIN_USES, normalize_command, worth_keeping
from .ranking import HalfLives, bump, decayed, demote, normalize, rank_key
from .search import Hit, Searcher

MIN_CONFIDENCE = 40.0
# A remembered "query -> item" choice is trusted while its decayed hit count stays at or above
# this: one pick lasts one half-life, a pick made three times lasts about 2.6.
CACHED_CHOICE_MIN = 0.5

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
        self.half_lives = HalfLives(dict(getattr(settings, "half_life_by_kind", {}) or {}), settings.half_life_days)
        self.searcher = Searcher(self.db, self.half_lives)
        self.indexer = Indexer(
            self.db,
            roots=[Path(os.path.expandvars(r)).expanduser() for r in settings.roots],
            excludes=DEFAULT_EXCLUDES | set(settings.exclude_dirs),
            max_depth=settings.max_depth,
            index_hidden=settings.index_hidden,
        )
        self.duplicate_finder = DuplicateFinder(self.db)
        self._index_lock = threading.Lock()
        self.last_results: list[Hit] = []
        self._sync_rank_keys()
        self._clean_commands()

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
        hit = self._cached_query(key)
        if hit and self._still_exists(hit):
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

    def open_hit(self, hit: Hit, query: str | None = None, kind: str | None = None) -> None:
        launch(hit.path)
        self.record_use(hit, query, kind)

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
            row = conn.execute("SELECT kind, score, score_at FROM items WHERE id = ?", (hit.id,)).fetchone()
            if not row:
                return
            half_life = self.half_lives.seconds(row["kind"])
            score, score_at = bump(row["score"], row["score_at"], half_life, now=now)
            conn.execute("UPDATE items SET score = ?, score_at = ?, rank_key = ?, use_count = use_count + 1, "
                         "last_used = ? WHERE id = ?", (score, score_at, rank_key(score, score_at, half_life), now, hit.id))
            if query:
                key = f"{kind or '*'}:{normalize(query)}"
                conn.execute(
                    """INSERT INTO query_cache (query, item_id, hits, last_used) VALUES (?, ?, 1, ?)
                       ON CONFLICT (query) DO UPDATE SET
                           hits = CASE WHEN item_id = excluded.item_id THEN hits + 1 ELSE 1 END,
                           item_id = excluded.item_id, last_used = excluded.last_used""",
                    (key, hit.id, now),
                )

    def record_rejection(self, hit: Hit, query: str | None = None, kind: str | None = None) -> None:
        """The user turned this result down ("no, the other one"): it loses half its usage
        score and stops being the remembered answer for that query."""
        conn = self.db.connect()
        now = int(time.time())
        with conn:
            row = conn.execute("SELECT kind, score, score_at FROM items WHERE id = ?", (hit.id,)).fetchone()
            if not row:
                return
            half_life = self.half_lives.seconds(row["kind"])
            score, score_at = demote(row["score"], row["score_at"], half_life, now=now)
            conn.execute("UPDATE items SET score = ?, score_at = ?, rank_key = ? WHERE id = ?",
                         (score, score_at, rank_key(score, score_at, half_life), hit.id))
            if query:
                conn.execute("DELETE FROM query_cache WHERE query = ? AND item_id = ?",
                             (f"{kind or '*'}:{normalize(query)}", hit.id))

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

    # -- command frecency (for the canvas's quick actions) ---------------------------------
    def record_command(self, text: str) -> bool:
        """Count a request that worked. False when it isn't button material (too long, noise)."""
        command = normalize_command(text)
        if not worth_keeping(command):
            return False
        conn = self.db.connect()
        now = int(time.time())
        half_life = self.half_lives.seconds("command")
        with conn:
            row = conn.execute("SELECT score, score_at FROM commands WHERE text = ?", (command,)).fetchone()
            score, score_at = bump(row["score"] if row else 0.0, row["score_at"] if row else 0, half_life, now=now)
            conn.execute("""INSERT INTO commands (text, count, last_used, score, score_at, rank_key) VALUES (?, 1, ?, ?, ?, ?)
                            ON CONFLICT (text) DO UPDATE SET count = count + 1, last_used = excluded.last_used,
                                score = excluded.score, score_at = excluded.score_at, rank_key = excluded.rank_key""",
                         (command, now, score, score_at, rank_key(score, score_at, half_life)))
        return True

    def top_commands(self, limit: int = 6) -> list[str]:
        """Your habits: commands used at least twice, most-used-lately first."""
        rows = self.db.connect().execute(
            "SELECT text FROM commands WHERE count >= ? ORDER BY rank_key DESC LIMIT ?", (MIN_USES, limit))
        return [r["text"] for r in rows]

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
            """SELECT i.id, i.kind, i.name, i.path, i.size, q.hits, q.last_used FROM query_cache q
               JOIN items i ON i.id = q.item_id WHERE q.query = ?""", (key,)).fetchone()
        if not row:
            return None
        # An old choice stops short-circuiting the search, so newer habits can win.
        if decayed(row["hits"], row["last_used"], time.time(), self.half_lives.seconds(row["kind"])) < CACHED_CHOICE_MIN:
            return None
        return Hit(row["id"], row["kind"], row["name"], row["path"], row["size"], 0.0, 100.0)

    def _sync_rank_keys(self) -> None:
        """Rank keys depend on the half-lives: recompute them when the settings change
        (or after the upgrade that added them). Only used rows have one, so this is quick."""
        signature = self.half_lives.signature()
        missing = self.db.connect().execute(
            "SELECT EXISTS (SELECT 1 FROM items WHERE score > 0 AND rank_key IS NULL) "
            "OR EXISTS (SELECT 1 FROM commands WHERE score > 0 AND rank_key IS NULL)").fetchone()[0]
        if self.db.get_meta("half_lives") == signature and not missing:
            return
        with self.db.connect() as conn:
            for r in conn.execute("SELECT id, kind, score, score_at FROM items WHERE score > 0").fetchall():
                conn.execute("UPDATE items SET rank_key = ? WHERE id = ?",
                             (rank_key(r["score"], r["score_at"], self.half_lives.seconds(r["kind"])), r["id"]))
            half_life = self.half_lives.seconds("command")
            for r in conn.execute("SELECT text, score, score_at FROM commands WHERE score > 0").fetchall():
                conn.execute("UPDATE commands SET rank_key = ? WHERE text = ?",
                             (rank_key(r["score"], r["score_at"], half_life), r["text"]))
        self.db.set_meta("half_lives", signature)

    def _clean_commands(self) -> None:
        """One-time cleanup of commands recorded before filtering existed: merge spellings of
        the same command, drop noise and long one-off requests."""
        if self.db.get_meta("commands_cleaned"):
            return
        half_life = self.half_lives.seconds("command")
        with self.db.connect() as conn:
            merged: dict[str, dict] = {}
            for r in conn.execute("SELECT text, count, last_used, score, score_at FROM commands").fetchall():
                command = normalize_command(r["text"])
                if not worth_keeping(command):
                    continue
                m = merged.setdefault(command, {"count": 0, "last_used": 0, "score": 0.0, "score_at": 0})
                at = max(m["score_at"], r["score_at"])
                m["score"] = decayed(m["score"], m["score_at"], at, half_life) + decayed(r["score"], r["score_at"], at, half_life)
                m["score_at"] = at
                m["count"] += r["count"]
                m["last_used"] = max(m["last_used"], r["last_used"] or 0)
            conn.execute("DELETE FROM commands")
            conn.executemany(
                "INSERT INTO commands (text, count, last_used, score, score_at, rank_key) VALUES (?, ?, ?, ?, ?, ?)",
                [(c, m["count"], m["last_used"], m["score"], m["score_at"], rank_key(m["score"], m["score_at"], half_life))
                 for c, m in merged.items()])
        self.db.set_meta("commands_cleaned", "1")

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
