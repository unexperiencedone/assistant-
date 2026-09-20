"""SQLite storage for work sessions: one row per session, every event in it, and the
finished graph of each request.

A session is a stretch of activity (see recorder.py for where one starts and ends).
Events keep their full text; the canvas shortens where it draws. A trigram FTS index
over event text makes "which session did I fix the login in" a millisecond query.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY,
    started_at    REAL    NOT NULL,
    last_activity REAL    NOT NULL,
    ended_at      REAL,                        -- NULL while the session is open
    title         TEXT    NOT NULL DEFAULT '', -- the first request
    requests      INTEGER NOT NULL DEFAULT 0,
    results       INTEGER NOT NULL DEFAULT 0,
    failures      INTEGER NOT NULL DEFAULT 0,
    actions       INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    backends      TEXT    NOT NULL DEFAULT '[]',
    projects      TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions (started_at);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    ts         REAL    NOT NULL,
    kind       TEXT    NOT NULL,  -- you | nova | request | start | tool | result | error | plan | log
    task       TEXT,
    backend    TEXT,
    tool       TEXT,
    text       TEXT    NOT NULL DEFAULT '',
    data       TEXT               -- JSON
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id, ts);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    ts         REAL    NOT NULL,
    task       TEXT,
    label      TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'done',
    graph      TEXT    NOT NULL  -- the request's full graph, as /api/run serves it
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs (session_id, ts);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
    USING fts5 (text, content = 'events', content_rowid = 'id', tokenize = 'trigram');
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts (events_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

SESSION_COLUMNS = ("id, started_at, last_activity, ended_at, title, requests, results, failures, actions, "
                   "cost_usd, backends, projects")
MAX_EVENTS = 5000  # per session detail response; a session past this is shown from its start
# Tools that change files, across Claude Code and Antigravity naming.
_WRITE_TOOL = re.compile(r"write|edit|create|replace|notebook", re.I)
_PATH = re.compile(r"^(?:[a-zA-Z]:[\\/]|[\\/]|\.{1,2}[\\/]|~[\\/])?[^\s\"'|<>]+[\\/][^\s\"'|<>]*\.[A-Za-z0-9]{1,8}$")


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        """One connection per thread; WAL lets the recorder write while the canvas reads."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- writing (the recorder's thread) ------------------------------------------------
    def open_session(self, ts: float) -> int:
        with self.connect() as conn:
            return conn.execute("INSERT INTO sessions (started_at, last_activity) VALUES (?, ?)", (ts, ts)).lastrowid

    def reopen(self, session_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET ended_at = NULL WHERE id = ?", (session_id,))

    def close_session(self, session_id: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET ended_at = last_activity WHERE id = ?", (session_id,))

    def close_all_open(self) -> None:
        """After a crash, sessions left open end at their last activity."""
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET ended_at = last_activity WHERE ended_at IS NULL")

    def latest(self) -> sqlite3.Row | None:
        return self.connect().execute(
            f"SELECT {SESSION_COLUMNS} FROM sessions ORDER BY last_activity DESC LIMIT 1").fetchone()

    def get(self, session_id: int) -> sqlite3.Row | None:
        return self.connect().execute(f"SELECT {SESSION_COLUMNS} FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def exists(self, session_id: int) -> bool:
        return self.get(session_id) is not None

    def add_event(self, session_id: int, ts: float, kind: str, text: str = "", task: str | None = None,
                  backend: str | None = None, tool: str | None = None, data: Any = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events (session_id, ts, kind, task, backend, tool, text, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, ts, kind, task, backend, tool, text or "",
                 json.dumps(data, ensure_ascii=False, default=str) if data is not None else None))

    def add_run(self, session_id: int, ts: float, task: str | None, graph: dict[str, Any]) -> None:
        status = next((n.get("status", "done") for n in graph.get("nodes", []) if n.get("kind") == "result"), "done")
        with self.connect() as conn:
            conn.execute("INSERT INTO runs (session_id, ts, task, label, status, graph) VALUES (?, ?, ?, ?, ?, ?)",
                         (session_id, ts, task, graph.get("label", ""), status,
                          json.dumps(graph, ensure_ascii=False, default=str)))

    def touch(self, session_id: int, ts: float, *, title: str | None = None, requests: int = 0, results: int = 0,
              failures: int = 0, actions: int = 0, cost: float = 0.0, backend: str | None = None,
              projects: list[str] | None = None) -> None:
        conn = self.connect()
        with conn:
            row = conn.execute("SELECT title, backends, projects FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return
            backends = _merge(row["backends"], [backend] if backend else [])
            tags = _merge(row["projects"], projects or [])
            conn.execute(
                """UPDATE sessions SET last_activity = MAX(last_activity, ?), title = ?,
                       requests = requests + ?, results = results + ?, failures = failures + ?,
                       actions = actions + ?, cost_usd = cost_usd + ?, backends = ?, projects = ?
                   WHERE id = ?""",
                (ts, row["title"] or (title or ""), requests, results, failures, actions, cost,
                 backends, tags, session_id))

    def prune(self, keep_days: float) -> int:
        cutoff = time.time() - keep_days * 86400
        with self.connect() as conn:
            return conn.execute("DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,)).rowcount

    def delete(self, session_id: int) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

    # -- reading (any thread) -----------------------------------------------------------
    def sessions(self, limit: int = 50, before: float | None = None, query: str = "",
                 since: float | None = None) -> tuple[list[dict[str, Any]], bool]:
        where, params = [], []
        if before is not None:
            where.append("s.started_at < ?")
            params.append(before)
        if since is not None:
            where.append("s.last_activity >= ?")
            params.append(since)
        query = " ".join(query.split())
        if len(query) >= 3:
            where.append("(s.title LIKE ? ESCAPE '\\' OR s.id IN (SELECT e.session_id FROM events_fts f "
                         "JOIN events e ON e.id = f.rowid WHERE events_fts MATCH ?))")
            params += [f"%{_like(query)}%", '"' + query.replace('"', '""') + '"']
        elif query:
            where.append("s.title LIKE ? ESCAPE '\\'")
            params.append(f"%{_like(query)}%")
        sql = (f"SELECT {', '.join('s.' + c.strip() for c in SESSION_COLUMNS.split(','))} FROM sessions s"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY s.started_at DESC LIMIT ?")
        rows = self.connect().execute(sql, [*params, limit + 1]).fetchall()
        return [session_dict(r) for r in rows[:limit]], len(rows) > limit

    def session(self, session_id: int) -> dict[str, Any] | None:
        conn = self.connect()
        row = conn.execute(f"SELECT {SESSION_COLUMNS} FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        events = [dict(r) for r in conn.execute(
            "SELECT id, ts, kind, task, backend, tool, text, data FROM events WHERE session_id = ? ORDER BY ts, id LIMIT ?",
            (session_id, MAX_EVENTS + 1))]
        for event in events:
            event["data"] = json.loads(event["data"]) if event["data"] else None
        runs = [dict(r) for r in conn.execute(
            "SELECT id, ts, task, label, status FROM runs WHERE session_id = ? ORDER BY ts, id", (session_id,))]
        return {"session": session_dict(row), "events": events[:MAX_EVENTS], "truncated": len(events) > MAX_EVENTS,
                "runs": runs, "files": files_changed(events)}

    def run(self, run_id: int) -> dict[str, Any] | None:
        row = self.connect().execute("SELECT graph FROM runs WHERE id = ?", (run_id,)).fetchone()
        return json.loads(row["graph"]) if row else None


def session_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["backends"] = json.loads(d["backends"] or "[]")
    d["projects"] = json.loads(d["projects"] or "[]")
    d["live"] = d["ended_at"] is None
    d["cost_usd"] = round(d["cost_usd"], 4)
    return d


def files_changed(events: list[dict[str, Any]]) -> list[str]:
    """Paths that agents wrote or edited during the session, in first-touched order."""
    seen: dict[str, None] = {}
    for e in events:
        text = (e.get("text") or "").strip()
        if e["kind"] == "tool" and _WRITE_TOOL.search(e.get("tool") or "") and _PATH.match(text):
            seen.setdefault(text, None)
    return list(seen)


def _merge(stored: str, new: list[str]) -> str:
    values = json.loads(stored or "[]")
    for value in new:
        if value and value not in values:
            values.append(value)
    return json.dumps(values, ensure_ascii=False)


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
