"""Your schedule, and Nova bringing it up before you have to ask.

Everything else waits to be spoken to. This doesn't:

- **Reminders** go out 60, 30 and 15 minutes before each item (`remind_minutes`), for
  things you told Nova and for timed events on your calendars. They fire whatever Nova
  is doing -- mid-task, mid-conversation, during quiet hours -- because a reminder that
  waits its turn is not a reminder. Spoken, put in the tray, and sent to the phone.
- **The evening check-in**: from `plan_nudge_hour`, if tomorrow is still empty, Nova
  offers once to plan it. Say no twice running and it stops asking for a week: silence
  is the default, and a nudge you keep turning down is noise (the AfterThought rule).

A reminder is announced once per threshold. If the laptop was asleep through the
60-minute mark and wakes at 40 minutes out, you get one reminder, not two.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..events import EventBus
from .store import ScheduleStore
from .when import spoken_day, spoken_time

_LEADS = [
    "Heads up: {title} {when}.",
    "Just so you know, {title} is {when}.",
    "{title}, {when}.",
    "Quick reminder: {title} {when}.",
    "Don't forget, {title} {when}.",
    "Coming up {when_bare}: {title}.",
]
_LAST_CALL = [
    "{title} in {minutes} minutes. Time to wrap up what you're on.",
    "{minutes} minutes to {title}.",
    "{title} starts in {minutes} minutes, at {at}.",
    "Nearly time: {title} in {minutes} minutes.",
]
_NUDGES = [
    "Tomorrow's still a blank page. Want to plan it now?",
    "Nothing's on your schedule for tomorrow yet. Shall we set it up?",
    "Want to lay out tomorrow before you call it a day?",
    "Tomorrow's wide open. Want to put a few things on it?",
]
PLAN_REQUEST = ("Help me set up my schedule for tomorrow. Ask me what I have on, one question at a time if "
                "needed, then add each item with its time using the schedule tool, and read the day back to me.")


def _in(minutes: float) -> str:
    whole = int(round(minutes))
    if 55 <= whole <= 65:
        return "in an hour"
    if whole >= 90:
        return f"in about {round(whole / 60)} hours"
    return f"in {whole} minutes"


class ScheduleService:
    def __init__(self, path: Path, bus: EventBus, settings: Any,
                 announce: Callable[[str], None] | None = None,
                 offer: Callable[[str, str, Callable[[bool], None]], None] | None = None,
                 calendar: Callable[[datetime, datetime], list[dict[str, Any]]] | None = None,
                 busy: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.bus = bus
        self.settings = settings
        self.announce = announce
        self.offer = offer
        self.calendar = calendar
        self.busy = busy or (lambda: False)
        self.clock = clock
        self.store = ScheduleStore(path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None or not getattr(self.settings, "enabled", True):
            return
        self._thread = threading.Thread(target=self._loop, name="schedule", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        self.store.close()

    def _loop(self) -> None:
        interval = max(5.0, float(getattr(self.settings, "check_seconds", 20)))
        while not self._stop.wait(interval):
            try:
                self.tick()
            except Exception as error:   # a reminder bug must never take Nova down
                self.bus.log(f"Schedule check failed: {error}", "warn")

    # -- what's on --------------------------------------------------------------------
    def add(self, title: str, start: datetime, end: datetime | None = None, note: str = "") -> dict[str, Any]:
        title = " ".join((title or "").split())
        if not title:
            raise ValueError("an item needs a title")
        item = self.store.get(self.store.add(title, start, end, note))
        self.bus.publish("schedule", added=item["id"])
        return item

    def remove(self, item_id: int) -> bool:
        gone = self.store.remove(item_id)
        if gone:
            self.bus.publish("schedule", removed=item_id)
        return gone

    def agenda(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Your items plus your calendar's, in time order. Holidays are not appointments."""
        entries = [{**item, "kind": "nova", "key": f"nova:{item['id']}@{int(item['start'].timestamp())}",
                    "all_day": False} for item in self.store.between(start, end)]
        if self.calendar is not None:
            try:
                for event in self.calendar(start, end):
                    entries.append({**event, "kind": "calendar", "id": None,
                                    "key": f"cal:{event.get('uid') or event['title']}@{int(event['start'].timestamp())}"})
            except Exception as error:
                self.bus.log(f"Couldn't read the calendar: {error}", "warn")
        entries.sort(key=lambda e: (not e["all_day"], e["start"]))
        return entries

    def day(self, day) -> list[dict[str, Any]]:
        start = datetime(day.year, day.month, day.day)
        return self.agenda(start, start + timedelta(days=1))

    def lines(self, start: datetime, end: datetime, limit: int = 8) -> list[str]:
        """'Fri 10 AM Standup' style lines, for prompts and the greeting."""
        now = datetime.fromtimestamp(self.clock())
        out = []
        for entry in self.agenda(start, end)[:limit]:
            when = spoken_day(entry["start"].date(), now.date())
            at = "all day" if entry["all_day"] else spoken_time(entry["start"])
            minutes = (entry["start"] - now).total_seconds() / 60
            soon = ""
            if not entry["all_day"] and -5 <= minutes < 180:
                soon = " (now)" if minutes < 1 else f" (in {_in(minutes)[3:]})"
            out.append(f"{when} {at}{soon}: {entry['title']}")
        return out

    def spoken(self, day) -> str:
        today = datetime.fromtimestamp(self.clock()).date()
        entries = self.day(day)
        name = spoken_day(day, today)
        if not entries:
            return f"Nothing on your schedule {name}." if name in ("today", "tomorrow") else \
                f"Nothing on your schedule for {name}."
        parts = [f"{'all day' if e['all_day'] else spoken_time(e['start'])}, {e['title']}" for e in entries]
        lead = name[0].upper() + name[1:]
        return f"{lead}: " + "; ".join(parts) + "."

    # -- the proactive part -----------------------------------------------------------
    def tick(self) -> list[str]:
        """Announce whatever reminder is due right now. Returns what was said."""
        now_ts = self.clock()
        now = datetime.fromtimestamp(now_ts)
        thresholds = sorted({int(m) for m in getattr(self.settings, "remind_minutes", [60, 30, 15]) if int(m) > 0},
                            reverse=True)
        said: list[str] = []
        if thresholds:
            horizon = now + timedelta(minutes=thresholds[0] + 1)
            for entry in self.agenda(now, horizon):
                if entry["all_day"] or (entry["kind"] == "calendar"
                                        and not getattr(self.settings, "calendar_reminders", True)):
                    continue
                minutes = (entry["start"] - now).total_seconds() / 60
                due = [m for m in thresholds if minutes <= m]
                if minutes <= 0 or not due:
                    continue
                done = self.store.marks(entry["key"])
                if min(due) in done:
                    continue
                text = self._phrase(entry, minutes, min(due) == thresholds[-1])
                self.store.mark(entry["key"], done | set(due))
                said.append(text)
                self.bus.publish("reminder", title=entry["title"], minutes=round(minutes),
                                 at=entry["start"].isoformat(timespec="minutes"))
                if self.announce is not None:
                    self.announce(text)
        self._maybe_nudge(now)
        return said

    def _phrase(self, entry: dict[str, Any], minutes: float, last: bool) -> str:
        at = spoken_time(entry["start"])
        if last:
            return random.choice(_LAST_CALL).format(title=entry["title"], minutes=int(round(minutes)), at=at)
        when = f"{_in(minutes)}, at {at}"
        text = random.choice(_LEADS).format(title=entry["title"], when=when, when_bare=_in(minutes))
        return text[0].upper() + text[1:]

    def _maybe_nudge(self, now: datetime) -> None:
        hour = int(getattr(self.settings, "plan_nudge_hour", 21))
        if hour < 0 or self.offer is None or not (hour <= now.hour < 23):
            return
        today = now.date().isoformat()
        if self.store.meta("nudged_on") == today:
            return
        if self.store.meta("nudge_snoozed_until") > today or self.busy():
            return
        if self.day(now.date() + timedelta(days=1)):
            return
        self.store.set_meta("nudged_on", today)
        self.offer(random.choice(_NUDGES), PLAN_REQUEST, self._nudge_answered)

    def _nudge_answered(self, accepted: bool) -> None:
        """Two no's in a row: stop asking for a week."""
        if accepted:
            self.store.set_meta("nudge_declined", "0")
            return
        declined = int(self.store.meta("nudge_declined", "0") or 0) + 1
        self.store.set_meta("nudge_declined", str(declined))
        if declined >= 2:
            until = (datetime.fromtimestamp(self.clock()).date() + timedelta(days=7)).isoformat()
            self.store.set_meta("nudge_snoozed_until", until)
            self.store.set_meta("nudge_declined", "0")
