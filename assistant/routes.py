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
    agent:groq       a free model turn -- fast tier, no subscription spent.
    agent:openrouter the same, on the other free provider.
    agent:claude     a Claude Code turn. The only one that actually costs.

An agent turn is recorded as `agent:<backend>` because "most of it goes to Groq now" is
a different claim from "most of it never reaches a model", and the flat `agent` bucket
could not tell them apart -- which made the one sentence this file exists to settle
unanswerable from its own data. Old weeks keep their flat `agent` key and are read as
paid, since that is what could not be ruled out when they were recorded.

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
# A free model turn is not free of everything -- it costs a rate-limit slot and a second
# of latency -- but it costs no subscription, which is the thing being counted here.
FREE_AGENTS = ("agent:groq", "agent:openrouter")
PAID = ("agent", "agent:claude", "agent:antigravity")
ORDER = FREE + FREE_AGENTS + PAID


def is_free(route: str) -> bool:
    return route in FREE or route in FREE_AGENTS


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
        route = str(event.data.get("route", "") or "unknown")
        if route == "agent":
            # dispatch names the backend it handed the turn to; without it the count
            # cannot say whether the free tier is carrying anything.
            backend = str(event.data.get("backend", "") or "").strip().lower()
            route = f"agent:{backend.split()[0]}" if backend else "agent"
        self.record(route)

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
        return sum(count for route, count in counts.items() if is_free(route)) / total


def _bar(counts: dict[str, int], width: int) -> str:
    """Three bands, because there are three costs and not two.

    █ scripted -- a regex, an automation or a local command answered it. No model ran.
    ▓ free model -- Groq or OpenRouter ran, which costs a rate-limit slot and nothing else.
    ░ paid -- a Claude Code turn.

    Lumping the first two together hid the question worth asking: whether the fast tier is
    taking real work, or whether every request that isn't already a saved script goes
    straight to the subscription.
    """
    total = sum(counts.values()) or 1
    scripted = sum(count for route, count in counts.items() if route in FREE)
    cheap = sum(count for route, count in counts.items() if route in FREE_AGENTS)
    a = round(scripted / total * width)
    b = round(cheap / total * width)
    return ("█" * a + "▓" * b + "░" * max(0, width - a - b))[:width]


def chart(counter: RouteCounter, width: int = 34) -> str:
    """A week-by-week bar of scripted versus free model versus paid, in plain text.

    The shape of this chart is the whole argument of docs/overview.md section 2. If the
    dark part of the bar does not shrink, the design is not working and the document
    should say so.
    """
    weeks = sorted(counter.weeks)
    if not weeks:
        return ("Nothing counted yet. Routes are recorded from the next request onward;\n"
                "come back after a week of normal use.")

    lines = ["week        requests   █ scripted  ▓ free model  ░ paid       no cost",
             "─" * 74]
    for week in weeks:
        counts = counter.weeks[week]
        total = sum(counts.values())
        share = RouteCounter.free_share(counts) or 0.0
        lines.append(f"{week}  {total:>8}   {_bar(counts, width)}  {share * 100:5.1f}%")

    totals = counter.totals()
    overall = RouteCounter.free_share(totals) or 0.0
    lines.append("─" * 74)
    lines.append(f"overall{'':>5}{sum(totals.values()):>8}   {_bar(totals, width)}  {overall * 100:5.1f}%")
    lines.append("")
    lines.append("by route: " + "  ".join(f"{route} {totals.get(route, 0)}"
                                          for route in ORDER if totals.get(route)))
    unknown = sorted(r for r in totals if r not in ORDER)
    if unknown:
        lines.append("unrecognised: " + "  ".join(f"{route} {totals[route]}" for route in unknown))
    return "\n".join(lines)
