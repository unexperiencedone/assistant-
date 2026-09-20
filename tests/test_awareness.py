"""Activity awareness: the parts that decide what gets kept and what it means.

None of this touches Windows — the probe is separated from the folding on purpose, so
the rules can be checked here.
"""

import tempfile
import time
import unittest
from pathlib import Path

from assistant.awareness.collapse import app_name, classify, collapse, context_of
from assistant.awareness.collector import Collector
from assistant.awareness.service import parse_range, summarise
from assistant.awareness.store import AwarenessStore


class Settings:
    """Stand-in for AwarenessSettings with the defaults the tests care about."""
    poll_seconds = 2
    dwell_seconds = 12
    idle_seconds = 120
    merge_gap_seconds = 120
    raw_days = 7
    session_days = 19


class TestClassify(unittest.TestCase):
    def test_known_apps_get_a_category(self):
        self.assertEqual(classify("Code.exe"), "editor")
        self.assertEqual(classify("chrome.exe"), "browser")
        self.assertEqual(classify("WindowsTerminal.exe"), "terminal")
        self.assertEqual(classify("something-else.exe"), "other")

    def test_app_name_is_readable(self):
        self.assertEqual(app_name("Code.exe"), "VS Code")
        self.assertEqual(app_name("msedge.exe"), "Edge")

    def test_editor_context_keeps_file_and_project(self):
        self.assertEqual(context_of("editor", "controller.py - Assisstant - Visual Studio Code"),
                         "controller.py · Assisstant")

    def test_browser_context_drops_the_browser_name(self):
        self.assertEqual(context_of("browser", "Some page title - Google Chrome"), "Some page title")

    def test_empty_title_is_no_context(self):
        self.assertEqual(context_of("other", "   "), "")


class TestCollapse(unittest.TestCase):
    def events(self, spec):
        """spec: (offset seconds, app, context, category) tuples."""
        return [{"ts": 1000 + offset, "app": app, "context": context, "category": category}
                for offset, app, context, category in spec]

    def test_a_flicker_is_dropped(self):
        events = self.events([(0, "VS Code", "a.py", "editor"), (2, "Explorer", "", "other"),
                              (4, "VS Code", "a.py", "editor")])
        sessions = collapse(events, dwell_seconds=12)
        self.assertEqual([s["app"] for s in sessions], [])

    def test_a_real_stretch_survives(self):
        events = self.events([(step, "VS Code", "a.py · proj", "editor") for step in range(0, 60, 2)])
        sessions = collapse(events, dwell_seconds=12)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["context"], "a.py · proj")
        self.assertGreaterEqual(sessions[0]["seconds"], 58)

    def test_coming_back_soon_is_one_session(self):
        first = [(step, "VS Code", "a.py", "editor") for step in range(0, 40, 2)]
        away = [(step, "Chrome", "docs", "browser") for step in range(40, 80, 2)]
        back = [(step, "VS Code", "a.py", "editor") for step in range(80, 120, 2)]
        sessions = collapse(self.events(first + away + back), dwell_seconds=12, merge_gap_seconds=120)
        editor = [s for s in sessions if s["app"] == "VS Code"]
        self.assertEqual(len(editor), 1, "the same file within the merge gap is one stretch")

    def test_idle_is_kept_however_short(self):
        events = self.events([(0, "idle", "no input", "idle"), (2, "idle", "no input", "idle")])
        self.assertEqual(len(collapse(events, dwell_seconds=12)), 1)


class FakeStore:
    def __init__(self):
        self.events = []
        self.sessions = {}
        self.next_id = 1

    def add_event(self, event):
        self.events.append(event)

    def upsert_session(self, session_id, session):
        if session_id is None:
            session_id = self.next_id
            self.next_id += 1
        self.sessions[session_id] = dict(session)
        return session_id

    def merge_candidate(self, app, context, since):
        for key, session in sorted(self.sessions.items(), reverse=True):
            if session["app"] == app and session["context"] == context and session["end"] >= since:
                return {"id": key, "started_at": session["start"]}
        return None

    def prune(self, raw_days, session_days):
        pass


