"""What the dashboards display, kept current from the event bus.

The state is a set of named slices (status, mic, conversation, graph, ...). Every
change stamps its slice with a new version number, so a client that has seen
some versions gets only the slices that changed since, and for the long lists
(conversation, activity) only the new rows. A mic level update then costs a few
bytes instead of the whole conversation and graph, and the canvas keeps its
graph object, so it doesn't lay the graph out again ten times a second.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable

from .events import Event, EventBus

Listener = Callable[[], None]

# Lists that only ever grow at the end (oldest rows fall off): sent as appended rows.
LIST_LIMITS = {"conversation": 40, "activity": 60}
MAX_DENIALS = 10


class AppState:
    def __init__(self, bus: EventBus, assistant_name: str, backend: str, workspace: str) -> None:
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._version = 0
        self._versions: dict[str, int] = {}
        self._values: dict[str, Any] = {}
        self._lists: dict[str, deque[tuple[int, dict[str, Any]]]] = {k: deque(maxlen=n) for k, n in LIST_LIMITS.items()}
        self._running: set[str] = set()   # task ids with an agent turn in flight
        self.saver = None  # assistant.store.Saver, when persistence is on
        for key, value in {
            "name": assistant_name, "workspace": workspace, "status": "idle", "backend": backend,
            "agent_running": False, "plan": {"title": "", "steps": []}, "graph": {"run": 0, "nodes": [], "edges": []},
            "cost_usd": 0.0,        # this run of Nova
            "cost_total_usd": 0.0,  # every run, kept across restarts
            "turns": 0, "denials": [], "mic": {"level": 0.0, "speaking": False}, "quick_actions": [], "queue": [],
            "profile": {"revision": 0, "name": ""},        # the form fetches the profile; this says it changed
            "history": {"revision": 0, "current": None},   # the open work session; the list is fetched
            "ambient": {"revision": 0, "status": "idle"},  # the briefing cache changed; the panel fetches it
            "awareness": {"revision": 0, "watching": False, "enabled": False},
        }.items():
            self._set(key, value)
        for key in LIST_LIMITS:
            self._versions[key] = 0
        bus.subscribe(EventBus.WILDCARD, self._on_event)

    # -- persistence ------------------------------------------------------------------
    def restore(self, saved: dict[str, Any]) -> list[str]:
        """Bring back the conversation, plan and costs from the last run. Returns the
        requests that were still queued, for the controller to offer again."""
        with self._lock:
            for message in saved.get("conversation", [])[-LIST_LIMITS["conversation"]:]:
                self._append("conversation", message)
            if saved.get("plan", {}).get("steps"):
                self._set("plan", saved["plan"])
            # Older state files only had "cost_usd", which was the running total.
            total = saved.get("cost_total_usd", saved.get("cost_usd", 0))
            self._set("cost_total_usd", round(float(total or 0), 4))
        return [str(q) for q in saved.get("queue", []) if str(q).strip()]

    def persisted(self) -> dict[str, Any]:
        with self._lock:
            return {"conversation": [row for _, row in self._lists["conversation"]], "plan": self._values["plan"],
                    "cost_total_usd": self._values["cost_total_usd"], "queue": self._values["queue"],
                    "saved_at": time.time()}

    # -- listeners ---------------------------------------------------------------------
    def add_listener(self, listener: Listener) -> None:
        """`listener()` is called (on the publishing thread) whenever something changed."""
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    # -- reading ----------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return self.snapshot_with_versions()[0]

    def snapshot_with_versions(self) -> tuple[dict[str, Any], dict[str, int]]:
        with self._lock:
            snap = dict(self._values)
            for key, rows in self._lists.items():
                snap[key] = [row for _, row in rows]
            snap["now"] = time.time()
            return snap, dict(self._versions)

    def changes_since(self, seen: dict[str, int]) -> tuple[dict[str, Any], dict[str, list], dict[str, int]]:
        """(replaced slices, appended rows per list, versions now) for a client that has `seen`."""
        replaced: dict[str, Any] = {}
        appended: dict[str, list] = {}
        with self._lock:
            for key, version in self._versions.items():
                last = seen.get(key, -1)
                if version <= last:
                    continue
                if key in self._lists:
                    appended[key] = [row for seq, row in self._lists[key] if seq > last]
                else:
                    replaced[key] = self._values[key]
            return replaced, appended, dict(self._versions)

    # -- writing ----------------------------------------------------------------------
    def dismiss_denials(self) -> None:
        with self._lock:
            self._set("denials", [])
        self._notify(persist=False)

    def _set(self, key: str, value: Any) -> bool:
        if key in self._values and self._values[key] == value:
            return False
        self._version += 1
        self._values[key] = value
        self._versions[key] = self._version
        return True

    def _append(self, key: str, row: dict[str, Any]) -> None:
        self._version += 1
        self._lists[key].append((self._version, row))
        self._versions[key] = self._version

    def _on_event(self, event: Event) -> None:
        d = event.data
        persist = False
        with self._lock:
            before = self._version
            topic = event.topic
            if topic == "status":
                self._set("status", d["state"])
            elif topic == "transcript":
                self._append("conversation", {"role": d["role"], "text": d["text"], "ts": event.ts,
                                              "origin": d.get("origin", "desktop")})
                persist = True
            elif topic == "plan":
                self._set("plan", d)
                persist = True
            elif topic == "graph":
                self._set("graph", d)
            elif topic == "backend":
                self._set("backend", d["name"])
            elif topic == "mic":
                self._set("mic", {"level": d.get("level", 0.0), "speaking": bool(d.get("speaking"))})
            elif topic == "quick_actions":
                self._set("quick_actions", d.get("items", []))
            elif topic == "profile":
                self._set("profile", {"revision": d.get("revision", 0), "name": d.get("name", "")})
            elif topic == "history":
                self._set("history", {"revision": d.get("revision", 0), "current": d.get("current")})
            elif topic == "ambient":
                self._set("ambient", {"revision": d.get("revision", 0), "status": d.get("status", "idle")})
            elif topic == "awareness":
                self._set("awareness", {"revision": d.get("revision", 0), "watching": bool(d.get("watching")),
                                        "enabled": bool(d.get("enabled"))})
            elif topic == "queue":
                self._set("queue", list(d.get("items", [])))
                persist = True
            elif topic == "agent":
                persist = self._on_agent(d, event.ts)
            elif topic == "log":
                self._append("activity", {"kind": "log", **d, "ts": event.ts})
            changed = self._version != before
        if changed:
            self._notify(persist)

    def _on_agent(self, d: dict[str, Any], ts: float) -> bool:
        """Called with the lock held. Returns True when the change should be saved."""
        task = str(d.get("task") or "")
        persist = False
        denials = list(self._values["denials"])
        if d["kind"] == "start":
            # A new request supersedes warnings about ones that already ended.
            denials = [x for x in denials if x.get("task") in self._running]
            self._running.add(task)
        elif d["kind"] in ("result", "error"):
            self._running.discard(task)
            self._set("turns", self._values["turns"] + 1)
            cost = float((d.get("data") or {}).get("cost_usd") or 0)
            if cost:
                self._set("cost_usd", round(self._values["cost_usd"] + cost, 4))
                self._set("cost_total_usd", round(self._values["cost_total_usd"] + cost, 4))
                persist = True
            for name in (d.get("data") or {}).get("denied") or []:
                denials.append({"tool": name, "ts": ts, "backend": d.get("backend", ""), "task": task})
        if d.get("tool") == "denied":
            denials.append({"tool": d.get("text", "a tool"), "ts": ts, "backend": d.get("backend", ""), "task": task})
        self._set("denials", denials[-MAX_DENIALS:])
        # Several tasks can run at once: "working" until the last one ends.
        self._set("agent_running", bool(self._running))
        self._append("activity", {**d, "ts": ts})
        return persist

    def _notify(self, persist: bool) -> None:
        with self._lock:
            listeners = list(self._listeners)
        if persist and self.saver is not None:
            self.saver.touch()
        for listener in listeners:
            listener()
