"""Standing goals: the things Nova is supposed to do without being asked each time.

One row per goal, with the next time it is due. A goal is a *request in Nova's own
words* -- the same sentence you would have said -- so a goal firing is indistinguishable
downstream from you asking, and every gate that applies to you asking still applies.

Nothing here decides anything. It stores what is due and records what ran.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id         INTEGER PRIMARY KEY,
    created_at REAL    NOT NULL,
    text       TEXT    NOT NULL,             -- the request, as it will be submitted
    cadence    TEXT    NOT NULL,             -- once | hourly | daily | weekly
    next_due   REAL    NOT NULL,
    last_run   REAL,
    runs       INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    source     TEXT    NOT NULL DEFAULT 'you' -- who set it: you | nova
);
CREATE INDEX IF NOT EXISTS goals_due ON goals(active, next_due);
"""

CADENCES = {"once": 0.0, "hourly": 3600.0, "daily": 86400.0, "weekly": 7 * 86400.0}


class GoalStore:
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
    def add(self, text: str, cadence: str = "daily", next_due: float | None = None,
            source: str = "you") -> int:
        cadence = cadence if cadence in CADENCES else "daily"
        now = time.time()
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO goals (created_at, text, cadence, next_due, source) VALUES (?, ?, ?, ?, ?)",
                (now, text.strip(), cadence, now if next_due is None else next_due, source))
            self._db.commit()
            return int(cursor.lastrowid)

    def ran(self, goal_id: int, when: float, next_due: float | None) -> None:
        """Record a firing. `next_due` of None retires the goal (a 'once')."""
        with self._lock:
            self._db.execute(
                "UPDATE goals SET last_run = ?, runs = runs + 1, next_due = ?, active = ? WHERE id = ?",
                (when, next_due if next_due is not None else when, 0 if next_due is None else 1, goal_id))
            self._db.commit()

    def set_active(self, goal_id: int, active: bool) -> bool:
        with self._lock:
            changed = self._db.execute("UPDATE goals SET active = ? WHERE id = ?",
                                       (1 if active else 0, goal_id)).rowcount
            self._db.commit()
        return changed > 0

    def remove(self, goal_id: int) -> bool:
        with self._lock:
            removed = self._db.execute("DELETE FROM goals WHERE id = ?", (goal_id,)).rowcount
            self._db.commit()
        return removed > 0

    def forget_all(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM goals")
            self._db.commit()

    # -- reading ----------------------------------------------------------------------
    def due(self, now: float) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM goals WHERE active = 1 AND next_due <= ? ORDER BY next_due", (now,)).fetchall()
        return [dict(r) for r in rows]

    def all(self, include_retired: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM goals" + ("" if include_retired else " WHERE active = 1") + " ORDER BY next_due"
        with self._lock:
            return [dict(r) for r in self._db.execute(sql).fetchall()]

    def get(self, goal_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None