class TestCollector(unittest.TestCase):
    def tick_run(self, collector, app, category, context, start, seconds, step=2):
        for offset in range(0, seconds + 1, step):
            collector.tick({"ts": start + offset, "app": app, "category": category,
                            "context": context, "title": ""})

    def test_short_visits_never_reach_the_store(self):
        store = FakeStore()
        collector = Collector(store, Settings())
        self.tick_run(collector, "Explorer", "other", "Downloads", 1000, 4)
        self.assertEqual(store.sessions, {}, "four seconds in a window is not a session")
        self.assertEqual(len(store.events), 1, "but the raw focus event is still recorded")

    def test_a_long_visit_becomes_one_growing_session(self):
        store = FakeStore()
        collector = Collector(store, Settings())
        self.tick_run(collector, "VS Code", "editor", "a.py · proj", 1000, 40)
        self.assertEqual(len(store.sessions), 1)
        session = next(iter(store.sessions.values()))
        self.assertEqual(session["start"], 1000)
        self.assertEqual(session["end"], 1040)

    def test_returning_extends_the_earlier_session(self):
        store = FakeStore()
        collector = Collector(store, Settings())
        self.tick_run(collector, "VS Code", "editor", "a.py", 1000, 40)
        self.tick_run(collector, "Chrome", "browser", "docs", 1050, 40)
        self.tick_run(collector, "VS Code", "editor", "a.py", 1100, 40)
        editor = [s for s in store.sessions.values() if s["app"] == "VS Code"]
        self.assertEqual(len(editor), 1)
        self.assertEqual(editor[0]["start"], 1000)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = AwarenessStore(Path(self.dir.name) / "awareness.db")

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_sessions_come_back_in_order(self):
        now = time.time()
        for offset, app in ((-600, "VS Code"), (-300, "Chrome")):
            self.store.upsert_session(None, {"start": now + offset, "end": now + offset + 120,
                                             "app": app, "category": "editor", "context": "x"})
        rows = self.store.sessions_between(now - 3600, now)
        self.assertEqual([row["app"] for row in rows], ["VS Code", "Chrome"])

    def test_pruning_forgets_old_rows(self):
        old = time.time() - 40 * 86400
        self.store.upsert_session(None, {"start": old, "end": old + 60, "app": "VS Code",
                                         "category": "editor", "context": "x"})
        self.store.prune(raw_days=7, session_days=19)
        self.assertEqual(self.store.sessions_between(old - 60, time.time()), [])

    def test_forget_all_leaves_nothing(self):
        now = time.time()
        self.store.add_event({"ts": now, "app": "VS Code", "category": "editor", "context": "x", "title": "t"})
        self.store.upsert_session(None, {"start": now, "end": now + 30, "app": "VS Code",
                                         "category": "editor", "context": "x"})
        self.store.forget_all()
        self.assertEqual(self.store.sessions_between(now - 60, now + 60), [])


class TestQuestions(unittest.TestCase):
    def test_named_stretches(self):
        now = time.mktime((2026, 9, 19, 15, 0, 0, 0, 0, -1))
        _, _, label = parse_range("what did I do last night?", now)
        self.assertEqual(label, "last night")
        _, _, label = parse_range("what have I done today", now)
        self.assertEqual(label, "today")
        _, _, label = parse_range("summarise my work", now)
        self.assertEqual(label, "the last twelve hours")

    def test_an_explicit_range_is_read(self):
        now = time.mktime((2026, 9, 19, 15, 0, 0, 0, 0, -1))
        # "last night" makes a bare 9 mean 21:00, and midnight is the end of that day.
        start, end, _ = parse_range("what did I do between 9 and midnight last night", now)
        self.assertEqual(time.localtime(start).tm_hour, 21)
        self.assertEqual(time.localtime(start).tm_mday, 18)
        self.assertEqual(end - start, 3 * 3600)

    def test_a_morning_range_stays_in_the_morning(self):
        now = time.mktime((2026, 9, 19, 15, 0, 0, 0, 0, -1))
        start, end, _ = parse_range("what did I do between 9 and 11 today", now)
        self.assertEqual(time.localtime(start).tm_hour, 9)
        self.assertEqual(time.localtime(end).tm_hour, 11)

    def test_two_flickers_in_the_same_app_are_not_a_session(self):
        events = [{"ts": 1000, "app": "Explorer", "context": "Downloads", "category": "other"},
                  {"ts": 1060, "app": "Explorer", "context": "Downloads", "category": "other"}]
        self.assertEqual(collapse(events, dwell_seconds=12), [])

    def test_the_plain_summary_only_states_what_is_there(self):
        now = time.time()
        sessions = [
            {"start": now - 3600, "end": now - 1800, "app": "VS Code", "category": "editor",
             "context": "controller.py · Assisstant", "seconds": 1800},
            {"start": now - 1800, "end": now - 900, "app": "Chrome", "category": "browser",
             "context": "docs", "seconds": 900},
        ]
        text = summarise(sessions, "today")
        self.assertIn("VS Code", text)
        self.assertIn("controller.py", text)
        self.assertIn("2 stretches", text)

    def test_nothing_recorded_says_so(self):
        self.assertIn("Nothing was recorded", summarise([], "today"))


if __name__ == "__main__":
    unittest.main()
