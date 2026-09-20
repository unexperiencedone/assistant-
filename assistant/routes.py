"""Which route answered each request, counted by week.

docs/overview.md claims Nova gets cheaper the longer you use it. That claim was
unmeasured for the whole life of the project, which is the wrong state for the one
sentence the design is organised around. This is the measurement.

Nothing here decides anything -- it subscribes to the `route` event every branch of
`controller.handle` publishes, and keeps counts. The interesting number is the share of
requests that never reached a model:

    intent      a regex matched. Instant, free.
    automation  a saved TOML matched. Instant, free.
    loose       matching.py recognised a script from a different wording. Free.
    loose-ask   the same, but it asked first.
    classifier  a free Groq model decided which script it was (assistant/classify.py).
    local       a built-in local command.
    agent       a model turn. The one that costs.

Counts live in data/routes.json, keyed by ISO week, so the file stays small forever and
a week-by-week trend falls straight out of it.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

from . import store

FREE = ("intent", "automation", "loose", "loose-ask", "classifier", "local")
PAID = ("agent",)
ORDER = FREE + PAID


def week_of(when: date | None = None) -> str:
    """ISO year-week, e.g. "2026-W38"."""
    year, week, _ = (when or date.today()).isocalendar()
    return f"{year}-W{week:02d}"


class RouteCounter:
    """Counts routes, and can say what share of requests were free."""

    def __init__(self, path: Path, bus: Any = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.weeks: dict[str, dict[str, int]] = store.load(path).get("weeks", {})
        if bus is not None:
            bus.subscribe("route", self._on_route)

    def _on_route(self, event) -> None:
        self.record(str(event.data.get("route", "") or "unknown"))

    def record(self, route: str, when: date | None = None) -> None:
        with self._lock:
            week = self.weeks.setdefault(week_of(when), {})
            week[route] = week.get(route, 0) + 1
            store.save(self.path, {"weeks": self.weeks})

    # -- reading ------------------------------------------------------------------------
    def totals(self) -> dict[str, int]:
        summed: dict[str, int] = {}
        for counts in self.weeks.values():
            for route, count in counts.items():
                summed[route] = summed.get(route, 0) + count
        return summed

    @staticmethod
    def free_share(counts: dict[str, int]) -> float | None:
        """What fraction never reached a model. None when nothing has been counted."""
        total = sum(counts.values())
        if not total:
            return None
        return sum(count for route, count in counts.items() if route in FREE) / total


def chart(counter: RouteCounter, width: int = 34) -> str:
    """A week-by-week bar of free versus paid, in plain text.

    The shape of this chart is the whole argument of docs/overview.md section 2. If the
    dark part of the bar does not grow, the design is not working and the document
    should say so.
    """
    weeks = sorted(counter.weeks)
    if not weeks:
        return ("Nothing counted yet. Routes are recorded from the next request onward;\n"
                "come back after a week of normal use.")

    lines = ["week        requests   free                                share",
             "─" * 74]
    for week in weeks:
        counts = counter.weeks[week]
        total = sum(counts.values())
        share = RouteCounter.free_share(counts) or 0.0
        filled = round(share * width)
        bar = "█" * filled + "░" * (width - filled)
        lines.append(f"{week}  {total:>8}   {bar}  {share * 100:5.1f}%")

    overall = RouteCounter.free_share(counter.totals()) or 0.0
    lines.append("─" * 74)
    lines.append(f"overall{'':>5}{sum(counter.totals().values()):>8}   "
                 f"{'█' * round(overall * width)}{'░' * (width - round(overall * width))}  "
                 f"{overall * 100:5.1f}%")
    lines.append("")
    breakdown = counter.totals()
    lines.append("by route: " + "  ".join(f"{route} {breakdown.get(route, 0)}"
                                          for route in ORDER if breakdown.get(route)))
    return "\n".join(lines)
