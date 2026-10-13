"""Every turn's tokens, every request that needed none, every browser call's weight.

Three tables in data/usage.db, all written from events that already exist:

    turns    one row per agent turn: backend, model, task type, the provider's own token
             counts and (for Claude) its list-price cost. From the runner's `agent`
             result events, and from `usage` events for turns run as a delegate
             (delegate_to_claude / delegate_to_agy / escalation), which never pass
             through the runner and so used to cost money without a trace.
    free     one row per request answered without any model: a regex intent, a saved
             automation, a loose match, the classifier, a local command (`route` events).
    browser  one row per Chrome extension call: characters sent to the model, and the
             size of the page's full text it stood in for (extension.BRIDGE.on_measure).

The rows are small and kept for good: the raw session logs they are backfilled from
(backfill.py) are deleted after two weeks, and a trend needs months.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..events import EventBus
from . import normalize

log = logging.getLogger("nova.usage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    task_type TEXT NOT NULL DEFAULT '',
    request TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    seconds REAL NOT NULL DEFAULT 0,
    input INTEGER NOT NULL DEFAULT 0,
    output INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    thinking INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    rounds INTEGER NOT NULL DEFAULT 0,
    delegated INTEGER NOT NULL DEFAULT 0,   -- run for another brain, not straight from the user
    escalated INTEGER NOT NULL DEFAULT 0,   -- a free model that handed the job to Claude
    source TEXT NOT NULL DEFAULT 'live',
    key TEXT UNIQUE                         -- backfill's dedupe key (the log file)
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
CREATE TABLE IF NOT EXISTS free (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    route TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    request TEXT NOT NULL DEFAULT '',
    count INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'live',
    key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS free_ts ON free(ts);
CREATE TABLE IF NOT EXISTS browser (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    method TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    page INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS browser_ts ON browser(ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# Routes where nothing but Nova's own code answered (assistant/routes.py). "macro" is
# left out: the runner publishes it for the same request the controller already
# published as "automation", and counting both would double every automation.
FREE_ROUTES = ("intent", "automation", "loose", "loose-ask", "classifier", "local")

_SECONDS = re.compile(r"\((\d+(?:\.\d+)?)s\)\s*$")


class UsageLedger:
    def __init__(self, path: Path, bus: EventBus | None = None) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._requests: dict[str, str] = {}  # task id -> what was asked, from the start event
        self.bus = bus
        self.revision = 0
        if bus is not None:
            # From here on turns are recorded as they finish; the backfill only imports
            # logs from before this moment, or every live turn would be counted twice.
            if not self.meta("live_since"):
                self.set_meta("live_since", str(time.time()))
            bus.subscribe("agent", self._on_agent)
            bus.subscribe("usage", self._on_usage)
            bus.subscribe("route", self._on_route)

    # -- events ---------------------------------------------------------------------
    def _on_agent(self, event) -> None:
        d = event.data
        task = str(d.get("task") or "")
        kind = d.get("kind")
        if kind == "start" and task:
            self._requests[task] = str(d.get("text") or "")
        elif kind in ("result", "error") and task and isinstance(d.get("data"), dict):
            text = str(d.get("text") or "")
            match = _SECONDS.search(text)
            self.record_turn(
                normalize.backend_key(str(d.get("backend") or "")), d["data"],
                request=self._requests.pop(task, ""), ok=kind == "result", text=text,
                seconds=float(match.group(1)) if match else 0.0)

    def _on_usage(self, event) -> None:
        d = event.data
        self.record_turn(normalize.backend_key(str(d.get("backend") or "")), d.get("data") or {},
                         request=str(d.get("request") or ""), ok=bool(d.get("ok", True)),
                         text=str(d.get("text") or ""), seconds=float(d.get("seconds") or 0), delegated=True)

    def _on_route(self, event) -> None:
        d = event.data
        route = str(d.get("route") or "")
        if route in FREE_ROUTES:
            self.record_free(route, str(d.get("name") or ""), str(d.get("utterance") or ""))

    # -- writing --------------------------------------------------------------------
    def record_turn(self, backend: str, data: dict[str, Any], request: str = "", ok: bool = True,
                    text: str = "", seconds: float = 0.0, delegated: bool = False, ts: float | None = None,
                    source: str = "live", key: str | None = None) -> bool:
        from ..automation.promotion import extract_task

        row = normalize.from_result(backend, data)
        task_type, _ = extract_task(text)
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO turns (ts, backend, model, task_type, request, ok, seconds, input, output,"
                    " cache_read, cache_write, thinking, cost_usd, rounds, delegated, escalated, source, key)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ts or time.time(), backend, row["model"], task_type, _first_line(request), int(ok), seconds,
                     row["input"], row["output"], row["cache_read"], row["cache_write"], row["thinking"],
                     row["cost_usd"], row["rounds"], int(delegated), int(bool(data.get("escalated"))), source, key))
                self._db.commit()
            except sqlite3.IntegrityError:
                return False  # already recorded (a backfill run twice)
        self._changed()
        return True

    def record_free(self, route: str, name: str = "", request: str = "", count: int = 1,
                    ts: float | None = None, source: str = "live", key: str | None = None) -> bool:
        with self._lock:
            try:
                self._db.execute("INSERT INTO free (ts, route, name, request, count, source, key) VALUES (?,?,?,?,?,?,?)",
                                 (ts or time.time(), route, name, _first_line(request), count, source, key))
                self._db.commit()
            except sqlite3.IntegrityError:
                return False
        self._changed()
        return True

    def record_browser(self, method: str, sent: int, page: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO browser (ts, method, sent, page) VALUES (?,?,?,?)",
                             (time.time(), method, int(sent), int(page)))
            self._db.commit()
        self._changed()

    def _changed(self) -> None:
        self.revision += 1
        if self.bus is not None:
            self.bus.publish("usage_ledger", revision=self.revision)

    # -- reading ----------------------------------------------------------------------
    def rows(self, table: str, since: float = 0.0) -> list[dict[str, Any]]:
        assert table in ("turns", "free", "browser")
        with self._lock:
            cursor = self._db.execute(f"SELECT * FROM {table} WHERE ts >= ? ORDER BY ts", (since,))
            return [dict(r) for r in cursor.fetchall()]

    def meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, value))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _first_line(text: str, limit: int = 200) -> str:
    """The request as the user put it. Escalations append a paragraph of notes for the
    next brain; the first line is what was asked."""
    line = (text or "").strip().split("\n", 1)[0].strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"
