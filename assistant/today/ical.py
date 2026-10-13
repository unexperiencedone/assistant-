"""Just enough iCalendar to know what is on a day.

Google Calendar's secret address, Outlook's published calendar and the public holiday
calendars all speak the same text format. This reads events and expands the recurrence
rules people actually use (daily, weekly on given days, monthly, yearly; INTERVAL,
COUNT, UNTIL, EXDATE). Anything stranger is read as its first occurrence rather than
guessed at.

Times come back as naive local datetimes, which is what everything else here compares.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

_DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_MAX_STEPS = 2000          # a malformed rule must not spin forever


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\")).strip()


def _when(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    """(local naive datetime, all-day?)"""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d"), True
    stamp = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return stamp.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None), False
    zone = params.get("TZID")
    if zone:
        try:
            from zoneinfo import ZoneInfo

            return stamp.replace(tzinfo=ZoneInfo(zone)).astimezone().replace(tzinfo=None), False
        except Exception:
            pass   # an unknown zone name: read it as local time, which it usually is
    return stamp, False


def parse(text: str, source: str = "") -> list[dict[str, Any]]:
    """Every VEVENT, unexpanded."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {"exdates": [], "source": source}
            continue
        if line == "END:VEVENT":
            if current and "start" in current and current.get("status") != "CANCELLED":
                current.setdefault("title", "(untitled)")
                if "end" not in current:
                    current["end"] = current["start"] + (timedelta(days=1) if current["all_day"] else timedelta(hours=1))
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *raw_params = head.split(";")
        params = dict(p.split("=", 1) for p in raw_params if "=" in p)
        name = name.upper()
        try:
            if name == "DTSTART":
                current["start"], current["all_day"] = _when(value, params)
            elif name == "DTEND":
                current["end"], _ = _when(value, params)
            elif name == "SUMMARY":
                current["title"] = _unescape(value)
            elif name == "LOCATION":
                current["location"] = _unescape(value)
            elif name == "UID":
                current["uid"] = value.strip()
            elif name == "RRULE":
                current["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                for part in value.split(","):
                    current["exdates"].append(_when(part, params)[0])
            elif name == "STATUS":
                current["status"] = value.strip().upper()
            elif name == "RECURRENCE-ID":
                current["recurrence_id"] = True
        except ValueError:
            continue
    return events


def _add_months(moment: datetime, months: int) -> datetime | None:
    month = moment.month - 1 + months
    year = moment.year + month // 12
    try:
        return moment.replace(year=year, month=month % 12 + 1)
    except ValueError:      # the 31st in a 30-day month: that month has no occurrence
        return None


def _starts(event: dict[str, Any], window_end: datetime):
    """Every start time of the event, in order, up to `window_end`."""
    start: datetime = event["start"]
    rule = event.get("rrule")
    if not rule:
        yield start
        return
    freq = rule.get("FREQ", "")
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        try:
            until = _when(rule["UNTIL"], {})[0]
        except ValueError:
            until = None
    stop = min(window_end, until) if until else window_end
    emitted = 0
    if freq == "WEEKLY" and rule.get("BYDAY"):
        days = sorted({_DAYS[d[-2:]] for d in rule["BYDAY"].split(",") if d[-2:] in _DAYS})
        week = start - timedelta(days=start.weekday())
        for _ in range(_MAX_STEPS):
            for day in days:
                moment = week + timedelta(days=day)
                if moment < start:
                    continue
                if moment > stop or (count is not None and emitted >= count):
                    return
                emitted += 1
                yield moment
            week += timedelta(weeks=interval)
        return
    step = {"DAILY": timedelta(days=interval), "WEEKLY": timedelta(weeks=interval)}.get(freq)
    for n in range(_MAX_STEPS):
        if step is not None:
            moment = start + step * n
        elif freq == "MONTHLY":
            moment = _add_months(start, n * interval)
        elif freq == "YEARLY":
            moment = _add_months(start, 12 * n * interval)
        else:
            yield start
            return
        if moment is None:
            continue
        if moment > stop or (count is not None and emitted >= count):
            return
        emitted += 1
        yield moment


def occurrences(events: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Events overlapping [start, end), recurrences expanded, sorted by start."""
    found: list[dict[str, Any]] = []
    for event in events:
        length = event["end"] - event["start"]
        excluded = set(event.get("exdates", []))
        for moment in _starts(event, end):
            if moment in excluded:
                continue
            if moment + length <= start:
                continue
            if moment >= end:
                break
            found.append({
                "title": event["title"], "start": moment, "end": moment + length,
                "all_day": event.get("all_day", False), "location": event.get("location", ""),
                "uid": event.get("uid", ""), "source": event.get("source", ""),
            })
    found.sort(key=lambda e: (e["start"], e["title"]))
    return found


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)
