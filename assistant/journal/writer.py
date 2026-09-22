"""Turning a finished day into one short entry.

Two halves, and the order matters. `facts_for` reads the day out of the stores as
numbers and titles -- that part is arithmetic and cannot be wrong. `plain` states those
facts in Nova's own voice with no model involved at all. A model is only ever asked to
phrase the same facts more naturally, and it is shown nothing but them, so the worst a
narrated entry can be is clumsy. It cannot be a day that never happened.
"""

from __future__ import annotations

import time
from typing import Any

DAY = 86400
MAX_TITLES = 6
MAX_APPS = 3


def midnight(ts: float) -> float:
    local = time.localtime(ts)
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))


def day_key(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def day_label(day: str) -> str:
    try:
        parsed = time.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return time.strftime("%A %d %B", parsed).replace(" 0", " ")


def _span(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{minutes} minutes" if minutes != 1 else "a minute"
    return f"{hours} h {rest:02d} m"


def _list(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def facts_for(day: str, history: Any = None, awareness: Any = None) -> dict[str, Any]:
    """Everything known about one day, as plain values. No prose, no judgement."""
    start = midnight(time.mktime(time.strptime(day, "%Y-%m-%d")))
    end = start + DAY
    facts: dict[str, Any] = {
        "day": day, "label": day_label(day), "sessions": 0, "seconds": 0.0, "requests": 0,
        "failures": 0, "projects": [], "titles": [], "files": 0, "cost_usd": 0.0, "apps": [],
    }
    if history is not None:
        rows = [s for s in history.sessions(limit=200, since=start)[0] if s["started_at"] < end]
        facts["sessions"] = len(rows)
        facts["seconds"] = sum(s["last_activity"] - s["started_at"] for s in rows)
        facts["requests"] = sum(s["requests"] for s in rows)
        facts["failures"] = sum(s["failures"] for s in rows)
        facts["cost_usd"] = round(sum(s["cost_usd"] for s in rows), 4)
        facts["projects"] = list(dict.fromkeys(p for s in rows for p in s["projects"]))
        titles, files = [], set()
        for row in rows:
            detail = history.session(row["id"]) or {}
            for event in detail.get("events", []):
                if event["kind"] == "request" and event["text"] and event["text"] not in titles:
                    titles.append(" ".join(event["text"].split())[:120])
            files.update(detail.get("files") or [])
        facts["titles"] = titles[:MAX_TITLES]
        facts["files"] = len(files)
    if awareness is not None:
        seconds: dict[str, float] = {}
        for session in awareness.sessions_between(start, end):
            if session["category"] == "idle":
                continue
            seconds[session["app"]] = seconds.get(session["app"], 0.0) + session["seconds"]
        facts["apps"] = [[app, round(total)] for app, total in
                         sorted(seconds.items(), key=lambda item: item[1], reverse=True)[:MAX_APPS]]
    return facts


def empty(facts: dict[str, Any]) -> bool:
    """A day with nothing in it gets no entry. Silence is more honest than a filler line."""
    return not facts["requests"] and not facts["sessions"] and not facts["apps"]


def plain(facts: dict[str, Any]) -> str:
    """The entry Nova writes for itself, from the numbers alone."""
    if empty(facts):
        return ""
    sentences = []
    if facts["sessions"]:
        head = (f"{facts['label']}: {facts['sessions']} session"
                f"{'s' if facts['sessions'] != 1 else ''} with you, {_span(facts['seconds'])}, "
                f"{facts['requests']} request{'s' if facts['requests'] != 1 else ''}")
        if facts["failures"]:
            head += f", {facts['failures']} of which I did not finish"
        if facts["projects"]:
            head += f", on {_list(facts['projects'])}"
        sentences.append(head + ".")
    else:
        sentences.append(f"{facts['label']}: you worked, but not with me.")
    if facts["titles"]:
        sentences.append("You asked me for: " + "; ".join(facts["titles"]) + ".")
    if facts["files"]:
        sentences.append(f"I changed {facts['files']} file{'s' if facts['files'] != 1 else ''}.")
    if facts["apps"]:
        apps = [f"{app} for {_span(seconds)}" for app, seconds in facts["apps"]]
        sentences.append("Your screen was on " + _list(apps) + ".")
    return " ".join(sentences)


def prompt_for(facts: dict[str, Any]) -> str:
    """What a model is shown when it phrases the entry. It gets the facts and nothing else."""
    lines = [f"Date: {facts['label']}",
             f"Sessions with me: {facts['sessions']} ({_span(facts['seconds'])})" if facts["sessions"] else
             "Sessions with me: none",
             f"Requests: {facts['requests']}, unfinished: {facts['failures']}",
             f"Projects touched: {_list(facts['projects']) if facts['projects'] else 'none recorded'}",
             f"Files I changed: {facts['files']}"]
    if facts["titles"]:
        lines.append("What you asked me for:")
        lines += [f"  - {title}" for title in facts["titles"]]
    if facts["apps"]:
        lines.append("Apps on screen: " + _list([f"{app} ({_span(s)})" for app, s in facts["apps"]]))
    return (
        "This is your own record of one day of your work. Write the diary entry for it.\n\n"
        + "\n".join(lines)
        + "\n\nTwo or three sentences, first person, past tense, plain words. Use only the facts above: "
          "no invented projects, no invented detail, no guessing at how anyone felt about it. "
          "Do not add a heading, a preamble or a sign-off -- the entry itself is the whole reply."
    )
