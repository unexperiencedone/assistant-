"""Which route answered, and whether it cost anything.

`docs/overview.md` organises the whole design around one sentence: Nova gets cheaper the
longer you use it. `assistant/routes.py` is the measurement of that sentence, and it had
two faults that made it unable to settle the claim it exists for.

First, every model turn was counted twice -- `controller.handle` published an anonymous
`agent` route and then `dispatch` published the real one -- so the measured free share
read about half of what it was. Second, the `agent` bucket did not say which agent, so
"most of it goes to Groq now" and "most of it goes to Claude now" produced identical
data. Both are regression-tested here.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from assistant.agent_runner import AgentRunner
from assistant.controller import Controller
from assistant.events import EventBus
from assistant.planning import Plan
from assistant.routes import RouteCounter, chart, is_free
from test_runner import FakeBackend, _Speaker


def counter(bus=None) -> RouteCounter:
    path = Path(tempfile.mkdtemp()) / "routes.json"
    return RouteCounter(path, bus)


class NamingTheBackend(unittest.TestCase):
    """An agent turn is recorded as agent:<backend>, or the cost claim is unanswerable."""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.routes = counter(self.bus)

    def test_a_free_model_turn_is_named(self) -> None:
        self.bus.publish("route", utterance="what is the weather", route="agent", backend="Groq")
        self.assertEqual(1, self.routes.totals().get("agent:groq"))

    def test_a_claude_turn_is_named(self) -> None:
        self.bus.publish("route", utterance="fix the bug", route="agent", backend="Claude Code")
        self.assertEqual(1, self.routes.totals().get("agent:claude"))

    def test_a_backend_that_did_not_say_stays_flat(self) -> None:
        self.bus.publish("route", utterance="something", route="agent")
        self.assertEqual(1, self.routes.totals().get("agent"))

    def test_the_free_routes_are_untouched(self) -> None:
        self.bus.publish("route", utterance="open spotify", route="intent", name="open_app")
        self.assertEqual(1, self.routes.totals().get("intent"))


class WhatCounted(unittest.TestCase):
    """A free model turn spends a rate-limit slot, not a subscription."""

    def test_a_free_model_turn_is_free(self) -> None:
        self.assertTrue(is_free("agent:groq"))
        self.assertTrue(is_free("agent:openrouter"))

    def test_a_claude_turn_is_not(self) -> None:
        self.assertFalse(is_free("agent:claude"))

    def test_a_scripted_route_is_free(self) -> None:
        for route in ("intent", "automation", "loose", "loose-ask", "classifier", "local"):
            self.assertTrue(is_free(route), route)

    def test_an_old_flat_agent_count_reads_as_paid(self) -> None:
        """It is what could not be ruled out when those weeks were recorded."""
        self.assertFalse(is_free("agent"))

    def test_the_share_uses_all_of_it(self) -> None:
        share = RouteCounter.free_share({"intent": 3, "agent:groq": 1, "agent:claude": 1})
        self.assertAlmostEqual(0.8, share)

    def test_nothing_counted_is_not_zero(self) -> None:
        """Zero percent free and nothing measured yet are different statements."""
        self.assertIsNone(RouteCounter.free_share({}))


class Chart(unittest.TestCase):
    def test_it_says_so_when_there_is_nothing(self) -> None:
        self.assertIn("Nothing counted yet", chart(counter()))

    def test_three_bands(self) -> None:
        routes = counter()
        for _ in range(5):
            routes.record("intent")
        for _ in range(3):
            routes.record("agent:groq")
        routes.record("agent:claude")
        drawn = chart(routes)
        self.assertIn("█", drawn)   # scripted
        self.assertIn("▓", drawn)   # free model
        self.assertIn("░", drawn)   # paid
        self.assertIn("agent:groq 3", drawn)

    def test_an_unrecognised_route_is_shown_rather_than_hidden(self) -> None:
        routes = counter()
        routes.record("something-new")
        self.assertIn("unrecognised", chart(routes))

    def test_weeks_are_kept_apart(self) -> None:
        routes = counter()
        routes.record("intent", when=date(2026, 9, 14))
        routes.record("agent:claude", when=date(2026, 9, 21))
        self.assertEqual(2, len(routes.weeks))


class _Registry:
    def __init__(self) -> None:
        self.current = FakeBackend(0.05)

    def spawn_current(self):
        return FakeBackend(0.05)


class CountedOnce(unittest.TestCase):
    """The double count. An agent request must publish exactly one route event.

    This is the fault that made the measured free share read low for the life of the
    file: `handle` announced the route it was about to take and `dispatch` announced the
    one it took, and both were counted.
    """

    def setUp(self) -> None:
        self.bus, self.plan = EventBus(), Plan()
        speaker = _Speaker()
        speaker.say = lambda text: None
        self.runner = AgentRunner(self.bus, self.plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova"))
        self.controller = Controller(settings, self.bus, speaker, _Registry(), self.plan, self.runner,
                                     request_quit=lambda: None, show_canvas=lambda: True)
        self.seen: list[dict] = []
        self.bus.subscribe("route", lambda e: self.seen.append(dict(e.data)))

    def tearDown(self) -> None:
        self.runner.cancel(everything=True)
        deadline = time.time() + 3
        while self.runner.running and time.time() < deadline:
            time.sleep(0.05)

    def test_one_event_per_agent_request(self) -> None:
        self.controller.dispatch("write a report about last quarter")
        agents = [e for e in self.seen if e.get("route") == "agent"]
        self.assertEqual(1, len(agents), self.seen)

    def test_and_it_names_the_backend(self) -> None:
        self.controller.dispatch("write a report about last quarter")
        agents = [e for e in self.seen if e.get("route") == "agent"]
        self.assertEqual("Fake", agents[0].get("backend"))

    def test_a_queued_request_is_not_counted_until_it_runs(self) -> None:
        """It has not reached a model yet, so it has not cost anything yet."""
        self.controller.dispatch("write a report about last quarter")
        self.controller.dispatch("tidy the Downloads folder")   # a second heavy job: queued
        agents = [e for e in self.seen if e.get("route") == "agent"]
        self.assertEqual(1, len(agents), self.seen)


if __name__ == "__main__":
    unittest.main()
