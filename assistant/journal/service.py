"""The journal: Nova's own continuous record of its days.

Why this exists. A persona in a system prompt is re-read from scratch every session,
so it produces the same *manner* every time but no *continuity* -- yesterday is gone.
The entries written here are the other half: a short, factual account of each finished
day, put back into every agent's instructions, so a new session starts knowing what the
last few days held rather than meeting you for the first time.

It writes about a day only once that day is over, and only when something actually
happened in it. See docs/journal.md.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..events import EventBus
from . import writer
from .store import JournalStore

MAX_NARRATED = 800  # a narrated entry longer than this has wandered off; keep the plain one


class JournalService:
    def __init__(self, path: Path, bus: EventBus, settings: Any, history: Any = None,
                 awareness: Any = None, narrate: Callable[[str], str] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.bus = bus
        self.settings = settings
        self.history = history
        self.awareness = getattr(awareness, "store", awareness)
        self.narrate = narrate
        self.clock = clock
        self.store = JournalStore(path)
        self.store.prune(float(getattr(settings, "keep_days", 730)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="journal", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        self.store.close()

    def _loop(self) -> None:
        # Catch up straight away: after the laptop has been off for two days, the first
        # thing Nova should do is work out what it missed.
        interval = max(60.0, float(getattr(self.settings, "check_minutes", 30)) * 60)
        while not self._stop.is_set():
            try:
                self.catch_up()
            except Exception as error:  # a diary must never take the assistant down with it
                self.bus.log(f"Could not write my journal: {error}", "warn")
            self._stop.wait(interval)

    # -- writing ----------------------------------------------------------------------
    def catch_up(self) -> list[str]:
        """Write an entry for every finished day that hasn't got one yet."""
        now = self.clock()
        written = []
        backfill = max(1, int(getattr(self.settings, "backfill_days", 3)))
        for offset in range(backfill, 0, -1):  # oldest first, so the entries read in order
            day = writer.day_key(now - offset * writer.DAY)
            if self.store.has(day):
                continue
            if self.write(day):
                written.append(day)
        return written

    def write(self, day: str) -> dict[str, Any] | None:
        """One day, from the stores. Returns the entry, or None for a day with nothing in it."""
        facts = writer.facts_for(day, self.history, self.awareness)
        if writer.empty(facts):
            return None
        text, source = writer.plain(facts), "plain"
        spoken = self._narrated(facts)
        if spoken:
            text, source = spoken, "narrated"
        self.store.save(day, text, facts, source)
        self.bus.publish("journal", day=day, source=source, entries=self.store.count())
        self.bus.log(f"Wrote my journal entry for {facts['label']}.")
        return self.store.get(day)

    def _narrated(self, facts: dict[str, Any]) -> str:
        """The same facts, phrased by a free model. Anything doubtful is thrown away."""
        if self.narrate is None or not getattr(self.settings, "narrate", True):
            return ""
        try:
            text = " ".join((self.narrate(writer.prompt_for(facts)) or "").split())
        except Exception:
            return ""  # the plain entry is already true; phrasing is a nicety
        return text if 0 < len(text) <= MAX_NARRATED else ""

    def forget(self) -> dict[str, Any]:
        self.store.forget_all()
        self.bus.log("Deleted every journal entry I had written.")
        self.bus.publish("journal", day="", source="", entries=0)
        return {"ok": True}

    # -- reading ----------------------------------------------------------------------
    def entries(self, limit: int = 7) -> list[dict[str, Any]]:
        return self.store.recent(limit)

    def context(self, days: int | None = None) -> str:
        """The continuity block that goes into every agent's instructions.

        Kept to a handful of lines on purpose: this is here so Nova remembers what it
        was in the middle of, not so it can recite its diary at you.
        """
        limit = days if days is not None else int(getattr(self.settings, "context_days", 3))
        entries = [e for e in self.store.recent(limit) if e["text"]]
        if not entries:
            return ""
        lines = "\n".join(f"- {e['text']}" for e in reversed(entries))
        return ("My own record of the last few days, written from what actually happened:\n"
                f"{lines}\n"
                "This is for continuity -- so you know what we were in the middle of. "
                "Don't recite it back unless you're asked about it.")

    def state(self) -> dict[str, Any]:
        latest = self.store.recent(1)
        return {
            "enabled": bool(getattr(self.settings, "enabled", True)),
            "entries": self.store.count(),
            "latest": latest[0]["day"] if latest else "",
        }
