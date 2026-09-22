"""Where Nova's own record of its days lives: one local SQLite file, one row per day.

A day gets exactly one entry, keyed by its date, so the record cannot quietly grow a
second version of the same day -- rewriting replaces. The facts the entry was written
from are kept beside the text, which is what makes it checkable later: if an entry ever
reads oddly, the rows that produced it are right there.

Nothing here leaves this machine.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    day        TEXT PRIMARY KEY,               -- YYYY-MM-DD, local time
    written_at REAL NOT NULL,
    text       TEXT NOT NULL,
    facts      TEXT NOT NULL DEFAULT '{}',     -- the rows the entry was written from, as JSON
    source     TEXT NOT NULL DEFAULT 'plain'   -- plain (written here) | narrated (phrased by a model)
);
CREATE INDEX IF NOT EXISTS entries_written ON entries(written_at);
"""


class JournalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writing ----------------------------------------------------------------------
    def save(self, day: str, text: str, facts: dict[str, Any], source: str = "plain") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO entries (day, written_at, text, facts, source) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(day) DO UPDATE SET written_at = excluded.written_at, text = excluded.text, "
                "facts = excluded.facts, source = excluded.source",
                (day, time.time(), text, json.dumps(facts, ensure_ascii=False, default=str), source))
            self._db.commit()

    def prune(self, keep_days: float) -> int:
        cutoff = time.time() - keep_days * 86400
        with self._lock:
            removed = self._db.execute("DELETE FROM entries WHERE written_at < ?", (cutoff,)).rowcount
            self._db.commit()
        return removed

    def forget_all(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM entries")
            self._db.commit()

    # -- reading ----------------------------------------------------------------------
    def has(self, day: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM entries WHERE day = ?", (day,)).fetchone() is not None

    def get(self, day: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM entries WHERE day = ?", (day,)).fetchone()
        return _entry(row) if row else None

    def recent(self, limit: int = 7) -> list[dict[str, Any]]:
        """Newest first."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM entries ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
        return [_entry(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM entries").fetchone()[0])


def _entry(row: sqlite3.Row) -> dict[str, Any]:
    entry = dict(row)
    try:
        entry["facts"] = json.loads(entry["facts"] or "{}")
    except ValueError:
        entry["facts"] = {}
    return entry
