"""Watching a job handed to another app: done, waiting for you, closed, too long.

The window is faked as a list of control names that changes, the way the Antigravity
IDE's did when observed live: "Working..." and "Cancel (Ctrl+D)" while working, a new
"Worked for 21s" when finished, "Accept all" when its edits wait for review.
"""

import time
import unittest
from pathlib import Path
from unittest import mock

from assistant.automation import watch
from assistant.automation.macros import MacroRunner
from assistant.automation.watch import WatchService, WatchSpec
from assistant.events import EventBus
from assistant.watcher import Watcher

AGY = WatchSpec(label="Antigravity in shop", window="shop - Antigravity IDE",
                working=["^Working", r"^Cancel \(Ctrl\+D\)"], done=["^Worked for "],
                needs_you=[r"^Accept\b", r"^Run\b"], review=["^Accept all$"], max_minutes=45)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.window = ["Agent", "Working...", "Cancel (Ctrl+D)"]
        self.said = []
        self.live = Watcher(EventBus())
        self.service = WatchService(say=self.said.append, live=self.live,
                                    names=lambda title: None if self.window is None else list(self.window),
                                    pid_of=lambda title: None)
        self.service._ensure_thread = lambda: None   # the tests drive look() themselves

    def start(self, spec=AGY):
        return self.service.start(spec)


class Finishing(Fixture):
    def test_a_new_done_marker_with_nothing_working_is_done(self):
        job = self.start()
        job.changed |= {"src/footer.html", "src/style.css"}
        self.window = ["Agent", "Worked for 3m 12s", "Accept all"]
        self.service.look(job)
        self.assertEqual(job.state, "done")
        self.assertIn("It changed 2 files: footer.html, style.css.", self.said[-1])
        self.assertIn("waiting for you to accept", self.said[-1])

    def test_the_last_jobs_marker_in_a_reused_window_is_not_done(self):
        self.window = ["Worked for 21s", "Working...", "Cancel (Ctrl+D)"]
        job = self.start()
        self.window = ["Worked for 21s", "Agent"]        # same old marker, nothing new
        self.service.look(job)
        self.assertEqual(job.state, "working")
        self.window = ["Worked for 21s", "Worked for 40s"]
        self.service.look(job)
        self.assertEqual(job.state, "done")

    def test_the_live_view_names_it_while_it_runs_and_after(self):
        job = self.start()
        self.assertIn("Antigravity in shop is working", self.live.spoken())
        self.window = ["Worked for 9s"]
        self.service.look(job)
        self.assertIn("The last thing I finished was Antigravity in shop", self.live.spoken())


class WaitingForYou(Fixture):
    def test_an_approval_button_on_a_stalled_job_is_said_once(self):
        job = self.start()
        self.window = ["Working...", "Cancel (Ctrl+D)", "Run cmd.exe /c npm install", "Accept Ctrl+⏎"]
        job.last_event = time.time() - watch.STALL - 1
        job.started = job.last_event
        self.service.look(job)
        self.service.look(job)
        self.assertEqual(job.state, "needs_you")
        self.assertEqual(sum("waiting for you" in s for s in self.said), 1)
        self.assertIn("Run cmd.exe", self.said[-1])

    def test_an_approval_button_while_it_is_still_busy_is_not_a_question(self):
        """Antigravity shows "Accept Changes" mid-run and carries on; events mean it's busy."""
        job = self.start()
        self.window = ["Working...", "Accept Changes Ctrl+⏎"]
        job.last_event = time.time()
        self.service.look(job)
        self.assertEqual(job.state, "working")
        self.assertEqual(self.said, [])


class OtherEndings(Fixture):
    def test_a_closed_window_is_reported(self):
        job = self.start()
        self.window = None
        self.service.look(job)
        self.assertEqual(job.state, "closed")
        self.assertIn("window was closed", self.said[-1])

    def test_a_long_job_is_mentioned_once_and_watched_on(self):
        job = self.start()
        job.started = time.time() - 46 * 60
        self.service.look(job)
        self.service.look(job)
        self.assertEqual(job.state, "working")
        self.assertEqual(sum("keep watching" in s for s in self.said), 1)


class Pacing(unittest.TestCase):
    """Looks come from events settling, or a slow fallback -- never a fast loop."""

    def job(self, **kw):
        job = watch.WatchJob(id="w1", spec=AGY)
        for key, value in kw.items():
            setattr(job, key, value)
        return job

    def test_mid_burst_waits_for_the_events_to_settle(self):
        now = time.time()
        self.assertFalse(WatchService._due(self.job(dirty=True, last_event=now, last_look=now, next_fallback=now + 60), now))
        self.assertTrue(WatchService._due(self.job(dirty=True, last_event=now - 2, last_look=now, next_fallback=now + 60), now))

    def test_a_long_burst_still_gets_a_look_now_and_then(self):
        now = time.time()
        self.assertTrue(WatchService._due(self.job(dirty=True, last_event=now, last_look=now - 11, next_fallback=now + 60), now))

    def test_with_no_events_the_fallback_backs_off(self):
        said = []
        service = WatchService(say=said.append, names=lambda t: ["Working..."], pid_of=lambda t: None)
        service._ensure_thread = lambda: None
        job = service.start(AGY)
        gaps = []
        for _ in range(4):
            before = time.time()
            service.look(job)
            gaps.append(round(job.next_fallback - before))
        self.assertEqual(gaps, [10, 20, 40, 60])


class TheStep(unittest.TestCase):
    def test_the_watch_step_hands_off_and_returns(self):
        service = mock.Mock()
        service.start.return_value = mock.Mock(id="w7")
        runner = MacroRunner(EventBus(), open_app=lambda n: True, say=lambda t: None, watches=service)
        detail = runner._execute({"do": "watch", "job": "Antigravity in {path_name}", "window": "{path_name} - IDE",
                                  "done": ["^Worked for "], "folder": "{path}"},
                                 {"path": r"C:\shop", "path_name": "shop"}, None)
        spec = service.start.call_args.args[0]
        self.assertEqual((spec.label, spec.window, spec.folder), ("Antigravity in shop", "shop - IDE", r"C:\shop"))
        self.assertIn("background", detail)


if __name__ == "__main__":
    unittest.main()
