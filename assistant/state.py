"""Snapshot of everything the dashboards display, kept current from the event bus."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable

from .events import Event, EventBus

Listener = Callable[[dict[str, Any]], None]


class AppState:
    def __init__(self, bus: EventBus, assistant_name: str, backend: str, workspace: str) -> None:
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self.assistant_name = assistant_name
        self.workspace = workspace
        self.status = "idle"
        self.backend = backend
        self.conversation: deque[dict[str, Any]] = deque(maxlen=40)
        self.activity: deque[dict[str, Any]] = deque(maxlen=60)
        self.plan: dict[str, Any] = {"title": "", "steps": []}
        self.graph: dict[str, Any] = {"run": 0, "nodes": [], "edges": []}
        self.agent_running = False
        bus.subscribe(EventBus.WILDCARD, self._on_event)

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _on_event(self, event: Event) -> None:
        d = event.data
        with self._lock:
            if event.topic == "status":
                self.status = d["state"]
            elif event.topic == "transcript":
                self.conversation.append({"role": d["role"], "text": d["text"], "ts": event.ts})
            elif event.topic == "plan":
                self.plan = d
            elif event.topic == "graph":
                self.graph = d
            elif event.topic == "backend":
                self.backend = d["name"]
            elif event.topic == "agent":
                if d["kind"] == "start":
                    self.agent_running = True
                elif d["kind"] in ("result", "error"):
                    self.agent_running = False
                self.activity.append({**d, "ts": event.ts})
            elif event.topic == "log":
                self.activity.append({"kind": "log", **d, "ts": event.ts})
            else:
                return
            snapshot = self._snapshot_locked()
            listeners = list(self._listeners)
        for listener in listeners:
            listener(snapshot)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "name": self.assistant_name,
            "workspace": self.workspace,
            "status": self.status,
            "backend": self.backend,
            "agent_running": self.agent_running,
            "conversation": list(self.conversation),
            "activity": list(self.activity),
            "plan": self.plan,
            "graph": self.graph,
            "now": time.time(),
        }
