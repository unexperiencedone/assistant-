"""Tests for running several agent tasks at once."""

import threading
import time
import unittest
from pathlib import Path

from assistant.agent_runner import AgentRunner
from assistant.agents.base import AgentBackend, AgentEvent, AgentResult
from assistant.events import EventBus
from assistant.planning import Plan


class FakeBackend(AgentBackend):
    """An agent that just waits, so timing tells us whether tasks really overlapped."""

    name, label = "fake", "Fake"

    def __init__(self, seconds: float) -> None:
        super().__init__("fake", Path("."), False)
        self.seconds = seconds

    def build_command(self, exe, task):
        return []

    def parse(self, obj):
        return ()

    def is_available(self):
        return True

    def spawn(self):
        return FakeBackend(self.seconds)

    def run(self, prompt, on_event, cancel):
        end = time.time() + self.seconds
        while time.time() < end:
            if cancel.is_set():
                return AgentResult(False, "Cancelled.", cancelled=True)
            time.sleep(0.02)
        on_event(AgentEvent("tool", tool="Fake", text=prompt))
        return AgentResult(True, f"done: {prompt}", seconds=self.seconds)


class _Speaker:
    is_busy = False

    def say(self, text):
        pass


class ParallelTasks(unittest.TestCase):
    def setUp(self):
        self.finished: list[tuple[str, str]] = []
        self.bus, self.plan = EventBus(), Plan()
        self.runner = AgentRunner(self.bus, self.plan, _Speaker(),
                                  on_finished=lambda b, r, t: self.finished.append((t, r.summary)),
                                  max_parallel=2)

    def wait(self, timeout=10):
        deadline = time.time() + timeout
        while self.runner.running and time.time() < deadline:
            time.sleep(0.05)

    def test_two_tasks_overlap_and_finish_out_of_order(self):
        started = time.time()
        slow = self.runner.start(FakeBackend(1.5), "slow task", "slow")
        fast = self.runner.start(FakeBackend(0.2), "fast task", "fast")
        self.assertEqual(len(self.runner.active), 2)
        self.wait()
        self.assertEqual([t for t, _ in self.finished], [fast, slow], "the quick task should answer first")
        self.assertLess(time.time() - started, 1.7 * 1.5, "they ran at the same time, not one after the other")

    def test_capacity_is_respected(self):
        self.runner.start(FakeBackend(1.0), "a", "a")
        self.runner.start(FakeBackend(1.0), "b", "b")
        self.assertIsNone(self.runner.start(FakeBackend(1.0), "c", "c"), "no free slot")
        self.assertFalse(self.runner.has_capacity)
        self.wait()
        self.assertTrue(self.runner.has_capacity)

    def test_cancel_one_leaves_the_other_running(self):
        first = self.runner.start(FakeBackend(1.0), "a", "a")
        self.runner.start(FakeBackend(1.0), "b", "b")
        self.assertEqual(self.runner.cancel(task_id=first), 1)
        time.sleep(0.3)
        self.assertEqual(len(self.runner.active), 1)
        self.wait()

    def test_cancel_everything(self):
        self.runner.start(FakeBackend(2.0), "a", "a")
        self.runner.start(FakeBackend(2.0), "b", "b")
        self.assertEqual(self.runner.cancel(everything=True), 2)
        self.wait(timeout=2)
        self.assertFalse(self.runner.running)

    def test_each_task_gets_its_own_lane_in_events(self):
        tasks = []
        self.bus.subscribe("agent", lambda e: tasks.append(e.data.get("task")))
        a = self.runner.start(FakeBackend(0.2), "a", "a")
        b = self.runner.start(FakeBackend(0.2), "b", "b")
        self.wait()
        self.assertEqual({a, b}, set(t for t in tasks if t))

    def test_an_errand_may_join_a_big_job(self):
        self.runner.start(FakeBackend(0.6), "write a report about the quarter", "write a report")
        self.assertIsNotNone(self.runner.start(FakeBackend(0.2), "play some music", "play some music"),
                             "a quick errand should not have to wait")
        self.wait()

    def test_two_big_jobs_never_run_together(self):
        self.runner.start(FakeBackend(0.6), "write a report about the quarter", "write a report")
        self.assertIsNone(self.runner.start(FakeBackend(0.6), "tidy the Downloads folder", "tidy Downloads"),
                          "the second big job waits")
        self.wait()
        self.assertIsNotNone(self.runner.start(FakeBackend(0.1), "tidy the Downloads folder", "tidy Downloads"))
        self.wait()

    def test_two_errands_run_together(self):
        a = self.runner.start(FakeBackend(0.3), "open the budget spreadsheet", "open the budget")
        b = self.runner.start(FakeBackend(0.3), "play Bohemian Rhapsody", "play Bohemian Rhapsody")
        self.assertEqual(len([t for t in (a, b) if t]), 2)
        self.wait()

    def test_status_mentions_both(self):
        self.runner.start(FakeBackend(0.6), "write the report", "write the report")
        self.runner.start(FakeBackend(0.6), "play some music", "play some music")
        sentence = self.runner.status_sentence()
        self.assertIn("2 tasks are running", sentence)
        self.assertIn("write the report", sentence)
        self.wait()


if __name__ == "__main__":
    unittest.main(verbosity=2)
