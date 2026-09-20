"""Record what happens into timed work sessions.

A session is one stretch of activity:
  - it opens with the first request (something you say or type, or an agent/automation start)
  - every event while it's open extends it
  - it closes after `idle_seconds` without activity, but never while a task is still running
  - if Nova restarts within that idle window, the same session carries on

Deciding sessions by project would mean guessing what a request is about, so the
clock decides. Projects from your profile that you mention become tags instead.

Bus handlers only queue the event; one writer thread does all the SQLite work in
order, so recording never slows the agent's stream down.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from ..events import Event, EventBus
from .store import HistoryStore, session_dict

TOPICS = ("transcript", "route", "agent", "plan", "macro_done", "run_finished", "log")
TICK_SECONDS = 30


class HistoryRecorder:
    def __init__(self, store: HistoryStore, bus: EventBus, idle_seconds: float,
                 projects_in: Callable[[str], list[str]] | None = None,
                 clock: Callable[[], float] = time.time, threaded: bool = True) -> None:
        self.store = store
        self.bus = bus
        self.idle_seconds = idle_seconds
        self.projects_in = projects_in or (lambda text: [])
        self.clock = clock
        self.current: int | None = None
        self.revision = 0
        self._running: set[str] = set()
        self._last_plan: tuple | None = None
        self._threaded = threaded
        self._queue: queue.Queue[tuple[str, dict[str, Any], float] | None] = queue.Queue()
        self._stop = threading.Event()
        store.close_all_open()
        for topic in TOPICS:
            bus.subscribe(topic, self._on_event)
        if threaded:
            threading.Thread(target=self._write_loop, name="history-writer", daemon=True).start()
            threading.Thread(target=self._tick_loop, name="history-idle", daemon=True).start()

    # -- intake -------------------------------------------------------------------------
    def _on_event(self, event: Event) -> None:
        item = (event.topic, event.data, event.ts)
        if self._threaded:
            self._queue.put(item)
        else:
            self._process(item)

    def tick(self) -> None:
        """Close the session if it has gone idle."""
        item = ("_tick", {}, self.clock())
        if self._threaded:
            self._queue.put(item)
        else:
            self._process(item)

    def stop(self) -> None:
        """Write everything still queued and close the open session (called on quit)."""
        self._stop.set()
        if self._threaded:
            self._queue.put(None)
            self._queue.join()
        if self.current is not None:
            self.store.close_session(self.current)
            self.current = None

    def notify(self) -> None:
        """Something changed outside the recorder (a session was deleted)."""
        self._announce()

    def _write_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                self._process(item)
            except Exception as exc:  # history must never take the assistant down
                self.bus.log(f"History couldn't record an event: {exc}", "warn")
            finally:
                self._queue.task_done()

    def _tick_loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            self.tick()

    # -- processing (one thread) ----------------------------------------------------------
    def _process(self, item: tuple[str, dict[str, Any], float]) -> None:
        topic, d, ts = item
        if topic == "_tick":
            self._close_if_idle(ts)
        elif topic == "transcript":
            if d["role"] == "user":
                # Every request counts once, whatever handles it: an instant command, an
                # automation or an agent (whose route and start events follow).
                sid = self._session(ts, opens=True)
                self.store.add_event(sid, ts, "you", d["text"])
                self.store.touch(sid, ts, title=d["text"], requests=1, projects=self.projects_in(d["text"]))
                self._announce()
            elif (sid := self._session(ts)) is not None:
                self.store.add_event(sid, ts, "nova", d["text"])
                self.store.touch(sid, ts)
        elif topic == "route":
            self._on_route(d, ts)
        elif topic == "agent":
            self._on_agent(d, ts)
        elif topic == "macro_done":
            if (sid := self._session(ts)) is not None:
                self.store.add_event(sid, ts, "result" if d.get("ok") else "error", d.get("text", ""), backend="automation")
                self.store.touch(sid, ts, results=1 if d.get("ok") else 0, failures=0 if d.get("ok") else 1)
                self._announce()
        elif topic == "plan":
            self._on_plan(d, ts)
        elif topic == "run_finished":
            if (sid := self._session(ts)) is not None:
                self.store.add_run(sid, ts, d.get("lane"), d["graph"])
        elif topic == "log":
            if d.get("level") in ("warn", "error") and (sid := self._session(ts)) is not None:
                self.store.add_event(sid, ts, "log", d.get("text", ""), data={"level": d["level"]})

    def _on_route(self, d: dict[str, Any], ts: float) -> None:
        utterance = d.get("utterance", "")
        sid = self._session(ts, opens=True)
        self.store.add_event(sid, ts, "request", utterance, task=d.get("task"), backend=d.get("backend"),
                             data={"route": d.get("route"), "name": d.get("name")})
        self.store.touch(sid, ts, title=utterance, backend=d.get("backend"), projects=self.projects_in(utterance))

    def _on_agent(self, d: dict[str, Any], ts: float) -> None:
        kind, task, backend = d["kind"], d.get("task"), d.get("backend")
        if kind == "text":
            return  # streamed commentary; the result carries the final reply
        if kind == "start":
            self._running.add(str(task or ""))
            sid = self._session(ts, opens=True)
            self.store.add_event(sid, ts, "start", d.get("text", ""), task=task, backend=backend)
            self.store.touch(sid, ts, backend=backend)
            return
        sid = self._session(ts)
        if sid is None:
            return
        if kind in ("tool", "local"):
            self.store.add_event(sid, ts, "tool", d.get("text", ""), task=task,
                                 backend="local" if kind == "local" else backend, tool=d.get("tool"))
            self.store.touch(sid, ts, actions=1)
        elif kind in ("result", "error"):
            self._running.discard(str(task or ""))
            data = d.get("data") or {}
            cost = float(data.get("cost_usd") or 0)
            self.store.add_event(sid, ts, kind, d.get("text", ""), task=task, backend=backend, data=data or None)
            self.store.touch(sid, ts, results=1 if kind == "result" else 0, failures=1 if kind == "error" else 0,
                             cost=cost)
            self._announce()

    def _on_plan(self, d: dict[str, Any], ts: float) -> None:
        steps = d.get("steps") or []
        key = (d.get("title", ""), tuple(s.get("text", "") for s in steps))
        if not steps or key == self._last_plan:
            return  # step status changes are already in the graph; only record new drafts
        self._last_plan = key
        if (sid := self._session(ts)) is not None:
            self.store.add_event(sid, ts, "plan", d.get("title", ""), data=d)
            self.store.touch(sid, ts)

    def _session(self, ts: float, opens: bool = False) -> int | None:
        if self.current is not None and not self.store.exists(self.current):
            self.current = None  # deleted from the canvas while open
        if opens and self.current is not None:
            # The idle tick may not have run (the PC slept): a request after a long gap
            # still starts a new session.
            self._close_if_idle(ts)
        if self.current is not None or not opens:
            return self.current
        latest = self.store.latest()
        if latest is not None and ts - latest["last_activity"] < self.idle_seconds:
            self.store.reopen(latest["id"])  # a restart in the middle of working: same session
            self.current = latest["id"]
        else:
            self.current = self.store.open_session(ts)
        self._announce()
        return self.current

    def _close_if_idle(self, now: float) -> None:
        if self.current is None or self._running:
            return  # a long, quiet agent turn is still work
        row = self.store.get(self.current)
        if row is None:
            self.current = None
            self._announce()
        elif now - row["last_activity"] >= self.idle_seconds:
            self.store.close_session(self.current)
            self.current = None
            self._announce()

    def _announce(self) -> None:
        self.revision += 1
        row = self.store.get(self.current) if self.current is not None else None
        self.bus.publish("history", revision=self.revision, current=session_dict(row) if row else None)
