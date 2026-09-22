"""The loop that lets Nova start something without being spoken to first.

Everything else in this assistant is reactive: you speak, it answers. A standing goal
is the one place that inverts -- a sentence Nova submits to itself when it comes due,
through `app.submit`, exactly as if you had said it.

Three rules hold the whole thing together, and they are why this is safe to leave on:

1. **A goal is a request, not a privilege.** It goes through the same pipeline, so the
   confirmation in front of a text message, a call or a post is still there.
2. **It waits its turn.** Nothing fires while you are mid-conversation or an agent task
   is running. Interrupting you is worse than being an hour late.
3. **It is quiet at night.** Outside waking hours a due goal simply waits.

A goal that comes due while the laptop was off runs once when it comes back, not once
per missed interval: catching up on eleven identical requests helps nobody.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..events import EventBus
from .store import CADENCES, GoalStore


class GoalsService:
    def __init__(self, path: Path, bus: EventBus, settings: Any,
                 submit: Callable[[str, str], None] | None = None,
                 busy: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.bus = bus
        self.settings = settings
        self.submit = submit
        self.busy = busy or (lambda: False)
        self.clock = clock
        self.store = GoalStore(path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None or not getattr(self.settings, "enabled", True):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="goals", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        self.store.close()

    def _loop(self) -> None:
        interval = max(30.0, float(getattr(self.settings, "check_seconds", 120)))
        while not self._stop.is_set():
            self._stop.wait(interval)
            if self._stop.is_set():
                return
            try:
                self.tick()
            except Exception as error:  # a standing goal must never take the assistant down
                self.bus.log(f"Could not run a standing goal: {error}", "warn")

    # -- the decision -----------------------------------------------------------------
    def tick(self) -> list[dict[str, Any]]:
        """Fire whatever is due and allowed right now. Returns what was fired."""
        now = self.clock()
        if not self.waking(now) or self.busy():
            return []
        fired = []
        for goal in self.store.due(now):
            self._fire(goal, now)
            fired.append(goal)
            if getattr(self.settings, "one_at_a_time", True):
                break  # the rest are still due; they will go on the next tick
        return fired

    def _fire(self, goal: dict[str, Any], now: float) -> None:
        step = CADENCES.get(goal["cadence"], 0.0)
        # One run, not one per missed interval: the next due time is measured from now.
        self.store.ran(goal["id"], now, now + step if step else None)
        self.bus.publish("goal", id=goal["id"], text=goal["text"], cadence=goal["cadence"])
        self.bus.log(f"Standing goal: {goal['text']}")
        if self.submit is not None:
            self.submit(goal["text"], "goal")

    def waking(self, now: float | None = None) -> bool:
        """Quiet hours. `quiet_from = quiet_to` means never quiet."""
        start = int(getattr(self.settings, "quiet_from", 22)) % 24
        end = int(getattr(self.settings, "quiet_to", 8)) % 24
        if start == end:
            return True
        hour = time.localtime(now if now is not None else self.clock()).tm_hour
        return not (hour >= start or hour < end) if start > end else not (start <= hour < end)

    # -- managing ---------------------------------------------------------------------
    def add(self, text: str, cadence: str = "daily", next_due: float | None = None,
            source: str = "you") -> dict[str, Any] | None:
        text = " ".join((text or "").split())
        if not text:
            return None
        # The store must not read the wall clock here: a goal's first due time comes
        # from this service's clock, the same one `tick` compares against.
        goal_id = self.store.add(text, cadence, self.clock() if next_due is None else next_due, source)
        self.bus.log(f"I'll do this {cadence}: {text}")
        self.bus.publish("goal", id=goal_id, text=text, cadence=cadence, added=True)
        return self.store.get(goal_id)

    def pause(self, goal_id: int) -> bool:
        return self.store.set_active(goal_id, False)

    def resume(self, goal_id: int) -> bool:
        return self.store.set_active(goal_id, True)

    def remove(self, goal_id: int) -> bool:
        return self.store.remove(goal_id)

    def goals(self, include_retired: bool = False) -> list[dict[str, Any]]:
        return self.store.all(include_retired)

    def state(self) -> dict[str, Any]:
        active = self.store.all(include_retired=False)
        return {
            "enabled": bool(getattr(self.settings, "enabled", True)),
            "goals": len(active),
            "waking": self.waking(),
            "next": min((g["next_due"] for g in active), default=0.0),
        }

    def spoken(self) -> str:
        """What Nova says when asked what it is doing on its own."""
        active = self.store.all(include_retired=False)
        if not active:
            return "Nothing standing. I only move when you ask."
        lines = [f"{g['text']}, {g['cadence']}" for g in active[:5]]
        text = f"{len(active)} standing goal{'s' if len(active) != 1 else ''}: " + "; ".join(lines) + "."
        return text if self.waking() else text + " Nothing will fire until morning."
