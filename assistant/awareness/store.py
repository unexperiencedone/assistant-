"""Where the activity record lives: one local SQLite file, two tables.

`events` is the raw focus stream (one row per poll that changed something) and is
short-lived. `sessions` is the collapsed record — the part that is actually worth
keeping and the only part a model is ever shown. Both are pruned on a timer; nothing
leaves this machine.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    app TEXT NOT NULL,
    category TEXT NOT NULL,
    context TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL,
    app TEXT NOT NULL,
    category TEXT NOT NULL,
    context TEXT NOT NULL,
    seconds REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at);
"""


class AwarenessStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
    def add_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events (ts, app, category, context, title) VALUES (?, ?, ?, ?, ?)",
                (event["ts"], event["app"], event["category"], event["context"], event.get("title", "")),
            )
            self._db.commit()

    def upsert_session(self, session_id: int | None, session: dict[str, Any]) -> int:
        """Write (or extend) one session. The open block is updated every poll, so the
        canvas shows a stretch of work while it is still happening."""
        with self._lock:
            if session_id is None:
                cursor = self._db.execute(
                    "INSERT INTO sessions (started_at, ended_at, app, category, context, seconds)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (session["start"], session["end"], session["app"], session["category"],
                     session["context"], session["end"] - session["start"]),
                )
                self._db.commit()
                return int(cursor.lastrowid)
            self._db.execute(
                "UPDATE sessions SET ended_at = ?, seconds = ? WHERE id = ?",
                (session["end"], session["end"] - session["start"], session_id),
            )
            self._db.commit()
            return session_id

    def merge_candidate(self, app: str, context: str, since: float) -> sqlite3.Row | None:
        """The last session of the same app and context, if it ended recently enough
        that this is really the same stretch of work coming back."""
        with self._lock:
            return self._db.execute(
                "SELECT * FROM sessions WHERE app = ? AND context = ? AND ended_at >= ?"
                " ORDER BY ended_at DESC LIMIT 1",
                (app, context, since),
            ).fetchone()

    # -- reading ----------------------------------------------------------------------
    def sessions_between(self, start: float, end: float, limit: int = 400) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM sessions WHERE ended_at >= ? AND started_at <= ?"
                " ORDER BY started_at ASC LIMIT ?",
                (start, end, limit),
            ).fetchall()
        return [
            {
                "start": row["started_at"],
                "end": row["ended_at"],
                "app": row["app"],
                "category": row["category"],
                "context": row["context"],
                "seconds": row["seconds"],
            }
            for row in rows
        ]

    def switches_between(self, start: float, end: float) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE started_at >= ? AND started_at <= ?"
                " AND category != 'idle'",
                (start, end),
            ).fetchone()
        return int(row["n"] if row else 0)

    # -- keeping it small -------------------------------------------------------------
    def prune(self, raw_days: float, session_days: float) -> None:
        """Raw events die young; the collapsed sessions live a little longer. Both are
        deleted for good — this is not an archive."""
        now = time.time()
        with self._lock:
            self._db.execute("DELETE FROM events WHERE ts < ?", (now - raw_days * 86400,))
            self._db.execute("DELETE FROM sessions WHERE ended_at < ?", (now - session_days * 86400,))
            self._db.commit()

    def forget_all(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM events")
            self._db.execute("DELETE FROM sessions")
            self._db.commit()
            self._db.execute("VACUUM")
