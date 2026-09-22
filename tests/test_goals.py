"""Standing goals: when Nova starts something itself, and when it deliberately doesn't."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from assistant.events import EventBus
from assistant.goals import GoalsService

HOUR = 3600


class Settings:
    enabled = True
    check_seconds = 120
    quiet_from = 22
    quiet_to = 8
    one_at_a_time = True


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.bus = EventBus()
        self.settings = Settings()
        self.submitted = []
        # A fixed, comfortably awake moment: 2 pm local.
        noonish = time.localtime()
        self.now = time.mktime((noonish.tm_year, noonish.tm_mon, noonish.tm_mday, 14, 0, 0, 0, 0, -1))
        self.busy = False
        self.goals = GoalsService(self.folder / "goals.db", self.bus, self.settings,
                                  submit=lambda text, source: self.submitted.append((text, source)),
                                  busy=lambda: self.busy, clock=lambda: self.now)

    def tearDown(self):
        self.goals.stop()
        shutil.rmtree(self.folder, ignore_errors=True)


class Firing(Base):
    def test_a_due_goal_is_submitted_as_an_ordinary_request(self):
        self.goals.add("tell me what changed in my repos", "daily")
        self.goals.tick()
        self.assertEqual(self.submitted, [("tell me what changed in my repos", "goal")])

    def test_a_goal_that_is_not_due_yet_stays_quiet(self):
        self.goals.add("weekly tidy", "weekly", next_due=self.now + HOUR)
        self.assertEqual(self.goals.tick(), [])
        self.assertEqual(self.submitted, [])

    def test_a_daily_goal_fires_once_a_day_not_once_a_tick(self):
        self.goals.add("morning briefing", "daily")
        self.goals.tick()
        self.now += 60
        self.goals.tick()
        self.assertEqual(len(self.submitted), 1)
        self.now += 25 * HOUR
        self.goals.tick()
        self.assertEqual(len(self.submitted), 2)

    def test_a_week_of_missed_days_fires_once_not_seven_times(self):
        self.goals.add("morning briefing", "daily")
        self.goals.tick()               # before the laptop was closed
        self.now += 7 * 24 * HOUR       # a week later
        self.goals.tick()
        self.assertEqual(len(self.submitted), 2)

    def test_a_once_goal_retires_itself(self):
        self.goals.add("remind me about the demo", "once")
        self.goals.tick()
        self.now += 48 * HOUR
        self.goals.tick()
        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(self.goals.goals(), [])

    def test_a_backlog_goes_out_one_at_a_time(self):
        for text in ("first", "second", "third"):
            self.goals.add(text, "daily")
        self.goals.tick()
        self.assertEqual(len(self.submitted), 1)
        self.goals.tick()
        self.assertEqual(len(self.submitted), 2)


class Restraint(Base):
    def test_nothing_fires_while_you_are_mid_conversation(self):
        self.goals.add("morning briefing", "daily")
        self.busy = True
        self.assertEqual(self.goals.tick(), [])
        self.busy = False
        self.assertEqual(len(self.goals.tick()), 1)

    def test_nothing_fires_during_quiet_hours(self):
        self.goals.add("morning briefing", "daily")
        night = time.localtime(self.now)
        self.now = time.mktime((night.tm_year, night.tm_mon, night.tm_mday, 3, 0, 0, 0, 0, -1))
        self.assertFalse(self.goals.waking())
        self.assertEqual(self.goals.tick(), [])

    def test_quiet_hours_can_be_switched_off_by_making_them_equal(self):
        self.settings.quiet_from = self.settings.quiet_to = 0
        night = time.localtime(self.now)
        self.now = time.mktime((night.tm_year, night.tm_mon, night.tm_mday, 3, 0, 0, 0, 0, -1))
        self.assertTrue(self.goals.waking())

    def test_a_paused_goal_does_not_fire_until_resumed(self):
        goal = self.goals.add("morning briefing", "daily")
        self.goals.pause(goal["id"])
        self.assertEqual(self.goals.tick(), [])
        self.goals.resume(goal["id"])
        self.assertEqual(len(self.goals.tick()), 1)


class Speaking(Base):
    def test_with_nothing_standing_it_says_so_plainly(self):
        self.assertIn("only move when you ask", self.goals.spoken())

    def test_it_names_what_it_is_going_to_do(self):
        self.goals.add("tell me what changed in my repos", "daily")
        self.assertIn("tell me what changed in my repos", self.goals.spoken())


if __name__ == "__main__":
    unittest.main()
