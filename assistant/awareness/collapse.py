"""Turning raw focus events into something worth keeping.

Pure functions only: no Windows calls, no database. The collector uses them live and
the tests use them directly, so the part that decides what counts as "a stretch of
work" can be checked without a desktop.

The shape is the same one the plan graph already uses for agent runs: a raw stream
becomes an episodic record. A run of focus events on the same app and context becomes
a block once it outlives a dwell threshold; blocks separated by less than a short gap
become one session; idle time ends a session instead of extending it.
"""

from __future__ import annotations

from typing import Any, Iterable

# Process name (lower case, no .exe) -> what kind of work it is. The category only
# drives colour and grouping, so an unknown app is simply "other".
CATEGORIES = {
    "editor": {"code", "code - insiders", "devenv", "antigravity", "cursor", "idea64", "pycharm64",
               "sublime_text", "notepad++", "rider64", "clion64", "webstorm64", "studio64"},
    "browser": {"chrome", "msedge", "firefox", "brave", "opera", "arc", "zen"},
    "terminal": {"windowsterminal", "cmd", "powershell", "pwsh", "wt", "conhost", "alacritty"},
    "call": {"zoom", "teams", "ms-teams", "discord", "slack", "skype", "webex"},
}

# Window titles are "<what you are on> <separator> <app name>". These are the
# separators Windows apps actually use, longest first.
SEPARATORS = (" — ", " – ", " - ", " | ", " · ")

# Trailing app names worth removing from a browser title.
BROWSER_TAILS = ("Google Chrome", "Microsoft​ Edge", "Microsoft Edge", "Mozilla Firefox",
                 "Brave", "Opera", "Arc", "Zen Browser")


def classify(process: str) -> str:
    """Which category a process belongs to."""
    name = (process or "").lower().removesuffix(".exe").strip()
    for category, names in CATEGORIES.items():
        if name in names:
            return category
    return "other"


def app_name(process: str) -> str:
    """A name worth showing: "VS Code", not "code.exe"."""
    name = (process or "").lower().removesuffix(".exe").strip()
    pretty = {
        "code": "VS Code", "code - insiders": "VS Code", "devenv": "Visual Studio",
        "antigravity": "Antigravity", "cursor": "Cursor", "msedge": "Edge", "chrome": "Chrome",
        "firefox": "Firefox", "windowsterminal": "Terminal", "pwsh": "PowerShell",
        "powershell": "PowerShell", "cmd": "Command Prompt", "explorer": "File Explorer",
        "ms-teams": "Teams", "teams": "Teams",
    }
    if name in pretty:
        return pretty[name]
    return name[:1].upper() + name[1:] if name else "unknown"


def context_of(category: str, title: str) -> str:
    """What you were on inside that app: the file, the page, the folder.

    Titles are all we take — never the window's contents — so this is deliberately
    shallow: the part of the title before the app's own name.
    """
    text = " ".join((title or "").split())
    if not text:
        return ""
    if category == "browser":
        for tail in BROWSER_TAILS:
            if text.endswith(tail):
                text = text[: -len(tail)]
                break
    for separator in SEPARATORS:
        if separator in text:
            head, _, rest = text.partition(separator)
            # An editor puts the file first ("controller.py - Assisstant"); keep both
            # the file and the project, they are the useful pair.
            if category == "editor" and rest:
                return f"{head.strip()} · {rest.split(separator)[0].strip()}"
            text = head
            break
    return text.strip()[:160]


def key_of(event: dict[str, Any]) -> tuple[str, str, str]:
    return (event.get("category", "other"), event.get("app", ""), event.get("context", ""))


def collapse(events: Iterable[dict[str, Any]], *, dwell_seconds: float = 12,
             merge_gap_seconds: float = 120, poll_seconds: float = 2) -> list[dict[str, Any]]:
    """Fold a stream of focus events into sessions, newest last.

    `events` are dicts with `ts`, `app`, `context`, `category` (category "idle" for a
    stretch with no input). A block shorter than `dwell_seconds` is a flicker — Alt-Tab
    passing through — and is dropped. Two sessions of the same app and context less
    than `merge_gap_seconds` apart are one session.
    """
    blocks: list[dict[str, Any]] = []
    for event in events:
        key = key_of(event)
        ts = float(event["ts"])
        # A block only continues across a missed poll or two: two two-second visits
        # a minute apart are two flickers, not one long block.
        if blocks and key_of(blocks[-1]) == key and ts - blocks[-1]["end"] <= poll_seconds * 3:
            blocks[-1]["end"] = ts
            continue
        blocks.append({"start": ts, "end": ts, "category": key[0], "app": key[1], "context": key[2]})

    sessions: list[dict[str, Any]] = []
    for block in blocks:
        # A block is at least one poll long: the last event still represents that slice.
        seconds = max(block["end"] - block["start"], poll_seconds)
        if block["category"] != "idle" and seconds < dwell_seconds:
            continue
        # Look back past whatever happened in between: ten minutes in the same file,
        # a detour to the browser, then back to the same file is one stretch of work.
        previous = next((s for s in reversed(sessions)
                         if key_of(s) == key_of(block) and block["start"] - s["end"] <= merge_gap_seconds), None)
        if previous is not None:
            previous["end"] = block["start"] + seconds
            previous["seconds"] = previous["end"] - previous["start"]
            continue
        sessions.append({**block, "end": block["start"] + seconds, "seconds": seconds})
    return sessions
