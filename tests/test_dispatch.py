"""What Nova says and does when a second request arrives while it is busy."""

import time
import unittest
from types import SimpleNamespace

from assistant.agent_runner import AgentRunner
from assistant.agents.base import AgentResult
from assistant.controller import Controller
from assistant.events import EventBus
from assistant.planning import Plan
from assistant.planning.plan import PlanStep
from test_runner import FakeBackend, _Speaker


class _Registry:
    """Just enough of AgentRegistry for dispatch()."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.current = FakeBackend(seconds)

    def spawn_current(self):
        return FakeBackend(self.seconds)


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.said: list[str] = []
        self.bus, self.plan = EventBus(), Plan()
        speaker = _Speaker()
        speaker.say = self.said.append
        self.runner = AgentRunner(self.bus, self.plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova"))
        self.controller = Controller(settings, self.bus, speaker, _Registry(0.6), self.plan, self.runner,
                                     request_quit=lambda: None, show_canvas=lambda: True)

    def tearDown(self):
        self.runner.cancel(everything=True)

    def wait(self, timeout=5):
        deadline = time.time() + timeout
        while self.runner.running and time.time() < deadline:
            time.sleep(0.05)

    def test_an_errand_starts_alongside_a_big_job(self):
        self.controller.dispatch("write a report about last quarter")
        self.controller.dispatch("play some music")
        self.assertEqual(2, len(self.runner.active))
        self.assertTrue(any("alongside" in s for s in self.said), self.said)
        self.assertEqual([], self.controller.waiting)

    def test_a_second_big_job_is_queued_with_a_reason(self):
        self.controller.dispatch("write a report about last quarter")
        self.controller.dispatch("tidy the Downloads folder")
        self.assertEqual(1, len(self.runner.active))
        self.assertEqual(["tidy the Downloads folder"], self.controller.waiting)
        self.assertTrue(any("big one" in s for s in self.said), self.said)

    def test_the_queued_big_job_starts_when_the_first_ends(self):
        self.controller.dispatch("write a report about last quarter")
        self.controller.dispatch("tidy the Downloads folder")
        self.wait()  # the first job ends; the runner reports it on the main loop
        self.controller.on_agent_finished(FakeBackend(0), AgentResult(True, "done"), "t1")
        self.assertEqual([], self.controller.waiting, "the queued job should have started")
        self.assertEqual(1, len(self.runner.active))

    def test_a_long_reply_is_spoken_short_but_shown_whole(self):
        shown = []
        self.bus.subscribe("transcript", lambda e: shown.append(e.data) if e.data["role"] == "assistant" else None)
        reply = ("The Reserve Bank transfers the money to the banks. " * 12).strip()
        self.controller.on_agent_finished(FakeBackend(0), AgentResult(True, reply), "t9")
        self.assertEqual(reply, shown[-1]["text"], "the canvas must keep the whole reply")
        self.assertLess(len(self.said[-1]), len(reply), "speech should be the short version")
        self.assertNotIn("...", shown[-1]["text"])

    def test_a_plan_reply_keeps_its_prompt_in_both(self):
        self.controller.plan.steps = [PlanStep("do the thing")]
        shown = []
        self.bus.subscribe("transcript", lambda e: shown.append(e.data) if e.data["role"] == "assistant" else None)
        self.controller.on_agent_finished(
            FakeBackend(0), AgentResult(True, "Here's the plan.\n[[PLAN: A plan]]\n1. do the thing\n[[/PLAN]]"), "t9")
        self.assertIn("Say go ahead", shown[-1]["text"])
        self.assertIn("Say go ahead", self.said[-1])
        self.assertIn("1. do the thing", shown[-1]["text"])  # the reply is kept exactly as the agent wrote it
        self.assertNotIn("[[PLAN", self.said[-1])           # ...but markup is never read aloud


if __name__ == "__main__":
    unittest.main(verbosity=2)
