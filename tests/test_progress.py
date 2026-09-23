"""Saying what an agent is doing, not which tool it just called.

The complaint this comes from: while a task ran, Nova said "Running a command." over
and over. True, and useless -- a command is the one thing an agent does constantly
whatever it is really up to, so naming it tells you the mechanism and never the work.
"""

from __future__ import annotations

import unittest

from assistant.progress import DEFAULT_KIND, Tracker


class WhatItSays(unittest.TestCase):
    def test_the_first_line_names_the_job(self) -> None:
        """At the start, what the job is is the useful part."""
        tracker = Tracker(label="the client site")
        for tool in ("Glob", "Read", "Grep"):
            tracker.saw(tool)
        said = tracker.line()
        self.assertIn("Working on the client site", said)
        self.assertIn("reading", said)

    def test_later_lines_say_still(self) -> None:
        """By then you know the job and want to know whether it has moved on."""
        tracker = Tracker(label="the client site")
        tracker.saw("Read")
        tracker.line()
        tracker.saw("Edit")
        self.assertTrue(tracker.line().startswith("Still on the client site"))

    def test_the_kind_of_work_changes_as_it_does(self) -> None:
        tracker = Tracker(label="the audit")
        tracker.saw("Read")
        self.assertIn("reading", tracker.line())
        tracker.saw("Edit")
        tracker.saw("Write")
        self.assertIn("writing code", tracker.line())
        tracker.saw("WebSearch")
        self.assertIn("searching the web", tracker.line())

    def test_repeats_of_one_kind_are_one_thing_happening(self) -> None:
        """Five Bash calls are "running things", not "running things and running
        things and running things"."""
        tracker = Tracker(label="x")
        for _ in range(5):
            tracker.saw("Bash")
        said = tracker.line()
        self.assertEqual(said.count("running things"), 1)

    def test_at_most_two_kinds_are_spoken(self) -> None:
        tracker = Tracker(label="x")
        for tool in ("Read", "Edit", "Bash", "WebSearch", "Glob"):
            tracker.saw(tool)
        self.assertLessEqual(tracker.line().count(" and "), 1)

    def test_a_window_resets_after_speaking(self) -> None:
        """Each line covers what happened since the last one, not the whole run."""
        tracker = Tracker(label="x")
        tracker.saw("WebSearch")
        self.assertIn("searching the web", tracker.line())
        tracker.saw("Edit")
        self.assertNotIn("searching the web", tracker.line())


class Edges(unittest.TestCase):
    def test_nothing_recognised_still_says_something_honest(self) -> None:
        """Between tool calls a model is generating, so "thinking it through" is true
        rather than a filler."""
        tracker = Tracker(label="the audit")
        self.assertIn(DEFAULT_KIND, tracker.line())

    def test_an_unknown_tool_gets_a_readable_phrase_for_the_canvas(self) -> None:
        tracker = Tracker()
        self.assertEqual(tracker.saw("some_new_tool"), "using some new tool")

    def test_no_label_still_produces_a_sentence(self) -> None:
        tracker = Tracker(label="")
        tracker.saw("Edit")
        said = tracker.line()
        self.assertTrue(said.endswith("."))
        self.assertTrue(said[0].isupper())

    def test_a_long_label_is_cut(self) -> None:
        tracker = Tracker(label="x" * 200)
        tracker.saw("Read")
        self.assertLess(len(tracker.line()), 120)

    def test_the_canvas_phrase_and_the_spoken_line_agree(self) -> None:
        tracker = Tracker(label="x")
        self.assertEqual(tracker.saw("Bash"), "running things")
        self.assertEqual(tracker.latest, "running things")


class ToolsFromEveryAgent(unittest.TestCase):
    """Claude Code, Antigravity and Nova's own tools all report through one event."""

    def test_claude_code_names(self) -> None:
        for tool, expected in (("Edit", "writing code"), ("Bash", "running things"),
                               ("Grep", "searching the code"), ("WebFetch", "reading a page")):
            self.assertEqual(Tracker().saw(tool), expected, tool)

    def test_antigravity_names(self) -> None:
        for tool, expected in (("write_to_file", "writing code"), ("view_file", "reading"),
                               ("run_command", "running things")):
            self.assertEqual(Tracker().saw(tool), expected, tool)

    def test_novas_own_tools(self) -> None:
        for tool, expected in (("web_search", "searching the web"), ("browse_open", "in the browser"),
                               ("run_automation", "running an automation"),
                               ("delegate_to_agy", "handing work to Antigravity"),
                               ("delegate_to_claude", "handing work to Claude")):
            self.assertEqual(Tracker().saw(tool), expected, tool)


if __name__ == "__main__":
    unittest.main()
