"""Your schedule: one row per thing to do, and which reminders have gone out.

Reminders are tracked by key, not by row, so a calendar event (which has no row here)
is reminded exactly once per threshold too.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    title      TEXT NOT NULL,
    start      REAL NOT NULL,          -- unix time
    end        REAL,
    note       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS items_start ON items(start);
CREATE TABLE IF NOT EXISTS reminded (
    key   TEXT PRIMARY KEY,            -- nova:<id>@<start> | cal:<uid>@<start>
    marks TEXT NOT NULL,               -- thresholds already announced, e.g. "60,30"
    at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["start"] = datetime.fromtimestamp(item["start"])
    item["end"] = datetime.fromtimestamp(item["end"]) if item["end"] else None
    return item


class ScheduleStore:
    def __init__(self, path: Path) -> None:
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

    def add(self, title: str, start: datetime, end: datetime | None = None, note: str = "") -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO items (created_at, title, start, end, note) VALUES (?, ?, ?, ?, ?)",
                (time.time(), title.strip(), start.timestamp(), end.timestamp() if end else None, note.strip()))
            self._db.commit()
            return int(cursor.lastrowid)

    def remove(self, item_id: int) -> bool:
        with self._lock:
            gone = self._db.execute("DELETE FROM items WHERE id = ?", (item_id,)).rowcount
            self._db.commit()
        return gone > 0

    def between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM items WHERE start >= ? AND start < ? ORDER BY start",
                                    (start.timestamp(), end.timestamp())).fetchall()
        return [_row(r) for r in rows]

    def get(self, item_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _row(row) if row else None

    # -- reminders --------------------------------------------------------------------
    def marks(self, key: str) -> set[int]:
        with self._lock:
            row = self._db.execute("SELECT marks FROM reminded WHERE key = ?", (key,)).fetchone()
        return {int(m) for m in row[0].split(",") if m} if row else set()

    def mark(self, key: str, marks: set[int]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO reminded (key, marks, at) VALUES (?, ?, ?)",
                             (key, ",".join(str(m) for m in sorted(marks)), time.time()))
            # Keys for things long past are no use to anyone.
            self._db.execute("DELETE FROM reminded WHERE at < ?", (time.time() - 14 * 86400,))
            self._db.commit()

    def meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self._db.commit()
