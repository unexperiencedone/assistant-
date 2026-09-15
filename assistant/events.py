"""A tiny thread-safe publish/subscribe bus that decouples the modules.

Topics used across the app:
    status      {"state": "idle|listening|transcribing|thinking|speaking|working"}
    transcript  {"role": "user|assistant", "text": str}
    plan        Plan.to_dict()
    agent       {"kind": "start|text|tool|step|result|error", "backend": str, ...}
    backend     {"name": str}
    log         {"level": "info|warn|error", "text": str}
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

Handler = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    topic: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)


class EventBus:
    WILDCARD = "*"

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, handler: Handler) -> None:
        with self._lock:
            self._handlers[topic].append(handler)

    def publish(self, topic: str, **data: Any) -> None:
        event = Event(topic, data)
        with self._lock:
            handlers = [*self._handlers[topic], *self._handlers[self.WILDCARD]]
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # one broken subscriber must not break the others
                traceback.print_exc()

    # Convenience wrappers keep call sites short.
    def status(self, state: str) -> None:
        self.publish("status", state=state)

    def log(self, text: str, level: str = "info") -> None:
        self.publish("log", level=level, text=text)
