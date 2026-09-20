"""The dashboard state sends only what changed, and its counters mean what they say."""

import unittest

from assistant.events import EventBus
from assistant.state import AppState


class Changes(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.state = AppState(self.bus, "Nova", "Claude Code", "C:\\work")
        _, self.seen = self.state.snapshot_with_versions()

    def changes(self):
        replaced, appended, self.seen = self.state.changes_since(self.seen)
        return replaced, appended

    def test_a_mic_update_sends_only_the_mic(self):
        self.bus.publish("graph", run=1, nodes=[{"id": "a"}], edges=[])
        self.bus.publish("transcript", role="user", text="hey")
        self.changes()
        self.bus.publish("mic", level=0.4, speaking=True)
        self.assertEqual(({"mic": {"level": 0.4, "speaking": True}}, {}), self.changes())

    def test_lists_send_only_new_rows(self):
        for i in range(3):
            self.bus.publish("transcript", role="user", text=f"m{i}")
        self.changes()
        self.bus.publish("transcript", role="assistant", text="reply")
        replaced, appended = self.changes()
        self.assertEqual({}, replaced)
        self.assertEqual(["reply"], [m["text"] for m in appended["conversation"]])

    def test_unchanged_values_send_nothing(self):
        self.bus.publish("mic", level=0.0, speaking=False)
        self.assertEqual(({}, {}), self.changes())

    def test_listeners_hear_about_changes_only(self):
        calls = []
        self.state.add_listener(lambda: calls.append(1))
        self.bus.publish("status", state="idle")      # already idle
        self.bus.publish("status", state="listening")
        self.assertEqual(1, len(calls))


class Agents(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.state = AppState(self.bus, "Nova", "Claude Code", "C:\\work")

    def test_working_until_the_last_parallel_task_ends(self):
        self.bus.publish("agent", kind="start", backend="claude", task="t1")
        self.bus.publish("agent", kind="start", backend="claude", task="t2")
        self.bus.publish("agent", kind="result", backend="claude", task="t1", data={})
        self.assertTrue(self.state.snapshot()["agent_running"])
        self.bus.publish("agent", kind="result", backend="claude", task="t2", data={})
        self.assertFalse(self.state.snapshot()["agent_running"])

    def test_a_denial_clears_when_the_next_request_starts(self):
        self.bus.publish("agent", kind="start", backend="claude", task="t1")
        self.bus.publish("agent", kind="result", backend="claude", task="t1", data={"denied": ["Bash"]})
        self.assertEqual(["Bash"], [d["tool"] for d in self.state.snapshot()["denials"]])
        self.bus.publish("agent", kind="start", backend="claude", task="t2")
        self.assertEqual([], self.state.snapshot()["denials"])

    def test_a_denial_can_be_dismissed(self):
        self.bus.publish("agent", kind="error", backend="claude", task="t1", data={"denied": ["Bash"]})
        self.state.dismiss_denials()
        self.assertEqual([], self.state.snapshot()["denials"])

    def test_session_cost_and_total_are_separate(self):
        self.state.restore({"cost_usd": 46.5})  # an old state file: that number was the running total
        self.bus.publish("agent", kind="result", backend="claude", task="t1", data={"cost_usd": 0.25})
        snap = self.state.snapshot()
        self.assertEqual(0.25, snap["cost_usd"])
        self.assertEqual(46.75, snap["cost_total_usd"])
        self.assertEqual(46.75, self.state.persisted()["cost_total_usd"])

    def test_the_queue_survives_a_restart(self):
        self.bus.publish("queue", items=["tidy downloads"])
        saved = self.state.persisted()
        fresh = AppState(EventBus(), "Nova", "Claude Code", "C:\\work")
        self.assertEqual(["tidy downloads"], fresh.restore(saved))


if __name__ == "__main__":
    unittest.main()
