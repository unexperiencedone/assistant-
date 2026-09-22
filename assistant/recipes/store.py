"""What worked last time, so the next model doesn't have to rediscover it.

One row per kind of task. The row is not a script -- it is a short account of how the
job went when it went well: which tools, in what order, how long, which brain did it,
and what went wrong on the way. A model reading it is expected to adapt, not replay;
that is the difference between this and `automations/*.toml`, which replays exactly.

Why it is worth keeping at all: the expensive tier solves something once, and after
that the cheap tier can read how. Work migrates down the cascade instead of sitting at
the top forever, which is the only way the running cost falls over a lifetime rather
than staying flat (AGENTS.md section 12).

The pitfalls column matters as much as the tools one. A recorded dead end stops the
next model walking into it, and dead ends are the part no model can infer from a
successful transcript.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    task_type   TEXT PRIMARY KEY,
    utterance   TEXT NOT NULL,            -- how it was asked, for matching a new request
    tools       TEXT NOT NULL DEFAULT '[]',  -- the tool names, in the order they were called
    backend     TEXT NOT NULL DEFAULT '',
    seconds     REAL NOT NULL DEFAULT 0,
    pitfalls    TEXT NOT NULL DEFAULT '',  -- what failed first, newest last
    skill       TEXT NOT NULL DEFAULT '',  -- the skill that was used, when one was
    wins        INTEGER NOT NULL DEFAULT 0,
    fails       INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""
MAX_PITFALLS = 900     # a few lines; past that it stops being read and starts being skipped


class RecipeStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writing ----------------------------------------------------------------------
    def remember(self, task_type: str, utterance: str, tools: list[str] | None = None,
                 backend: str = "", seconds: float = 0.0, skill: str = "") -> None:
        """Record a success. A repeat updates the row rather than adding a second one.

        The newest successful run wins on tools and timing, because a better route found
        later is the one worth keeping. Pitfalls accumulate instead, since an old dead
        end is still a dead end.
        """
        task_type = (task_type or "").strip()
        if not task_type or not (utterance or "").strip():
            return
        now = time.time()
        payload = json.dumps([t for t in (tools or []) if t][:20])
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO recipes (task_type, utterance, tools, backend, seconds, skill,
                                        wins, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(task_type) DO UPDATE SET
                       utterance = excluded.utterance,
                       tools = excluded.tools,
                       backend = excluded.backend,
                       seconds = excluded.seconds,
                       skill = CASE WHEN excluded.skill != '' THEN excluded.skill ELSE recipes.skill END,
                       wins = recipes.wins + 1,
                       updated_at = excluded.updated_at""",
                (task_type, utterance.strip(), payload, backend, float(seconds), skill, now, now))

    def note_failure(self, task_type: str, note: str) -> None:
        """Record a dead end: what was tried and how it failed.

        Kept even when there is no successful recipe yet, because "this approach does
        not work" is useful on its own -- it is the bug trap the next model would
        otherwise walk into.
        """
        task_type, note = (task_type or "").strip(), " ".join((note or "").split())[:300]
        if not task_type or not note:
            return
        now = time.time()
        with self._lock, self._db:
            row = self._db.execute("SELECT pitfalls FROM recipes WHERE task_type = ?",
                                   (task_type,)).fetchone()
            if row is None:
                self._db.execute(
                    """INSERT INTO recipes (task_type, utterance, pitfalls, fails, created_at, updated_at)
                       VALUES (?, '', ?, 1, ?, ?)""", (task_type, note, now, now))
                return
            existing = row["pitfalls"] or ""
            if note in existing:      # the same failure twice is one lesson, not two
                self._db.execute("UPDATE recipes SET fails = fails + 1, updated_at = ? WHERE task_type = ?",
                                 (now, task_type))
                return
            merged = f"{existing}\n{note}".strip()[-MAX_PITFALLS:]
            self._db.execute(
                "UPDATE recipes SET pitfalls = ?, fails = fails + 1, updated_at = ? WHERE task_type = ?",
                (merged, now, task_type))

    def forget(self, task_type: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM recipes WHERE task_type = ?",
                                    (task_type,)).rowcount > 0

    # -- reading ----------------------------------------------------------------------
    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM recipes ORDER BY updated_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, task_type: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM recipes WHERE task_type = ?",
                                   (task_type,)).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["tools"] = json.loads(data.get("tools") or "[]")
        except ValueError:
            data["tools"] = []
        return data

    def state(self) -> dict[str, Any]:
        rows = self.all()
        return {"count": len(rows),
                "recipes": [{"task_type": r["task_type"], "tools": r["tools"], "wins": r["wins"],
                             "fails": r["fails"], "backend": r["backend"]} for r in rows[:20]]}
