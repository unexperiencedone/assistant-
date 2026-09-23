"""Progress that comes from the agent, and tool schemas that fit the budget.

Two corrections in one place, because they were asked for together.

The first version of progress narration inferred the work from tool names, which was
still describing the mechanism one level up: an agent reading around a repo and an agent
running a test suite both call Bash, and only the agent knows which it is doing. So the
agent declares its phases and those are spoken verbatim.

The second: every tool schema costs tokens on every turn, and the whole set was eating
a fifth of the free tier's per-minute budget before a word of the request went out.
"""

from __future__ import annotations

import json
import unittest

from assistant.agents.tools import CORE, ToolContext, relevant, schemas
from assistant.planning import Plan
from assistant.progress import Tracker, milestones_in, strip_milestones


def full_context() -> ToolContext:
    return ToolContext(plan=Plan(), browser=True, publisher=object(), runner=object(),
                       delegate=lambda _t: "", delegate_agy=lambda _t: "",
                       capture=object(), goals=object(), local_system=object(),
                       automations=object())


class DeclaredMilestones(unittest.TestCase):
    def test_a_phase_is_spoken_in_the_agents_own_words(self) -> None:
        tracker = Tracker(label="the client site")
        tracker.declared("Let me look. [[DOING: reading the existing layout]]")
        self.assertEqual(tracker.line(), "Now reading the existing layout.")

    def test_finishing_and_starting_are_both_reported(self) -> None:
        tracker = Tracker(label="x")
        tracker.declared("[[DONE: reading the layout]] [[DOING: ideating the structure]]")
        said = tracker.line()
        self.assertIn("Finished reading the layout", said)
        self.assertIn("Now ideating the structure", said)

    def test_finishing_a_phase_you_announced_is_not_swallowed(self) -> None:
        """It repeats the phrase, and deduping on the phrase alone dropped it -- losing
        the one event the user most wants, that something is done."""
        tracker = Tracker(label="x")
        tracker.declared("[[DOING: ideating the structure]]")
        tracker.line()
        tracker.declared("[[DONE: ideating the structure]]")
        self.assertIn("Finished ideating the structure", tracker.line())

    def test_the_identical_marker_twice_is_said_once(self) -> None:
        tracker = Tracker(label="x")
        tracker.declared("[[DOING: reading]]")
        tracker.declared("[[DOING: reading]]")
        self.assertEqual(tracker.line().count("reading"), 1)

    def test_a_milestone_beats_anything_inferred(self) -> None:
        """Tool calls happened too, and are not what gets said."""
        tracker = Tracker(label="the audit")
        for tool in ("Bash", "Read", "Edit"):
            tracker.saw(tool)
        tracker.declared("[[DOING: running the test suite]]")
        said = tracker.line()
        self.assertIn("running the test suite", said)
        self.assertNotIn("writing code", said)

    def test_markers_are_parsed_loosely(self) -> None:
        for text in ("[[DOING: a thing]]", "[[doing: a thing]]", "[[ DOING : a thing ]]",
                     "[[PHASE: a thing]]"):
            self.assertTrue(milestones_in(text), text)

    def test_nonsense_is_not_a_marker(self) -> None:
        for text in ("[[TASK: play_song]]", "[[STEP 2 DONE]]", "[[PLAN: a title]]", "no markers"):
            self.assertEqual(milestones_in(text), [], text)

    def test_they_never_reach_the_user(self) -> None:
        cleaned = strip_milestones("Done. [[DONE: scaffolding]] The site is up.")
        self.assertNotIn("[[", cleaned)
        self.assertIn("The site is up.", cleaned)

    def test_the_canvas_gets_the_phase_too(self) -> None:
        tracker = Tracker(label="x")
        tracker.declared("[[DOING: writing the copy]]")
        self.assertEqual(tracker.latest, "writing the copy")
        tracker.declared("[[DONE: writing the copy]]")
        self.assertEqual(tracker.latest, "finished writing the copy")


class InferenceIsOnlyTheFallback(unittest.TestCase):
    def test_it_still_says_something_when_no_marker_comes(self) -> None:
        """A short task never reaches a phase boundary, and a model sometimes forgets.
        Something true and vague beats silence while a minute passes."""
        tracker = Tracker(label="the audit")
        for tool in ("Bash", "Read"):
            tracker.saw(tool)
        said = tracker.line()
        self.assertIn("the audit", said)
        self.assertTrue(said.endswith("."))


class SchemaBudget(unittest.TestCase):
    """Every schema is sent on every turn, against a small per-minute allowance."""

    def _tokens(self, selection: list[dict]) -> int:
        return len(json.dumps(selection)) // 4

    def test_an_ordinary_request_sends_far_fewer_tools(self) -> None:
        ctx = full_context()
        everything = self._tokens(schemas(ctx))
        asked = self._tokens(relevant(ctx, "what's the latest on nvidia earnings"))
        self.assertLess(asked, everything // 2, "selection should at least halve the cost")

    def test_the_core_is_always_there(self) -> None:
        """A lookup, a local search, saying what it is doing, and handing the job on."""
        names = {s["function"]["name"] for s in relevant(full_context(), "what time is it")}
        for tool in ("web_search", "nova_status", "delegate_to_claude", "delegate_to_agy"):
            self.assertIn(tool, names)

    def test_a_request_brings_in_what_it_mentions(self) -> None:
        ctx = full_context()
        cases = [("record my screen while i work", "start_recording"),
                 ("draft an outreach email to them", "draft_outreach"),
                 ("clear all the plans that were made", "clear_plan"),
                 ("scrape that directory site", "browse_open"),
                 ("every morning tell me the repo changes", "add_goal")]
        for said, expected in cases:
            names = {s["function"]["name"] for s in relevant(ctx, said)}
            self.assertIn(expected, names, said)

    def test_what_is_not_mentioned_stays_out(self) -> None:
        names = {s["function"]["name"] for s in relevant(full_context(), "what time is it")}
        for tool in ("start_recording", "draft_outreach", "browse_fill", "write_word"):
            self.assertNotIn(tool, names)

    def test_no_request_means_everything_usable(self) -> None:
        """An empty request is not evidence that a tool is unwanted."""
        ctx = full_context()
        self.assertEqual(len(relevant(ctx, "")), len(schemas(ctx)))

    def test_selection_never_invents_a_tool(self) -> None:
        ctx = full_context()
        usable = {s["function"]["name"] for s in schemas(ctx)}
        for said in ("record and draft and browse and plan", "anything at all"):
            picked = {s["function"]["name"] for s in relevant(ctx, said)}
            self.assertTrue(picked <= usable, said)

    def test_core_names_all_exist(self) -> None:
        """A core entry that is not a real tool would silently do nothing."""
        from assistant.agents.tools import TOOLS

        self.assertTrue(CORE <= set(TOOLS), CORE - set(TOOLS))


if __name__ == "__main__":
    unittest.main()
