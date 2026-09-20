"""A spoken recap of a session or a day: "what did we do this session?"

Built from the stored counts and request titles, so it costs nothing and never
makes anything up.
"""

from __future__ import annotations

import time
from typing import Any

from .store import HistoryStore


def duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    parts = ([f"{hours} hour{'s' if hours != 1 else ''}"] if hours else []) + \
            ([f"{minutes} minute{'s' if minutes != 1 else ''}"] if minutes else [])
    return " ".join(parts)


def clock(ts: float) -> str:
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")


def _counts(requests: int, results: int, failures: int) -> str:
    text = f"{requests} request{'s' if requests != 1 else ''}"
    if failures:
        text += f", {failures} of them failed" if failures < requests else ", all failed"
    return text


def _projects(projects: list[str]) -> str:
    if not projects:
        return ""
    names = projects[0] if len(projects) == 1 else ", ".join(projects[:-1]) + f" and {projects[-1]}"
    return f", on {names}"


def _session_sentence(s: dict[str, Any], label: str, store: HistoryStore) -> str:
    if not s["requests"]:
        return f"{label} started at {clock(s['started_at'])}, but there were no requests in it."
    tense = "has run for" if s["live"] else "ran for"
    text = (f"{label} started at {clock(s['started_at'])} and {tense} {duration(s['last_activity'] - s['started_at'])}: "
            f"{_counts(s['requests'], s['results'], s['failures'])}{_projects(s['projects'])}.")
    detail = store.session(s["id"]) or {}
    requests = [e["text"] for e in detail.get("events", []) if e["kind"] == "request" and e["text"]]
    if requests:
        text += f" The latest was: {requests[-1]}."
    files = detail.get("files") or []
    if files:
        text += f" {len(files)} file{'s were' if len(files) != 1 else ' was'} changed."
    return text


def recap(store: HistoryStore, when: str = "", now: float | None = None) -> str:
    now = time.time() if now is None else now
    when = (when or "").lower()
    if "today" in when or "yesterday" in when:
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        start, end, label = (midnight - 86400, midnight, "Yesterday") if "yesterday" in when else (midnight, now + 1, "Today")
        sessions = [s for s in store.sessions(limit=200, since=start)[0] if s["started_at"] < end]
        if not sessions:
            return f"{label} there's nothing in the history."
        total = sum(s["last_activity"] - s["started_at"] for s in sessions)
        requests = sum(s["requests"] for s in sessions)
        failures = sum(s["failures"] for s in sessions)
        projects = list(dict.fromkeys(p for s in sessions for p in s["projects"]))
        return (f"{label} you had {len(sessions)} session{'s' if len(sessions) != 1 else ''}, "
                f"{duration(total)} in all: {_counts(requests, 0, failures)}{_projects(projects)}.")

    sessions, _ = store.sessions(limit=2)
    if not sessions:
        return "There's no history yet."
    current = sessions[0]
    if "last" in when or "previous" in when:
        previous = sessions[1] if current["live"] and len(sessions) > 1 else (None if current["live"] else current)
        return _session_sentence(previous, "The last session", store) if previous else "There's no earlier session yet."
    return _session_sentence(current, "This session" if current["live"] else "The last session", store)
