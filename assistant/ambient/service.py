"""Serves the briefing cache, and asks the agent to refill it.

The heavy lifting — visiting sites, reading them, summarising, citing them — is the
agent's job, not this module's. A refresh is one ordinary request through the same
path as anything you type, so it costs exactly one turn and shows up in the activity
feed and History like any other work.

The cache is a single JSON file (`data/ambient.json`):

    {"updated_at": 1726740000, "sources_read": 6,
     "weather": {"place": "Kanpur", "temp": "26°C", "text": "clear", "source": "...", "url": "..."},
     "markets": [{"name": "NIFTY 50", "change": "+0.42%", "direction": "up",
                  "points": [...], "source": "Google Finance", "url": "..."}],
     "headlines": [{"title": "...", "summary": "...", "url": "...", "source": "...", "at": 1726739000}]}

Everything in it came from a page the agent actually read; anything it could not read
is left out rather than guessed.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..events import EventBus

# What the agent is told to produce. Kept here so the prompt and the reader agree.
SCHEMA_HINT = (
    '{"sources_read": <int>, '
    '"weather": {"place": str, "temp": str, "text": str, "source": str, "url": str}, '
    '"markets": [{"name": str, "change": "+0.42%", "direction": "up"|"down"|"flat", '
    '"points": [<last 5-7 daily closes, oldest first>], "source": str, "url": str}], '
    '"headlines": [{"title": str, "summary": "<one or two lines you wrote>", '
    '"url": str, "source": str}]}'
)


class AmbientService:
    """Reads the cache, and turns "brief me" into one agent request."""

    def __init__(self, path: Path, bus: EventBus, settings: Any,
                 on_request: Callable[[str], None] | None = None) -> None:
        self.path = path
        self.bus = bus
        self.settings = settings
        self.on_request = on_request
        self._lock = threading.Lock()
        self._revision = 0
        self._status = "idle"          # idle | working
        self._asked_at = 0.0
        self._mtime = self._current_mtime()
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        """Watch the cache file, so a briefing written by the agent reaches the canvas."""
        if self._watcher is not None:
            return
        self._watcher = threading.Thread(target=self._watch, name="ambient-watch", daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        self._stop.set()

    def _watch(self) -> None:
        while not self._stop.wait(2.0):
            mtime = self._current_mtime()
            if mtime != self._mtime:
                self._mtime = mtime
                with self._lock:
                    self._status = "idle"
                self._publish()

    def _current_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    # -- reading ----------------------------------------------------------------------
    def document(self) -> dict[str, Any]:
        """The cache as the canvas shows it. Missing or unreadable reads as empty."""
        data: dict[str, Any] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        with self._lock:
            status, asked_at, revision = self._status, self._asked_at, self._revision
        return {
            "status": status,
            "revision": revision,
            "asked_at": asked_at,
            "updated_at": data.get("updated_at") or (self._mtime or None),
            "sources_read": data.get("sources_read", 0),
            "weather": data.get("weather"),
            "markets": data.get("markets", []),
            "headlines": data.get("headlines", []),
            "reading": data.get("reading", []),
        }

    def state(self) -> dict[str, Any]:
        """The small slice the canvas keeps in its snapshot."""
        with self._lock:
            return {"revision": self._revision, "status": self._status}

    # -- refreshing -------------------------------------------------------------------
    def refresh(self) -> dict[str, Any]:
        """Ask the agent for a fresh briefing. One request, one turn, then it stops."""
        if self.on_request is None:
            return {"ok": False, "error": "no agent is connected"}
        with self._lock:
            self._status = "working"
            self._asked_at = time.time()
        self._publish()
        self.on_request(self.prompt())
        return {"ok": True}

    def prompt(self) -> str:
        """What the agent is asked. Explicit about sources, and about not guessing."""
        place = getattr(self.settings, "place", "") or "my location"
        markets = ", ".join(getattr(self.settings, "markets", []) or []) or "the main indices"
        count = getattr(self.settings, "headlines", 3)
        topics = ", ".join(getattr(self.settings, "topics", []) or [])
        wanted = f" Prefer headlines about {topics}." if topics else ""
        return (
            "Compile my ambient briefing. Read the live pages yourself, then write the result to "
            f"{self.path.as_posix()} as JSON in exactly this shape: {SCHEMA_HINT}\n"
            f"Cover: current weather for {place}; {markets} (use Google Finance for the quote and the "
            "last few daily closes); "
            f"and the top {count} headlines right now.{wanted}\n"
            "Rules: every item carries the URL of the page you actually read and its source name. "
            "Write the one-line summaries yourself from the page, do not copy the site's own blurb. "
            "Leave anything out that you could not read rather than guessing it, and set "
            '"updated_at" to the current unix time and "sources_read" to how many pages you opened. '
            "Reply with one short sentence when the file is written."
        )

    def _publish(self) -> None:
        with self._lock:
            self._revision += 1
            payload = {"revision": self._revision, "status": self._status}
        self.bus.publish("ambient", **payload)
