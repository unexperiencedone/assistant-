"""Turn "tomorrow 10am", "today 17:30", "fri 9" or "2026-10-09 10:00" into a datetime.

Models are told the date and asked for ISO, but people (and the CLI) type the short
forms, so both work. Anything that cannot be read raises ValueError with the reason:
a reminder at a guessed time is worse than a question.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIME = re.compile(r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)?", re.I)
_NAMED = {"noon": (12, 0), "midday": (12, 0), "midnight": (0, 0), "morning": (9, 0),
          "afternoon": (14, 0), "evening": (18, 0), "night": (21, 0), "tonight": (21, 0)}


def _day(text: str, today: date) -> tuple[date | None, str]:
    """The day named in `text`, and `text` with that part removed."""
    lowered = text.lower()
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", lowered)
    if match:
        return date(*map(int, match.groups())), lowered.replace(match.group(0), " ")
    for word, offset in (("day after tomorrow", 2), ("tomorrow", 1), ("tmrw", 1), ("today", 0), ("tonight", 0)):
        if word in lowered:
            return today + timedelta(days=offset), lowered.replace(word, " " if word != "tonight" else " night ")
    match = re.search(r"\b(?:next\s+)?(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", lowered)
    if match:
        ahead = (_WEEKDAYS.index(match.group(1)) - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead), lowered.replace(match.group(0), " ")
    return None, lowered


def parse_when(text: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    raw = (text or "").strip()
    if not raw:
        raise ValueError("no time given")
    try:  # ISO first: what models send
        return datetime.fromisoformat(raw.replace("Z", "")).replace(tzinfo=None, second=0, microsecond=0)
    except ValueError:
        pass
    day, rest = _day(raw, now.date())
    rest = rest.replace("at", " ")
    hour = minute = None
    for word, (h, m) in _NAMED.items():
        if re.search(rf"\b{word}\b", rest) and not _TIME.search(rest):
            hour, minute = h, m
    match = _TIME.search(rest)
    if match:
        hour, minute = int(match.group("h")), int(match.group("m") or 0)
        ap = (match.group("ap") or "").lower().replace(".", "")
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        elif not ap and day is None and hour < 12 and hour + 12 > now.hour and hour <= now.hour:
            hour += 12   # "at 5" said at 3 pm means 5 pm, not tomorrow morning
    if hour is None or not (0 <= hour <= 23 and 0 <= (minute or 0) <= 59):
        raise ValueError(f"couldn't read a time in {raw!r}")
    moment = datetime.combine(day or now.date(), datetime.min.time()).replace(hour=hour, minute=minute or 0)
    if day is None and moment <= now:
        moment += timedelta(days=1)   # a bare time that already passed today means tomorrow
    return moment


def parse_day(text: str, now: datetime | None = None) -> date:
    now = now or datetime.now()
    day, _ = _day(text or "today", now.date())
    if day is None:
        raise ValueError(f"couldn't read a day in {text!r}")
    return day


def spoken_time(moment: datetime) -> str:
    """'10 AM', '5:30 PM'."""
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {suffix}" if moment.minute else f"{hour} {suffix}"


def spoken_day(day: date, today: date) -> str:
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return day.strftime("%A %d %B").replace(" 0", " ")
