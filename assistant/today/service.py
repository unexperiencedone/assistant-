"""What today is, kept fresh in the background and handed to every agent.

A small loop refreshes the weather, markets, news and calendars every
`refresh_minutes` over plain HTTP: no agent turn, no key. The result is cached in
`data/today.json`, so the very first request after a restart already knows the day.

`context()` is the one line every agent turn gets, so "should I take an umbrella",
"how did the market do" or "what's on tomorrow" is answerable without a lookup.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..events import EventBus
from . import ical, sources


class TodayService:
    def __init__(self, settings: Any, cache_path: Path, state_path: Path, bus: EventBus,
                 briefing_path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.cache_path = cache_path
        self.state_path = state_path
        self.briefing_path = briefing_path
        self.bus = bus
        self.clock = clock
        # Set by the app once the schedule exists: (start, end) -> ["today 10 AM: Standup", ...]
        self.agenda: Callable[[datetime, datetime], list[str]] | None = None
        self._lock = threading.Lock()
        self._refreshing = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.data: dict[str, Any] = self._load(cache_path)
        self._events: list[dict[str, Any]] = []
        self._parse_calendars(self.data.get("calendars", {}))

    # -- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None and getattr(self.settings, "enabled", True):
            self._thread = threading.Thread(target=self._loop, name="today", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        every = max(2.0, float(getattr(self.settings, "refresh_minutes", 15))) * 60
        while not self._stop.is_set():
            try:
                self.refresh_if_stale(every)
            except Exception as error:
                self.bus.log(f"Couldn't refresh today's briefing: {error}", "warn")
            self._stop.wait(60)

    # -- refreshing -------------------------------------------------------------------
    def age(self) -> float:
        return self.clock() - float(self.data.get("updated_at") or 0)

    def refresh_if_stale(self, max_age: float) -> bool:
        if self.age() < max_age:
            return False
        self.refresh()
        return True

    def calendar_urls(self) -> list[str]:
        urls = [sources.HOLIDAYS_ICS] if getattr(self.settings, "holidays", True) else []
        private = os.environ.get(getattr(self.settings, "calendar_env", "NOVA_CALENDAR_ICS"), "")
        urls += [u.strip() for u in private.split(",") if u.strip().startswith("http")]
        return urls

    def refresh(self) -> dict[str, Any]:
        """Fetch everything at once (about a second). One refresh at a time."""
        with self._refreshing:
            s = self.settings
            urls = self.calendar_urls()
            with ThreadPoolExecutor(max_workers=6) as pool:
                weather = pool.submit(sources.weather, s.lat, s.lon, s.place)
                markets = pool.submit(sources.markets, dict(s.markets))
                india = pool.submit(sources.india_news, s.india_headlines, list(s.topics))
                finance = pool.submit(sources.finance_news, s.finance_headlines)
                calendars = {url: pool.submit(sources.calendar_text, url) for url in urls}
            old = self.data
            texts = {url: (f.result() or old.get("calendars", {}).get(url)) for url, f in calendars.items()}
            data = {
                "updated_at": self.clock(),
                # A part that failed this time keeps its last good value: stale beats empty.
                "weather": weather.result() or old.get("weather"),
                "markets": markets.result() or old.get("markets", []),
                "india": india.result() or old.get("india", []),
                "finance": finance.result() or old.get("finance", []),
                "calendars": {u: t for u, t in texts.items() if t},
            }
            with self._lock:
                self.data = data
            self._parse_calendars(data["calendars"])
            self._save(self.cache_path, data)
            if getattr(s, "feed_briefing", True) and self.briefing_path is not None:
                self._feed_briefing(data)
            self.bus.publish("today", updated_at=data["updated_at"])
            return data

    def _parse_calendars(self, calendars: dict[str, str]) -> None:
        events: list[dict[str, Any]] = []
        for url, text in calendars.items():
            source = "holidays" if url == sources.HOLIDAYS_ICS else "calendar"
            try:
                events += ical.parse(text, source)
            except Exception:
                continue
        self._events = events

    def _feed_briefing(self, data: dict[str, Any]) -> None:
        """Keep the canvas briefing panel current, without an agent turn."""
        try:
            briefing = self._load(self.briefing_path)
            w = data.get("weather")
            if w:
                briefing["weather"] = {"place": w["place"], "temp": f"{round(w['now']['temp'])}°C",
                                       "text": w["now"]["text"], "source": w["source"], "url": ""}
            if data.get("markets"):
                briefing["markets"] = data["markets"]
            heads = (data.get("india") or [])[:3] + (data.get("finance") or [])[:2]
            if heads:
                briefing["headlines"] = heads
            briefing["updated_at"] = int(data["updated_at"])
            self._save(self.briefing_path, briefing)
        except Exception as error:
            self.bus.log(f"Couldn't update the briefing panel: {error}", "warn")

    # -- reading ----------------------------------------------------------------------
    def events(self, start: datetime, end: datetime, holidays: bool = True) -> list[dict[str, Any]]:
        found = ical.occurrences(self._events, start, end)
        return found if holidays else [e for e in found if e["source"] != "holidays"]

    def personal_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.events(start, end, holidays=False)

    def holidays(self, days: int = 7) -> list[str]:
        now = datetime.fromtimestamp(self.clock())
        start = datetime(now.year, now.month, now.day)
        out = []
        for event in self.events(start, start + timedelta(days=days)):
            if event["source"] != "holidays":
                continue
            delta = (event["start"].date() - start.date()).days
            when = "today" if delta == 0 else "tomorrow" if delta == 1 else event["start"].strftime("%A %d %b")
            out.append(f"{event['title']} ({when})")
        return out

    def context(self) -> str:
        """One line for every agent turn: when, where, what the weather and markets are
        doing, and what's on. About sixty tokens."""
        now = datetime.fromtimestamp(self.clock())
        parts = [f"Now: {now.strftime('%A %d %B %Y, %H:%M').replace(' 0', ' ')} (local time), "
                 f"{getattr(self.settings, 'place', '')}"]
        w = self.data.get("weather")
        if w:
            t = w["today"]
            parts.append(f"weather ({w['source']}): {round(w['now']['temp'])}°C {w['now']['text'].lower()}, "
                         f"today {t['high']:.0f}°/{t['low']:.0f}° rain {t['rain_chance'] or 0:.0f}%")
            if w.get("next_days"):
                d = w["next_days"][0]
                parts.append(f"tomorrow {d['high']:.0f}°/{d['low']:.0f}° {d['text'].lower()}")
        quotes = self.data.get("markets") or []
        if quotes:
            parts.append("markets: " + ", ".join(f"{q['name']} {q['price']:,.0f} ({q['change']})" for q in quotes[:2]))
        holidays = self.holidays(3)
        if holidays:
            parts.append("holidays: " + ", ".join(holidays[:2]))
        if self.agenda is not None:
            start = now.replace(second=0, microsecond=0)
            upcoming = self.agenda(start, datetime(now.year, now.month, now.day) + timedelta(days=2))
            parts.append("schedule: " + ("; ".join(upcoming[:4]) if upcoming else "nothing set for today or tomorrow"))
        return "[" + " | ".join(parts) + "]"

    def facts(self) -> dict[str, Any]:
        """Everything the greeting may mention, already in plain words."""
        now = datetime.fromtimestamp(self.clock())
        w = self.data.get("weather")
        weather = None
        if w:
            later = [f"{h['at']} {round(h['temp'])}° {h['text'].lower()}"
                     + (f", {h['rain_chance']:.0f}% rain" if (h.get("rain_chance") or 0) >= 20 else "")
                     for h in w.get("hours", [])[:8:2]]
            weather = {
                "now": f"{round(w['now']['temp'])}°C and {w['now']['text'].lower()}, feels like {round(w['now']['feels'])}°",
                "today": f"high {w['today']['high']:.0f}°, low {w['today']['low']:.0f}°, "
                         f"{w['today']['rain_chance'] or 0:.0f}% chance of rain. {w['today']['day']} {w['today']['night']}".strip(),
                "next_hours": later,
                "coming_days": [f"{d['date']}: {d['high']:.0f}°/{d['low']:.0f}° {d['text'].lower()}"
                                for d in w.get("next_days", [])[:3]],
                "air": f"AQI {w['now']['aqi']} ({w['now']['aqi_text'].lower()})" if w["now"].get("aqi") else "",
                "alerts": w.get("alerts", []),
            }
        return {
            "now": now.strftime("%A %d %B %Y, %I:%M %p").replace(" 0", " "),
            "part_of_day": ("early morning" if now.hour < 6 else "morning" if now.hour < 12 else
                            "afternoon" if now.hour < 17 else "evening" if now.hour < 21 else "night"),
            "place": getattr(self.settings, "place", ""),
            "weather": weather,
            "markets": [f"{q['name']} {q['price']:,.2f} ({q['change']}, as of {q['at']})"
                        for q in self.data.get("markets", [])],
            "india_news": [f"{h['title']} ({h['source']})" for h in self.data.get("india", [])],
            "finance_news": [f"{h['title']} ({h['source']})" for h in self.data.get("finance", [])],
            "holidays": self.holidays(3),
        }

    # -- greeting memory --------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        return self._load(self.state_path)

    def remember(self, **changes: Any) -> None:
        state = self._load(self.state_path)
        state.update(changes)
        self._save(self.state_path, state)

    # -- files ------------------------------------------------------------------------
    @staticmethod
    def _load(path: Path | None) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    @staticmethod
    def _save(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        temporary.replace(path)
