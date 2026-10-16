import tempfile
import unittest
from pathlib import Path

from assistant.automation.macros import MacroRunner, load_macros
from assistant.events import EventBus

FAILS = '''name = "First"
phrases = ["play {query}"]
fallback = "second"

[[steps]]
do = "media"
key = "not_a_key"
'''
WORKS = '''name = "Second"
phrases = ["play {query} elsewhere"]

[[steps]]
do = "say"
text = "Playing {query} elsewhere."
'''


class MacroFallbackTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.said = []
        self.bus = EventBus()
        self.done = []
        self.bus.subscribe("macro_done", lambda e: self.done.append(e.data))
        self.runner = MacroRunner(self.bus, open_app=lambda _: True, say=self.said.append)

    def tearDown(self):
        self.runner.close()

    def write(self, name, text):
        (self.folder / f"{name}.toml").write_text(text, encoding="utf-8")

    def first(self):
        return next(m for m in load_macros(self.folder) if m.name == "First")

    def test_a_failed_step_goes_to_the_fallback_with_the_same_values(self):
        self.write("first", FAILS)
        self.write("second", WORKS)
        ok, summary = self.runner.run(self.first(), {"query": "tum hi ho"})
        self.assertTrue(ok)
        self.assertEqual(self.said, ["Playing tum hi ho elsewhere."])   # no "failed at step" line
        self.assertEqual([d["ok"] for d in self.done], [True])

    def test_a_failing_fallback_reports_one_failure_and_stops(self):
        self.write("first", FAILS)
        self.write("second", FAILS.replace('name = "First"', 'name = "Second"').replace('fallback = "second"', 'fallback = "first"'))
        ok, _ = self.runner.run(self.first(), {"query": "x"})
        self.assertFalse(ok)
        self.assertEqual([d["ok"] for d in self.done], [False])

    def test_a_missing_fallback_is_an_ordinary_failure(self):
        self.write("first", FAILS)
        ok, _ = self.runner.run(self.first(), {"query": "x"})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
