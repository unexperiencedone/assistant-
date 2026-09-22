"""Several pieces of work for one request, answered once.

The three behaviours worth protecting are the three that make this better than not
having it: steps with no dependencies really do run at the same time, a group says
nothing until it is finished, and a step that fails is named rather than swallowed.
"""

from __future__ import annotations

import unittest

from assistant.orchestrate import BLOCKED, DONE, FAILED, RUNNING, Orchestrator


class Recorder:
    """Stands in for the task pool: hands back ids and remembers what it was given."""

    def __init__(self, capacity: int = 99) -> None:
        self.started: list[str] = []
        self.capacity = capacity

    def __call__(self, prompt: str, _label: str) -> str | None:
        if len(self.started) >= self.capacity:
            return None
        self.started.append(prompt)
        return f"t{len(self.started)}"


class Parallel(unittest.TestCase):
    def test_independent_steps_start_together(self) -> None:
        dispatch = Recorder()
        group = Orchestrator(dispatch).start("do both", [("first", []), ("second", [])])
        self.assertEqual(dispatch.started, ["first", "second"])
        self.assertTrue(all(s.status == RUNNING for s in group.steps))

    def test_a_dependent_step_waits(self) -> None:
        dispatch = Recorder()
        orch = Orchestrator(dispatch)
        orch.start("chain", [("first", []), ("second", [1])])
        self.assertEqual(dispatch.started, ["first"], "the second step must not start yet")
        orch.finished("t1", True, "first done")
        self.assertEqual(dispatch.started, ["first", "second"])

    def test_a_step_waits_for_all_of_its_blockers(self) -> None:
        dispatch = Recorder()
        orch = Orchestrator(dispatch)
        orch.start("fan in", [("a", []), ("b", []), ("write up", [1, 2])])
        orch.finished("t1", True, "a done")
        self.assertEqual(len(dispatch.started), 2, "still waiting on b")
        orch.finished("t2", True, "b done")
        self.assertEqual(dispatch.started[-1], "write up")

    def test_sequential_is_just_a_chain_of_dependencies(self) -> None:
        """The whole reason this is one mechanism and not two."""
        dispatch = Recorder()
        orch = Orchestrator(dispatch)
        orch.start("one at a time", [("a", []), ("b", [1]), ("c", [2])])
        for i, expected in enumerate(["a", "b", "c"], 1):
            self.assertEqual(dispatch.started[-1], expected)
            orch.finished(f"t{i}", True, f"{expected} done")


class OneVoice(unittest.TestCase):
    """Three tasks each narrating themselves is three voices over each other."""

    def test_silent_until_the_group_is_done(self) -> None:
        orch = Orchestrator(Recorder())
        orch.start("two things", [("first", []), ("second", [])])
        self.assertEqual(orch.finished("t1", True, "first answer"), "")
        combined = orch.finished("t2", True, "second answer")
        self.assertIn("first answer", combined)
        self.assertIn("second answer", combined)

    def test_the_group_is_forgotten_once_answered(self) -> None:
        orch = Orchestrator(Recorder())
        orch.start("one thing", [("only", [])])
        orch.finished("t1", True, "done")
        self.assertEqual(orch.state()["groups"], [])

    def test_an_unknown_task_is_not_claimed(self) -> None:
        orch = Orchestrator(Recorder())
        self.assertFalse(orch.owns("t99"))
        self.assertEqual(orch.finished("t99", True, "stray"), "")


