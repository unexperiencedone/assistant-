"""Work history: timed sessions, what they record, search, recap."""

import shutil
import tempfile
import unittest
from pathlib import Path

from assistant.events import EventBus
from assistant.history import HistoryRecorder, HistoryStore, recap
from assistant.history.store import files_changed

IDLE = 30 * 60


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class Recording(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.store = HistoryStore(self.folder / "history.db")
        self.bus = EventBus()
        self.clock = Clock()
        self.announced = []
        self.bus.subscribe("history", lambda e: self.announced.append(e.data))
        self.recorder = self.make_recorder()

    def make_recorder(self):
        return HistoryRecorder(self.store, self.bus, IDLE, clock=self.clock, threaded=False,
                               projects_in=lambda text: ["Drishtikon"] if "drishtikon" in text.lower() else [])

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def publish(self, topic, at, **data):
        """Publish with a controlled timestamp (events carry their own ts)."""
        from assistant.events import Event
        self.clock.now = at
        for handler in [*self.bus._handlers[topic], *self.bus._handlers["*"]]:
            handler(Event(topic, data, at))

    def request(self, at, text, task="t1", ok=True, cost=0.1, tools=("Edit",)):
        t = self.clock.now if at is None else at
        self.publish("transcript", t, role="user", text=text)
        self.publish("route", t, utterance=text, route="agent", name="task", backend="Claude Code", task=task)
        self.publish("agent", t + 1, kind="start", backend="Claude Code", text=text, task=task)
        for i, tool in enumerate(tools):
            self.publish("agent", t + 2 + i, kind="tool", backend="Claude Code", tool=tool,
                         text=f"C:\\proj\\file{i}.py", task=task)
        self.publish("agent", t + 10, kind="result" if ok else "error", backend="Claude Code",
                     text="Done.", data={"cost_usd": cost}, task=task)
        self.publish("transcript", t + 11, role="assistant", text="Done.")

    def sessions(self):
        return self.store.sessions()[0]

    def test_nothing_is_recorded_before_a_request(self):
        self.publish("transcript", 100, role="assistant", text="Hi Aakshant, I'm Nova.")
        self.publish("log", 101, level="warn", text="something")
        self.assertEqual([], self.sessions())

    def test_one_stretch_of_work_is_one_session(self):
        t = self.clock.now
        self.request(t, "update the drishtikon readme")
        self.request(t + 600, "run the tests", task="t2", ok=False, tools=("Bash",))
        [s] = self.sessions()
        self.assertTrue(s["live"])
        self.assertEqual("update the drishtikon readme", s["title"])
        self.assertEqual((2, 1, 1), (s["requests"], s["results"], s["failures"]))
        self.assertAlmostEqual(0.2, s["cost_usd"])
        self.assertEqual(["Drishtikon"], s["projects"])
        self.assertEqual(["Claude Code"], s["backends"])
        detail = self.store.session(s["id"])
        self.assertEqual(["you", "request", "start", "tool", "result", "nova"], [e["kind"] for e in detail["events"][:6]])
        self.assertEqual(["C:\\proj\\file0.py"], detail["files"])  # Edit counts, Bash doesn't

    def test_idle_time_closes_the_session_and_the_next_request_opens_another(self):
        t = self.clock.now
        self.request(t, "first")
        self.clock.now = t + 11 + IDLE + 1
        self.recorder.tick()
        self.assertFalse(self.sessions()[0]["live"])
        self.request(t + IDLE + 100, "second")
        newest, oldest = self.sessions()
        self.assertEqual(("second", "first"), (newest["title"], oldest["title"]))
        self.assertEqual(oldest["last_activity"], oldest["ended_at"])  # ends at its last activity, not at the tick

    def test_a_running_task_keeps_the_session_open(self):
        t = self.clock.now
        self.publish("route", t, utterance="big refactor", route="agent", name="task", backend="Claude Code", task="t1")
        self.publish("agent", t, kind="start", backend="Claude Code", text="big refactor", task="t1")
        self.clock.now = t + 3 * IDLE
        self.recorder.tick()
        self.assertTrue(self.sessions()[0]["live"])

    def test_a_restart_inside_the_idle_window_continues_the_session(self):
        t = self.clock.now
        self.request(t, "first")
        self.recorder.stop()  # Nova quits
        self.assertFalse(self.sessions()[0]["live"])
        self.bus = EventBus()  # a new process: the old recorder is gone
        self.recorder = self.make_recorder()
        self.request(t + 300, "after restart")
        [s] = self.sessions()
        self.assertTrue(s["live"])
        self.assertEqual(2, s["requests"])

    def test_every_request_counts_once_whatever_handles_it(self):
        t = self.clock.now
        self.publish("transcript", t, role="user", text="what time is it")  # an instant command: no route
        self.publish("transcript", t + 5, role="user", text="open the report")
        self.publish("route", t + 5, utterance="open the report", route="local", name="open_item")
        self.publish("route", t + 5.2, utterance="open the report", route="agent", name="task", task="t1")  # fell through
        self.assertEqual(2, self.sessions()[0]["requests"])
        self.assertEqual("what time is it", self.announced[-1]["current"]["title"])

    def test_runs_and_plan_drafts_are_kept(self):
        t = self.clock.now
        self.request(t, "plan it")
        plan = {"title": "Site", "steps": [{"text": "a", "status": "pending"}]}
        self.publish("plan", t + 12, **plan)
        self.publish("plan", t + 13, **{**plan, "steps": [{"text": "a", "status": "done"}]})  # status only
        self.publish("run_finished", t + 14, lane="t1",
                     graph={"label": "plan it", "nodes": [{"kind": "result", "status": "failed"}], "edges": []})
        detail = self.store.session(self.sessions()[0]["id"])
        self.assertEqual(1, sum(e["kind"] == "plan" for e in detail["events"]))
        [run] = detail["runs"]
        self.assertEqual(("plan it", "failed"), (run["label"], run["status"]))
        self.assertEqual("plan it", self.store.run(run["id"])["label"])

    def test_search_finds_sessions_by_anything_said_or_done(self):
        t = self.clock.now
        self.request(t, "fix the login page")
        self.request(t + IDLE * 3, "write the drishtikon readme")
        self.assertEqual(["fix the login page"], [s["title"] for s in self.store.sessions(query="login")[0]])
        self.assertEqual(2, len(self.store.sessions(query="file0")[0]))  # a tool's path matches too

    def test_deleting_the_open_session_starts_a_fresh_one(self):
        t = self.clock.now
        self.request(t, "first")
        self.assertTrue(self.store.delete(self.sessions()[0]["id"]))
        self.request(t + 60, "second")
        self.assertEqual(["second"], [s["title"] for s in self.sessions()])

    def test_changes_are_announced_for_the_canvas(self):
        self.request(self.clock.now, "first")
        self.assertTrue(self.announced)
        self.assertEqual("first", self.announced[-1]["current"]["title"])

    def test_recap(self):
        t = self.clock.now
        self.request(t, "update the drishtikon readme")
        self.request(t + 1500, "run the tests", task="t2", ok=False)
        text = recap(self.store, "", now=t + 1600)
        self.assertIn("This session", text)
        self.assertIn("2 requests, 1 of them failed, on Drishtikon", text)
        self.assertIn("The latest was: run the tests.", text)
        self.assertIn("today you had 1 session", recap(self.store, "today", now=t + 1600).lower())
        self.assertEqual("There's no earlier session yet.", recap(self.store, "last session", now=t + 1600))


class Files(unittest.TestCase):
    def test_only_written_paths_count(self):
        events = [{"kind": "tool", "tool": "Write", "text": "C:\\a\\b.md"},
                  {"kind": "tool", "tool": "Read", "text": "C:\\a\\c.md"},
                  {"kind": "tool", "tool": "Edit", "text": "npm run build"},
                  {"kind": "tool", "tool": "replace_file_content", "text": "src/app.js"},
                  {"kind": "tool", "tool": "Write", "text": "C:\\a\\b.md"}]
        self.assertEqual(["C:\\a\\b.md", "src/app.js"], files_changed(events))


if __name__ == "__main__":
    unittest.main()
