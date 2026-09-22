"""Nova's journal: what a day's entry is built from, and what it refuses to write."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from assistant.events import EventBus
from assistant.history import HistoryRecorder, HistoryStore
from assistant.journal import JournalService
from assistant.journal import writer

DAY = 86400


class Settings:
    enabled = True
    check_minutes = 30
    backfill_days = 3
    context_days = 3
    keep_days = 730
    narrate = False


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.bus = EventBus()
        self.history = HistoryStore(self.folder / "history.db")
        self.now = time.time()
        self.yesterday = writer.day_key(self.now - DAY)
        self.settings = Settings()

    def tearDown(self):
        self.history.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def service(self, **kwargs):
        return JournalService(self.folder / "journal.db", self.bus, self.settings,
                              history=self.history, clock=lambda: self.now, **kwargs)

    def record_yesterday(self, title="fix the login page", failures=0):
        """A session that ran during yesterday, with one request in it."""
        ts = writer.midnight(self.now - DAY) + 10 * 3600
        session = self.history.open_session(ts)
        self.history.add_event(session, ts, "request", title)
        self.history.touch(session, ts + 600, title=title, requests=1, results=1,
                           failures=failures, projects=["Drishtikon"])
        self.history.close_session(session)
        return session


class Entries(Base):
    def test_a_day_with_work_gets_one_entry_naming_it(self):
        self.record_yesterday()
        journal = self.service()
        entry = journal.write(self.yesterday)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["source"], "plain")
        self.assertIn("Drishtikon", entry["text"])
        self.assertIn("fix the login page", entry["text"])
        self.assertEqual(entry["facts"]["requests"], 1)

    def test_an_empty_day_gets_no_entry_at_all(self):
        journal = self.service()
        self.assertIsNone(journal.write(self.yesterday))
        self.assertEqual(journal.store.count(), 0)

    def test_a_day_is_written_once_however_often_it_is_caught_up(self):
        self.record_yesterday()
        journal = self.service()
        self.assertEqual(journal.catch_up(), [self.yesterday])
        self.assertEqual(journal.catch_up(), [])
        self.assertEqual(journal.store.count(), 1)

    def test_today_is_never_written_because_it_is_not_over(self):
        ts = self.now - 60
        session = self.history.open_session(ts)
        self.history.touch(session, ts, title="something now", requests=1)
        journal = self.service()
        journal.catch_up()
        self.assertFalse(journal.store.has(writer.day_key(self.now)))

    def test_an_unfinished_request_is_admitted_in_the_entry(self):
        self.record_yesterday(failures=1)
        journal = self.service()
        self.assertIn("did not finish", journal.write(self.yesterday)["text"])


class Narration(Base):
    def test_a_models_phrasing_replaces_the_plain_entry(self):
        self.record_yesterday()
        self.settings.narrate = True
        journal = self.service(narrate=lambda prompt: "I spent the morning on Drishtikon's login page.")
        self.assertEqual(journal.write(self.yesterday)["source"], "narrated")

    def test_a_model_that_rambles_or_fails_leaves_the_facts_standing(self):
        self.record_yesterday()
        self.settings.narrate = True
        for narrate in (lambda prompt: "x" * 5000, lambda prompt: "", self._raise):
            with self.subTest(narrate=narrate):
                journal = self.service(narrate=narrate)
                entry = journal.write(self.yesterday)
                self.assertEqual(entry["source"], "plain")
                self.assertIn("Drishtikon", entry["text"])

    @staticmethod
    def _raise(prompt):
        raise RuntimeError("no backend")


class Continuity(Base):
    def test_context_carries_the_entries_and_says_what_they_are_for(self):
        self.record_yesterday()
        journal = self.service()
        journal.catch_up()
        context = journal.context()
        self.assertIn("fix the login page", context)
        self.assertIn("continuity", context)

    def test_there_is_no_context_before_anything_has_happened(self):
        self.assertEqual(self.service().context(), "")


if __name__ == "__main__":
    unittest.main()
