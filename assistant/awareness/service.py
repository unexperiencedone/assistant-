"""Activity awareness: what you were doing on this PC, and answers about it.

The service owns the store and the collector, decides what a question's time range is,
and turns the rows in that range into an answer. The plain answer is written here,
from the rows, with no model involved — a model is only asked to say it more naturally
when one is available, and it is shown the same handful of rows, never a screenshot.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..events import EventBus
from .collector import Collector
from .store import AwarenessStore

DAY = 86400


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _span(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d} m" if hours else f"{minutes} m"


def _midnight(ts: float) -> float:
    local = time.localtime(ts)
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))


WORD_HOURS = {"midnight": 24, "noon": 12, "midday": 12}


def _hour(token: str, suffix: str | None, evening: bool) -> int:
    """One end of a spoken range, as an hour of the day. 24 means the following midnight."""
    if token in WORD_HOURS:
        return WORD_HOURS[token]
    hour = int(token) % 24
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    elif suffix is None and evening and hour < 12:
        hour += 12
    return hour


def parse_range(question: str, now: float | None = None) -> tuple[float, float, str]:
    """Work out which stretch of time a question is about.

    Deliberately simple and local: named parts of the day, "between 9 and midnight",
    "last night", or the last twelve hours when the question says nothing.
    """
    now = now or time.time()
    text = (question or "").lower()
    today = _midnight(now)

    match = re.search(
        r"(?:between|from)\s+(\d{1,2}|midnight|noon|midday)(?::(\d{2}))?\s*(am|pm)?\s*"
        r"(?:and|to|till|until|-)\s*(\d{1,2}|midnight|noon|midday)(?::(\d{2}))?\s*(am|pm)?",
        text)
    if match:
        # "last night", "this evening" and a bare pm all push a bare hour into the afternoon.
        evening = any(word in text for word in ("night", "evening", "tonight", "pm"))
        base = today - DAY if ("last night" in text or "yesterday" in text) else today
        start_hour = _hour(match.group(1), match.group(3), evening)
        end_hour = _hour(match.group(4), match.group(6), evening)
        start = base + start_hour * 3600 + int(match.group(2) or 0) * 60
        end = base + end_hour * 3600 + int(match.group(5) or 0) * 60
        if end <= start:
            end += DAY
        return start, min(end, now), "that stretch"

    if "last night" in text:
        return today - DAY + 21 * 3600, today + 3 * 3600, "last night"
    if "yesterday" in text:
        return today - DAY, today, "yesterday"
    if "this morning" in text or "morning" in text:
        return today + 5 * 3600, min(today + 12 * 3600, now), "this morning"
    if "afternoon" in text:
        return today + 12 * 3600, min(today + 18 * 3600, now), "this afternoon"
    if "evening" in text or "tonight" in text:
        return today + 18 * 3600, now, "this evening"
    if "today" in text or "so far" in text:
        return today, now, "today"
    if "week" in text:
        return now - 7 * DAY, now, "the last week"
    return now - 12 * 3600, now, "the last twelve hours"


def summarise(sessions: list[dict[str, Any]], label: str) -> str:
    """The answer, written from the rows. No model, no invention: this is arithmetic."""
    working = [s for s in sessions if s["category"] != "idle"]
    if not working:
        return f"Nothing was recorded for {label}."
    by_app: dict[str, float] = {}
    contexts: dict[str, set[str]] = {}
    for session in working:
        by_app[session["app"]] = by_app.get(session["app"], 0) + session["seconds"]
        if session["context"]:
            contexts.setdefault(session["app"], set()).add(session["context"])
    ranked = sorted(by_app.items(), key=lambda item: item[1], reverse=True)
    total = sum(by_app.values())
    idle = sum(s["seconds"] for s in sessions if s["category"] == "idle")

    parts = []
    for app, seconds in ranked[:3]:
        where = sorted(contexts.get(app, set()))[:2]
        detail = f" ({', '.join(where)})" if where else ""
        parts.append(f"{_span(seconds)} in {app}{detail}")
    first, last = working[0], working[-1]
    lead = f"From {_clock(first['start'])} to {_clock(last['end'])} {label}, you spent "
    tail = f". {len(working)} stretches in all"
    if idle > 60:
        tail += f", plus {_span(idle)} idle"
    return lead + "; ".join(parts) + tail + f" ({_span(total)} active)."


def rows_for_model(sessions: list[dict[str, Any]], limit: int = 40) -> str:
    """The compact table a model is shown — twenty-odd lines, never images."""
    lines = []
    for session in sessions[-limit:]:
        lines.append(f"{_clock(session['start'])}-{_clock(session['end'])} | {session['app']} | "
                     f"{session['context'] or '-'} | {_span(session['seconds'])}")
    return "\n".join(lines)


class AwarenessService:
    def __init__(self, path: Path, bus: EventBus, settings: Any,
                 narrate: Callable[[str], str] | None = None) -> None:
        self.settings = settings
        self.bus = bus
        self.narrate = narrate
        self.store = AwarenessStore(path)
        self.collector = Collector(self.store, settings, on_change=self._changed)
        self._lock = threading.Lock()
        self._revision = 0
        self._last_push = 0.0
        self.store.prune(float(getattr(settings, "raw_days", 7)), float(getattr(settings, "session_days", 19)))

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        """Watching is opt-in: it only starts here if the config says so."""
        if getattr(self.settings, "enabled", False) and getattr(self.settings, "watch_on_start", False):
            self.watch(True)
        else:
            self._publish()

    def stop(self) -> None:
        self.collector.stop()

    def watch(self, on: bool) -> dict[str, Any]:
        if on and getattr(self.settings, "enabled", False):
            self.collector.start()
            self.bus.log("Watching this PC's activity: app and window titles only.")
        else:
            self.collector.stop()
            if not on:
                self.bus.log("Stopped watching this PC's activity.")
        self._publish()
        return self.state()

    # -- reading ----------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self._lock:
            revision = self._revision
        return {
            "enabled": bool(getattr(self.settings, "enabled", False)),
            "watching": self.collector.running,
            "revision": revision,
            "retention": {
                "raw_days": getattr(self.settings, "raw_days", 7),
                "session_days": getattr(self.settings, "session_days", 19),
            },
        }

    def day(self, when: float | None = None) -> dict[str, Any]:
        now = time.time()
        start = _midnight(when or now)
        sessions = self.store.sessions_between(start, now)
        live_from = now - float(getattr(self.settings, "poll_seconds", 2)) * 3
        for session in sessions:
            session["live"] = session["end"] >= live_from
        tracked = sum(s["seconds"] for s in sessions if s["category"] != "idle")
        return {
            "sessions": sessions,
            "tracked": tracked,
            "switches": self.store.switches_between(start, now),
            "coding": self._coding(sessions),
        }

    def _coding(self, sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
        """What this coding stretch has touched so far — files, not yesterday's recap."""
        editing = [s for s in sessions if s["category"] == "editor"]
        if not editing:
            return None
        recent = [s for s in editing if s["end"] >= time.time() - 4 * 3600]
        if not recent:
            return None
        projects: dict[str, float] = {}
        files: list[str] = []
        for session in recent:
            head, _, project = session["context"].partition(" · ")
            projects[project or session["app"]] = projects.get(project or session["app"], 0) + session["seconds"]
            if head and head not in files:
                files.append(head)
        project = max(projects.items(), key=lambda item: item[1])[0] if projects else ""
        return {"project": project, "files": files[:8], "since": recent[0]["start"]}

    def ask(self, question: str) -> dict[str, Any]:
        """Answer a question about a stretch of time, from the rows in it."""
        start, end, label = parse_range(question)
        sessions = self.store.sessions_between(start, end)
        plain = summarise(sessions, label)
        answer, backend = plain, ""
        if self.narrate and sessions:
            prompt = (
                "Here is a record of which apps someone had in front of them, one line per stretch:\n"
                f"{rows_for_model(sessions)}\n\n"
                f"In three or four sentences, say what they were doing {label}. "
                "Use only what is in the rows — no guessing about what they were working on beyond the "
                "file and page names shown, and no invented detail."
            )
            try:
                spoken = self.narrate(prompt)
                if spoken:
                    answer, backend = spoken.strip(), "narrated"
            except Exception:
                pass  # the plain answer is already correct; a model is only a nicety
        return {
            "question": question,
            "answer": answer,
            "plain": plain,
            "rows": len(sessions),
            "from": start,
            "to": end,
            "backend": backend,
        }

    def forget(self) -> dict[str, Any]:
        self.store.forget_all()
        self.bus.log("Deleted every recorded activity event and session.")
        self._publish()
        return {"ok": True}

    # -- telling the canvas ------------------------------------------------------------
    def _changed(self) -> None:
        # The collector ticks every couple of seconds; the canvas only needs to hear
        # about it now and then.
        now = time.time()
        if now - self._last_push < 20:
            return
        self._last_push = now
        self._publish()

    def _publish(self) -> None:
        with self._lock:
            self._revision += 1
            revision = self._revision
        self.bus.publish("awareness", revision=revision, watching=self.collector.running,
                         enabled=bool(getattr(self.settings, "enabled", False)))
