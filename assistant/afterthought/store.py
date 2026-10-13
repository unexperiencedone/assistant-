"""Short-term and long-term memory in one SQLite file, vectors beside the rows.

Why not ChromaDB: a few thousand 384-float vectors fit in a numpy matrix that a
cosine search scans in well under a millisecond, and this keeps the "no new
dependency without a reason" rule. The matrix is rebuilt only when rows change.

`tier` is "short" (raw chunks, expire after 7 days unless promoted) or "long" (the
knowledge base: consolidated entries and early promotions, kept until you delete them).
Every long-term entry can be listed and deleted individually.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id           INTEGER PRIMARY KEY,
    tier         TEXT NOT NULL DEFAULT 'short',   -- short | long
    kind         TEXT NOT NULL,                   -- window | browser | file | clipboard | nova | note | summary
    created_at   REAL NOT NULL,
    start        REAL NOT NULL,
    end          REAL NOT NULL,
    app          TEXT NOT NULL DEFAULT '',
    context      TEXT NOT NULL DEFAULT '',        -- what the window was on
    title        TEXT NOT NULL DEFAULT '',        -- how it is shown
    text         TEXT NOT NULL DEFAULT '',
    entities     TEXT NOT NULL DEFAULT '[]',
    sources      TEXT NOT NULL DEFAULT '[]',      -- [{"kind","app","title","at"}]: where it came from
    importance   REAL NOT NULL DEFAULT 0.3,
    refs         INTEGER NOT NULL DEFAULT 0,      -- recalled or accepted
    last_ref     REAL,
    shown        INTEGER NOT NULL DEFAULT 0,
    accepted     INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    last_shown   REAL,
    merged_into  INTEGER,                         -- the long-term entry it was consolidated into
    vector       BLOB
);
CREATE INDEX IF NOT EXISTS memories_tier ON memories(tier, created_at);
CREATE TABLE IF NOT EXISTS suggestions (
    id         INTEGER PRIMARY KEY,
    at         REAL NOT NULL,
    memory_id  INTEGER NOT NULL,
    score      REAL NOT NULL,
    similarity REAL NOT NULL,
    context    TEXT NOT NULL DEFAULT '',
    spoken     INTEGER NOT NULL DEFAULT 0,
    outcome    TEXT NOT NULL DEFAULT 'shown'      -- shown | accepted | dismissed
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_COLUMNS = ("tier", "kind", "created_at", "start", "end", "app", "context", "title", "text", "entities",
            "sources", "importance", "refs", "last_ref", "merged_into")


def _row(row: sqlite3.Row) -> dict[str, Any]:
    item = {k: row[k] for k in row.keys() if k != "vector"}
    item["entities"] = json.loads(item.get("entities") or "[]")
    item["sources"] = json.loads(item.get("sources") or "[]")
    return item


class MemoryStore:
    def __init__(self, path: Path, dim: int = 384) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._ids: np.ndarray = np.zeros(0, dtype=np.int64)
        self._matrix: np.ndarray = np.zeros((0, dim), dtype=np.float32)
        self._dirty = True
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writing ----------------------------------------------------------------------
    def add(self, memory: dict[str, Any], vector: np.ndarray | None) -> int:
        values = {k: memory.get(k) for k in _COLUMNS if k in memory}
        values.setdefault("created_at", time.time())
        values.setdefault("start", values["created_at"])
        values.setdefault("end", values["start"])
        for key in ("entities", "sources"):
            values[key] = json.dumps(values.get(key) or [], ensure_ascii=False)
        names = list(values) + ["vector"]
        blob = None if vector is None else np.asarray(vector, dtype=np.float32).tobytes()
        with self._lock:
            cursor = self._db.execute(
                f"INSERT INTO memories ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                [*values.values(), blob])
            self._db.commit()
            self._dirty = True
            return int(cursor.lastrowid)

    def update(self, memory_id: int, vector: np.ndarray | None = None, **fields: Any) -> None:
        for key in ("entities", "sources"):
            if key in fields:
                fields[key] = json.dumps(fields[key] or [], ensure_ascii=False)
        sets = [f"{k} = ?" for k in fields]
        params = list(fields.values())
        if vector is not None:
            sets.append("vector = ?")
            params.append(np.asarray(vector, dtype=np.float32).tobytes())
            self._dirty = True
        if not sets:
            return
        with self._lock:
            self._db.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", [*params, memory_id])
            self._db.commit()

    def bump(self, memory_id: int, column: str) -> None:
        assert column in ("refs", "shown", "accepted", "dismissed")
        now = time.time()
        extra = ", last_ref = ?" if column in ("refs", "accepted") else ", last_shown = ?" if column == "shown" else ""
        params: list[Any] = [now] if extra else []
        with self._lock:
            self._db.execute(f"UPDATE memories SET {column} = {column} + 1{extra} WHERE id = ?", [*params, memory_id])
            if column == "accepted":
                self._db.execute("UPDATE memories SET refs = refs + 1 WHERE id = ?", (memory_id,))
            self._db.commit()

    def delete(self, memory_id: int) -> bool:
        with self._lock:
            gone = self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount
            # Forgetting a long-term entry forgets the chunks it was made from, too.
            self._db.execute("DELETE FROM memories WHERE merged_into = ?", (memory_id,))
            self._db.execute("DELETE FROM suggestions WHERE memory_id = ?", (memory_id,))
            self._db.commit()
            self._dirty = True
        return gone > 0

    def expire(self, before: float, promote_refs: int) -> int:
        """Short-term memories older than `before` go, unless referenced enough to stay."""
        with self._lock:
            gone = self._db.execute(
                "DELETE FROM memories WHERE tier = 'short' AND end < ? AND refs < ?", (before, promote_refs)).rowcount
            self._db.commit()
            self._dirty = self._dirty or gone > 0
        return gone

    def forget_all(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM memories")
            self._db.execute("DELETE FROM suggestions")
            self._db.commit()
            self._dirty = True

    # -- reading ----------------------------------------------------------------------
    def get(self, memory_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return _row(row) if row else None

    def vector(self, memory_id: int) -> np.ndarray | None:
        with self._lock:
            row = self._db.execute("SELECT vector FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return np.frombuffer(row[0], dtype=np.float32) if row and row[0] else None

    def listing(self, tier: str | None = None, limit: int = 100, since: float = 0.0,
                unmerged: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE created_at >= ?"
        params: list[Any] = [since]
        if tier:
            sql += " AND tier = ?"
            params.append(tier)
        if unmerged:
            sql += " AND merged_into IS NULL"
        sql += " ORDER BY end DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, params).fetchall()]

    def recent_vectors(self, count: int) -> list[tuple[int, np.ndarray]]:
        with self._lock:
            rows = self._db.execute("SELECT id, vector FROM memories WHERE tier = 'short' AND vector IS NOT NULL "
                                    "ORDER BY end DESC LIMIT ?", (count,)).fetchall()
        return [(r[0], np.frombuffer(r[1], dtype=np.float32)) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT tier, COUNT(*) FROM memories GROUP BY tier").fetchall()
        out = {"short": 0, "long": 0}
        out.update({r[0]: r[1] for r in rows})
        return out

    def matrix(self) -> tuple[np.ndarray, np.ndarray]:
        """(ids, unit vectors) of every memory, rebuilt only after a change."""
        with self._lock:
            if self._dirty:
                rows = self._db.execute("SELECT id, vector FROM memories WHERE vector IS NOT NULL").fetchall()
                self._ids = np.array([r[0] for r in rows], dtype=np.int64)
                self._matrix = (np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                                if rows else np.zeros((0, self.dim), dtype=np.float32))
                self._dirty = False
            return self._ids, self._matrix

    # -- suggestions ------------------------------------------------------------------
    def log_suggestion(self, memory_id: int, score: float, similarity: float, context: str, spoken: bool) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO suggestions (at, memory_id, score, similarity, context, spoken) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), memory_id, score, similarity, context, int(spoken)))
            self._db.commit()
            return int(cursor.lastrowid)

    def resolve_suggestion(self, suggestion_id: int, outcome: str) -> None:
        with self._lock:
            self._db.execute("UPDATE suggestions SET outcome = ? WHERE id = ?", (outcome, suggestion_id))
            self._db.commit()

    def suggestions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM suggestions ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def last_suggestion_at(self) -> float:
        with self._lock:
            row = self._db.execute("SELECT MAX(at) FROM suggestions").fetchone()
        return float(row[0] or 0)

    # -- meta -------------------------------------------------------------------------
    def meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            self._db.commit()
