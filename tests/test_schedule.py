"""The schedule: reminders Nova gives unasked, and the evening offer to plan tomorrow."""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from assistant.events import EventBus
from assistant.schedule import ScheduleService, parse_when
from assistant.today import ical

MIN = 60


class Settings:
    enabled = True
    remind_minutes = [60, 30, 15]
    check_seconds = 20
    calendar_reminders = True
    plan_nudge_hour = 21
    phone = False


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.said, self.offers = [], []
        self.now = datetime(2026, 10, 9, 9, 0).timestamp()
        self.calendar_events = []
        self.busy = False
        self.schedule = ScheduleService(
            self.folder / "s.db", EventBus(), Settings(), announce=self.said.append,
            offer=lambda q, r, cb: self.offers.append((q, r, cb)),
            calendar=lambda a, b: [e for e in self.calendar_events if a <= e["start"] < b],
            busy=lambda: self.busy, clock=lambda: self.now)

    def tearDown(self):
        self.schedule.stop()
        shutil.rmtree(self.folder, ignore_errors=True)

    def at(self, hour, minute=0, day=9):
        return datetime(2026, 10, day, hour, minute)


class Reminders(Base):
    def test_sixty_thirty_and_fifteen_minutes_before_each_once(self):
        self.schedule.add("Standup", self.at(11))
        fired = []
        for minutes_before in (70, 60, 59, 45, 30, 29, 16, 15, 10, 1):
            self.now = self.at(11).timestamp() - minutes_before * MIN
            fired += self.schedule.tick()
        self.assertEqual(len(fired), 3)
        self.assertEqual(len(self.said), 3)
        self.assertTrue(all("Standup" in line for line in fired))

    def test_waking_late_gives_one_reminder_not_a_backlog(self):
        self.schedule.add("Dentist", self.at(11))
        self.now = self.at(11).timestamp() - 20 * MIN   # slept through the 60 and 30 marks
        self.assertEqual(len(self.schedule.tick()), 1)
        self.now += MIN
        self.assertEqual(self.schedule.tick(), [])
        self.now = self.at(11).timestamp() - 14 * MIN
        self.assertEqual(len(self.schedule.tick()), 1)   # the 15-minute one is still its own

    def test_reminders_fire_even_while_nova_is_busy(self):
        self.busy = True
        self.schedule.add("Call", self.at(10))
        self.assertEqual(len(self.schedule.tick()), 1)

    def test_calendar_events_are_reminded_but_all_day_ones_are_not(self):
        self.calendar_events = [
            {"title": "Lecture", "start": self.at(10), "end": self.at(11), "all_day": False, "uid": "x"},
            {"title": "Holiday", "start": self.at(0), "end": self.at(0, day=10), "all_day": True, "uid": "y"},
        ]
        fired = self.schedule.tick()
        self.assertEqual(len(fired), 1)
        self.assertIn("Lecture", fired[0])

    def test_something_already_started_is_not_announced(self):
        self.schedule.add("Gym", self.at(8, 30))
        self.assertEqual(self.schedule.tick(), [])


class EveningCheckIn(Base):
    def test_an_empty_tomorrow_is_offered_once_in_the_evening(self):
        self.now = self.at(21, 5).timestamp()
        self.schedule.tick()
        self.schedule.tick()
        self.assertEqual(len(self.offers), 1)
        self.assertTrue(self.offers[0][0].endswith("?"))

    def test_no_offer_when_tomorrow_already_has_plans_or_before_the_hour(self):
        self.now = self.at(18).timestamp()
        self.schedule.tick()
        self.schedule.add("Class", self.at(9, day=10))
        self.now = self.at(21, 30).timestamp()
        self.schedule.tick()
        self.assertEqual(self.offers, [])

    def test_two_noes_in_a_row_and_it_stops_asking_for_a_week(self):
        for day in (9, 10):
            self.now = datetime(2026, 10, day, 21, 10).timestamp()
            self.schedule.tick()
            self.offers[-1][2](False)
        self.now = datetime(2026, 10, 11, 21, 10).timestamp()
        self.schedule.tick()
        self.assertEqual(len(self.offers), 2)
        self.now = datetime(2026, 10, 18, 21, 10).timestamp()
        self.schedule.tick()
        self.assertEqual(len(self.offers), 3)


class Times(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 15, 0)   # a Thursday afternoon

    def test_the_forms_people_say(self):
        cases = {
            "tomorrow 10am": datetime(2026, 10, 9, 10, 0),
            "today 17:30": datetime(2026, 10, 8, 17, 30),
            "2026-10-09T09:15": datetime(2026, 10, 9, 9, 15),
            "fri 9": datetime(2026, 10, 9, 9, 0),
            "monday at 2 pm": datetime(2026, 10, 12, 14, 0),
            "at 5": datetime(2026, 10, 8, 17, 0),       # said at 3 pm: 5 pm, not 5 am tomorrow
            "tomorrow morning": datetime(2026, 10, 9, 9, 0),
        }
        for said, expected in cases.items():
            self.assertEqual(parse_when(said, self.NOW), expected, said)

    def test_no_time_is_a_question_not_a_guess(self):
        with self.assertRaises(ValueError):
            parse_when("sometime", self.NOW)


class Calendars(unittest.TestCase):
    ICS = "\r\n".join([
        "BEGIN:VCALENDAR",
        "BEGIN:VEVENT", "UID:w1", "DTSTART:20261005T093000", "DTEND:20261005T103000",
        "RRULE:FREQ=WEEKLY;BYDAY=MO,TH", "EXDATE:20261015T093000", "SUMMARY:Lab\\, room 4", "END:VEVENT",
        "BEGIN:VEVENT", "UID:h1", "DTSTART;VALUE=DATE:20261020", "SUMMARY:Dussehra", "END:VEVENT",
        "BEGIN:VEVENT", "UID:c1", "DTSTART:20261009T120000", "STATUS:CANCELLED", "SUMMARY:Gone", "END:VEVENT",
        "END:VCALENDAR"])

    def test_weekly_rule_with_an_exception(self):
        events = ical.parse(self.ICS, "calendar")
        found = ical.occurrences(events, datetime(2026, 10, 6), datetime(2026, 10, 20))
        starts = [e["start"] for e in found if e["title"] == "Lab, room 4"]
        self.assertEqual(starts, [datetime(2026, 10, 8, 9, 30), datetime(2026, 10, 12, 9, 30),
                                  datetime(2026, 10, 19, 9, 30)])   # the 15th is excepted

    def test_all_day_and_cancelled(self):
        events = ical.parse(self.ICS, "holidays")
        found = ical.occurrences(events, datetime(2026, 10, 20), datetime(2026, 10, 21))
        self.assertEqual([(e["title"], e["all_day"]) for e in found], [("Dussehra", True)])
        self.assertNotIn("Gone", [e["title"] for e in events])


if __name__ == "__main__":
    unittest.main()
