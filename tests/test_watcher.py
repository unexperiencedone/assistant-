"""The live view: what is running, what ran last, and that every session hears it."""

import unittest

from assistant.events import EventBus
from assistant.watcher import Watcher


def _tool(bus, task, tool, text, backend="Groq"):
    bus.publish("agent", kind="tool", task=task, tool=tool, text=text, backend=backend)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.watcher = Watcher(self.bus)
        self.seen = []
        self.bus.subscribe("live", lambda event: self.seen.append(event.data))

    def test_idle_says_so_without_pretending(self):
        self.assertIn("Nothing is running", self.watcher.spoken())
        self.assertIn("haven't run anything", self.watcher.spoken())

    def test_running_names_the_command_and_its_target(self):
        self.bus.publish("agent", kind="start", task="t1", text="find lucknow prospects",
                         backend="Groq")
        _tool(self.bus, "t1", "web_search", "lucknow web design agencies")
        said = self.watcher.spoken()
        # The target is the half the old status line threw away: "web_search" alone said
        # nothing about the work.
        self.assertIn("web search on lucknow web design agencies", said)
        self.assertIn("find lucknow prospects", said)

    def test_a_declared_phase_beats_the_tool_name(self):
        self.bus.publish("agent", kind="start", task="t1", text="research", backend="Groq")
        _tool(self.bus, "t1", "read_page", "https://example.com")
        self.bus.publish("phase", task="t1", phase="reading the third directory page")
        self.assertIn("reading the third directory page", self.watcher.spoken())
        self.assertNotIn("read page on", self.watcher.spoken())

    def test_a_phase_for_a_job_that_ended_is_dropped(self):
        self.bus.publish("phase", task="gone", phase="somewhere")
        self.assertEqual(self.watcher.running(), [])

    def test_the_previous_call_is_kept_when_the_next_one_starts(self):
        self.bus.publish("agent", kind="start", task="t1", text="research", backend="Groq")
        _tool(self.bus, "t1", "web_search", "one")
        _tool(self.bus, "t1", "read_page", "two")
        finished = self.watcher.last(4)
        self.assertEqual([c.tool for c in finished], ["web_search"])
        self.assertIs(finished[0].ok, True)

    def test_after_a_job_ends_the_last_command_is_still_answerable(self):
        self.bus.publish("agent", kind="start", task="t1", text="research", backend="Groq")
        _tool(self.bus, "t1", "read_page", "https://justdial.com/Lucknow")
        self.bus.publish("agent", kind="result", task="t1", backend="Groq")
        self.assertEqual(self.watcher.running(), [])
        said = self.watcher.spoken()
        self.assertIn("Nothing is running", said)
        self.assertIn("read page on https://justdial.com/Lucknow", said)
        self.assertIn("finished", said)

    def test_a_failed_turn_is_not_reported_as_finished(self):
        self.bus.publish("agent", kind="start", task="t1", text="research", backend="Groq")
        _tool(self.bus, "t1", "read_page", "https://example.com")
        self.bus.publish("agent", kind="error", task="t1", backend="Groq")
        self.assertIn("failed at", self.watcher.spoken())

    def test_an_automation_counts_as_activity(self):
        self.bus.publish("macro_step", status="running", index=2)
        self.assertIn("automation step 2", self.watcher.spoken())
        self.bus.publish("macro_done", name="play_song", ok=True)
        said = self.watcher.spoken()
        self.assertNotIn("automation step", said)
        self.assertIn("automation on play_song", said)

    def test_every_change_is_broadcast_but_streamed_text_is_not(self):
        self.bus.publish("agent", kind="start", task="t1", text="research", backend="Groq")
        _tool(self.bus, "t1", "web_search", "one")
        pushed = len(self.seen)
        self.assertEqual(pushed, 2)
        # A slice per token would be a push per chunk of the reply.
        self.bus.publish("agent", kind="text", task="t1", text="thinking out loud",
                         backend="Groq")
        self.assertEqual(len(self.seen), pushed)

    def test_the_slice_carries_what_a_late_session_needs(self):
        self.bus.publish("agent", kind="start", task="t1", text="research", backend="Groq")
        _tool(self.bus, "t1", "web_search", "one")
        slice_ = self.seen[-1]
        self.assertEqual(slice_["running"][0]["command"]["tool"], "web_search")
        self.assertEqual(slice_["running"][0]["backend"], "Groq")
        self.assertEqual(slice_["macro"], "")

    def test_a_subscriber_that_asks_back_does_not_deadlock(self):
        # The announcement goes out with the lock released, so a canvas handler is free
        # to turn round and ask the watcher what it is looking at.
        answers = []
        self.bus.subscribe("live", lambda event: answers.append(self.watcher.spoken()))
        self.bus.publish("agent", kind="start", task="t1", text="research", backend="Groq")
        self.assertTrue(answers and "research" in answers[-1])

    def test_the_idle_phrasing_has_one_home(self):
        # The controller's idle answer and spoken() both read this, so they cannot drift.
        self.assertEqual(self.watcher.last_sentence(), "")
        self.bus.publish("macro_done", name="play_song", ok=True)
        said = self.watcher.last_sentence()
        self.assertIn("automation on play_song", said)
        self.assertIn(said, self.watcher.spoken())


if __name__ == "__main__":
    unittest.main()