class Failure(unittest.TestCase):
    def test_a_failed_step_is_named(self) -> None:
        orch = Orchestrator(Recorder())
        orch.start("two things", [("first", []), ("second", [])])
        orch.finished("t1", True, "first worked")
        combined = orch.finished("t2", False, "")
        self.assertIn("first worked", combined)
        self.assertIn("couldn't finish", combined)
        self.assertIn("second", combined)

    def test_what_depended_on_it_is_reported_not_dropped(self) -> None:
        """A step that never ran because something else broke is the thing the user
        most needs told."""
        orch = Orchestrator(Recorder())
        orch.start("chain", [("gather", []), ("write up", [1])])
        combined = orch.finished("t1", False, "")
        self.assertIn("undone", combined)
        self.assertIn("write up", combined)

    def test_a_lone_failure_names_the_step(self) -> None:
        orch = Orchestrator(Recorder())
        orch.start("one thing", [("only", [])])
        self.assertEqual(orch.finished("t1", False, ""), "I couldn't finish: only.")

    def test_a_success_that_said_nothing_still_answers(self) -> None:
        """A step can succeed and report nothing. The group must still say something
        rather than going silent, which would look like a hang."""
        orch = Orchestrator(Recorder())
        orch.start("one thing", [("only", [])])
        answer = orch.finished("t1", True, "")
        self.assertIn("That's done", answer)
        self.assertNotIn("worked", answer)   # it did work; saying otherwise is a lie

    def test_blocked_steps_are_marked(self) -> None:
        orch = Orchestrator(Recorder())
        group = orch.start("chain", [("gather", []), ("write up", [1])])
        orch.finished("t1", False, "")
        self.assertEqual(group.steps[0].status, FAILED)
        self.assertEqual(group.steps[1].status, BLOCKED)


class Limits(unittest.TestCase):
    def test_fan_out_is_capped(self) -> None:
        """A small model asked to decompose will decompose anything."""
        dispatch = Recorder()
        group = Orchestrator(dispatch, max_fanout=2).start(
            "too much", [("a", []), ("b", []), ("c", []), ("d", [])])
        self.assertEqual(len(group.steps), 2)
        self.assertEqual(dispatch.started, ["a", "b"])

    def test_no_capacity_means_no_group(self) -> None:
        orch = Orchestrator(Recorder(capacity=0))
        self.assertIsNone(orch.start("anything", [("a", [])]))
        self.assertEqual(orch.state()["groups"], [])

    def test_a_partly_dispatched_group_picks_up_the_rest_later(self) -> None:
        """One slot free: the second step starts when the first finishes."""
        dispatch = Recorder(capacity=1)
        orch = Orchestrator(dispatch)
        orch.start("two", [("a", []), ("b", [])])
        self.assertEqual(dispatch.started, ["a"])
        dispatch.capacity = 2
        orch.finished("t1", True, "a done")
        self.assertEqual(dispatch.started, ["a", "b"])

    def test_empty_steps_are_ignored(self) -> None:
        orch = Orchestrator(Recorder())
        self.assertIsNone(orch.start("nothing", [("", []), ("   ", [])]))

    def test_a_dependency_outside_the_plan_is_dropped(self) -> None:
        """Truncating at the cap must not leave a step waiting on one that is gone."""
        dispatch = Recorder()
        orch = Orchestrator(dispatch, max_fanout=2)
        group = orch.start("clipped", [("a", []), ("b", [9])])
        self.assertEqual(group.steps[1].after, [])
        self.assertEqual(dispatch.started, ["a", "b"])


class Visibility(unittest.TestCase):
    def test_status_reports_real_progress(self) -> None:
        orch = Orchestrator(Recorder())
        orch.start("two", [("gather the files", []), ("write it up", [1])])
        self.assertIn("0 of 2 steps done", orch.spoken())
        orch.finished("t1", True, "got them")
        spoken = orch.spoken()
        self.assertIn("1 of 2 steps done", spoken)
        self.assertIn("write it up", spoken)

    def test_nothing_running_says_nothing(self) -> None:
        self.assertEqual(Orchestrator(Recorder()).spoken(), "")

    def test_cancelling_forgets_the_group(self) -> None:
        orch = Orchestrator(Recorder())
        orch.start("two", [("a", []), ("b", [])])
        self.assertEqual(orch.cancel(), 1)
        self.assertFalse(orch.owns("t1"))


if __name__ == "__main__":
    unittest.main()
