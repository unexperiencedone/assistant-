"""Tests for the heuristics the live canvas depends on: which plan step an action
belongs to, and how the graph is built from bus events."""

import unittest

from assistant.events import EventBus
from assistant.planning import Plan
from assistant.planning.plan import strip_markup
from assistant.planning.graph import GraphTracker

SITE_PLAN = """[[PLAN: Site]]
1. Create a folder called site
2. Write index.html inside site with a page linked to style.css
3. Write style.css inside site (after 1)
4. Write a README describing index.html and style.css (after 2, 3)
[[/PLAN]]"""


def statuses(plan):
    return "".join(step.status[0] for step in plan.steps)


class PlanParsing(unittest.TestCase):
    def test_dependencies_and_defaults(self):
        plan = Plan()
        self.assertTrue(plan.replace_from_block(SITE_PLAN))
        self.assertEqual([s.after for s in plan.steps], [[], [1], [1], [2, 3]])

    def test_same_plan_twice_is_not_a_change(self):
        plan = Plan()
        plan.replace_from_block(SITE_PLAN)
        self.assertFalse(plan.replace_from_block(SITE_PLAN))

    def test_removing_a_step_rewires_dependencies(self):
        plan = Plan()
        plan.replace_from_block(SITE_PLAN)
        plan.remove_step(2)
        self.assertEqual([s.text for s in plan.steps][0], "Create a folder called site")
        self.assertEqual([s.after for s in plan.steps], [[], [1], [1, 2]])


class ReorderingSteps(unittest.TestCase):
    def setUp(self):
        self.plan = Plan()
        self.plan.replace_from_block(SITE_PLAN)   # 1; 2 after 1; 3 after 1; 4 after 2,3

    def texts(self):
        return [s.text.split()[0] + s.text.split()[1] for s in self.plan.steps]

    def test_moving_a_step_later_renumbers_dependencies(self):
        self.plan.move_step(1, 4)                 # "Create a folder" goes last
        self.assertEqual([s.after for s in self.plan.steps], [[], [], [1, 2], []])

    def test_moving_a_step_earlier_keeps_the_order_of_the_rest(self):
        before = [s.text for s in self.plan.steps]
        self.plan.move_step(4, 1)
        self.assertEqual([s.text for s in self.plan.steps], [before[3], before[0], before[1], before[2]])

    def test_dependencies_never_point_forwards_after_a_move(self):
        self.plan.move_step(2, 4)
        for position, step in enumerate(self.plan.steps, 1):
            self.assertTrue(all(dep < position for dep in step.after), f"step {position} depends on later work")

    def test_out_of_range_moves_do_nothing(self):
        before = [s.text for s in self.plan.steps]
        self.assertIsNone(self.plan.move_step(9, 1))
        self.assertIsNone(self.plan.move_step(1, 1))
        self.assertEqual([s.text for s in self.plan.steps], before)


class StepInference(unittest.TestCase):
    def setUp(self):
        self.plan = Plan()
        self.plan.replace_from_block(SITE_PLAN)
        self.plan.begin_execution()

    def test_actions_walk_the_plan(self):
        self.assertEqual(statuses(self.plan), "rppp")
        self.plan.infer_from_action("index.html")
        self.assertEqual(statuses(self.plan), "drpp")
        self.plan.infer_from_action("style.css")
        self.assertEqual(statuses(self.plan), "ddrp")
        self.plan.infer_from_action("README.md")
        self.assertEqual(statuses(self.plan), "dddr")

    def test_mention_in_a_later_step_does_not_steal_the_action(self):
        # Step 2 mentions style.css in passing; step 3 is the one about it.
        self.plan.infer_from_action("index.html")
        self.plan.infer_from_action("style.css")
        self.assertEqual(self.plan.steps[2].status, "running")

    def test_overlapping_file_names_prefer_the_step_that_is_about_it(self):
        plan = Plan()
        plan.replace_from_block("[[PLAN: X]]\n1. Fix the bug in login.py\n2. Write tests for login.py\n[[/PLAN]]")
        plan.begin_execution()
        plan.infer_from_action("test_login.py")
        self.assertEqual(plan.steps[1].status, "running", "a test file belongs to the testing step")

    def test_unrelated_action_changes_nothing(self):
        before = statuses(self.plan)
        self.plan.infer_from_action("git status")
        self.assertEqual(statuses(self.plan), before)

    def test_markers_override_inference(self):
        self.plan.infer_from_action("index.html")
        self.plan.apply_markers("[[STEP 4 START]]")
        self.assertEqual(self.plan.steps[3].status, "running")


class GraphBuilding(unittest.TestCase):
    def setUp(self):
        self.bus, self.plan = EventBus(), Plan()
        self.tracker = GraphTracker(self.bus, self.plan)

    def run_request(self, label="do a thing", actions=3):
        self.bus.publish("route", utterance=label, route="agent", name="task", backend="Claude Code")
        self.bus.publish("agent", kind="start", backend="Claude Code", text=label)
        for i in range(actions):
            self.bus.publish("agent", kind="tool", backend="Claude Code", tool="Bash", text=f"command number {i}")
        self.bus.publish("agent", kind="result", backend="Claude Code", text="finished")

    def test_all_actions_are_kept_even_though_few_are_shown(self):
        self.run_request(actions=20)
        node = next(n for n in self.tracker.run_detail()["nodes"] if n["kind"] == "agent")
        self.assertEqual(len(node["actions"]), 20)
        shown = next(n for n in self.tracker.to_dict()["nodes"] if n["kind"] == "agent")
        self.assertEqual(len(shown["actions"]), 4)
        self.assertEqual(shown["action_count"], 20)

    def test_previous_request_is_kept_in_history(self):
        self.run_request("first")
        self.run_request("second")
        history = self.tracker.to_dict()["history"]
        self.assertEqual([h["label"] for h in history], ["first"])
        self.assertEqual(self.tracker.run_detail(1)["label"], "first")

    def test_local_command_that_falls_through_is_not_kept_as_history(self):
        # "find my resume" matches a local pattern, finds nothing, then goes to the agent:
        # one request, one history entry, not two.
        self.bus.publish("route", utterance="find my resume", route="local", name="find_item")
        self.run_request("find my resume")          # the fall-through to the agent
        self.run_request("something else")
        self.assertEqual([h["label"] for h in self.tracker.to_dict()["history"]], ["find my resume"])

    def test_local_command_has_no_agent_node(self):
        self.bus.publish("route", utterance="find my resume", route="local", name="find_item")
        kinds = [n["kind"] for n in self.tracker.to_dict()["nodes"]]
        self.assertEqual(kinds, ["request", "route"])

    def test_automation_and_agent_keep_separate_graphs(self):
        # An automation starting mid-task must not steal the agent's graph, and the
        # agent's later actions must not land in the automation's picture.
        self.bus.publish("route", utterance="fix the tests", route="agent", name="task",
                         backend="Claude Code", task="t1")
        self.bus.publish("agent", kind="start", backend="Claude Code", text="fix the tests", task="t1")
        self.bus.publish("route", utterance="calculator demo", route="macro", name="Calculator demo",
                         steps=["Open Calculator", "Click Five"])
        self.bus.publish("macro_step", index=0, status="done", detail="opened Calculator")
        self.bus.publish("agent", kind="tool", backend="Claude Code", tool="Edit",
                         text="tests/test_app.py", task="t1")

        snapshot = self.tracker.to_dict()
        agent_lane = snapshot["lanes"]["t1"]
        macro_lane = snapshot["lanes"]["macro"]
        self.assertEqual(agent_lane["label"], "fix the tests")
        self.assertEqual(macro_lane["label"], "calculator demo")
        agent_node = next(n for n in agent_lane["nodes"] if n["kind"] == "agent")
        self.assertEqual([a["tool"] for a in agent_node["actions"]], ["Edit"])
        self.assertEqual(snapshot["lane"], "t1", "the canvas follows whatever moved last")
        step = next(n for n in macro_lane["nodes"] if n["id"] == "step1")
        self.assertEqual(step["status"], "done")

    def test_two_agent_tasks_run_in_separate_lanes(self):
        for task, label in (("t1", "write the report"), ("t2", "fix the failing test")):
            self.bus.publish("route", utterance=label, route="agent", name="task",
                             backend="Claude Code", task=task)
            self.bus.publish("agent", kind="start", backend="Claude Code", text=label, task=task)
        self.bus.publish("agent", kind="tool", backend="Claude Code", tool="Write", text="report.md", task="t1")
        self.bus.publish("agent", kind="tool", backend="Claude Code", tool="Bash", text="pytest -q", task="t2")
        self.bus.publish("agent", kind="result", backend="Claude Code", text="report written", task="t1")

        lanes = self.tracker.to_dict()["lanes"]
        self.assertEqual(lanes["t1"]["label"], "write the report")
        self.assertEqual(lanes["t2"]["label"], "fix the failing test")
        tools = {name: [a["tool"] for n in lane["nodes"] for a in n["actions"]] for name, lane in lanes.items()}
        self.assertEqual(tools["t1"], ["Write"])
        self.assertEqual(tools["t2"], ["Bash"])
        # Finishing one task must not mark the other's work as finished.
        self.assertTrue(any(n["kind"] == "result" for n in lanes["t1"]["nodes"]))
        self.assertFalse(any(n["kind"] == "result" for n in lanes["t2"]["nodes"]))

    def test_a_plan_with_no_request_is_still_drawn(self):
        # e.g. the plan restored at startup, or edited before anything runs
        self.plan.replace_from_block(SITE_PLAN)
        self.bus.publish("plan", **self.plan.to_dict())
        kinds = [n["kind"] for n in self.tracker.to_dict()["nodes"]]
        self.assertEqual(kinds.count("step"), 4)
        self.assertIn("request", kinds)

    def test_plan_steps_become_a_dag(self):
        self.bus.publish("route", utterance="go ahead", route="agent", name="execute_plan", backend="Claude Code")
        self.plan.replace_from_block(SITE_PLAN)
        self.bus.publish("plan", **self.plan.to_dict())
        edges = {(e["source"], e["target"]) for e in self.tracker.to_dict()["edges"]}
        self.assertIn(("step1", "step2"), edges)
        self.assertIn(("step2", "step4"), edges)
        self.assertIn(("step3", "step4"), edges)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestUnclosedPlanBlock(unittest.TestCase):
    """Agents forget [[/PLAN]]. The plan must still be adopted, and the markup must
    still never reach the reply that gets spoken."""

    REPLY = (
        "[[PLAN: Simple to-do list web app]]\n"
        "1. Create a project folder with index.html, style.css and app.js\n"
        "2. Build the markup: a heading and an add button (after 1)\n"
        "3. Write the JavaScript: add, toggle done, delete (after 2)\n"
        "\n"
        "That's a three step plan, no build tools needed. Say go ahead and I'll build it."
    )

    def test_a_plan_without_a_closing_tag_is_adopted(self):
        plan = Plan()
        self.assertTrue(plan.replace_from_block(self.REPLY))
        self.assertEqual([step.text for step in plan.steps], [
            "Create a project folder with index.html, style.css and app.js",
            "Build the markup: a heading and an add button",
            "Write the JavaScript: add, toggle done, delete",
        ])
        self.assertEqual(plan.steps[2].after, [2])

    def test_the_prose_after_it_survives_and_the_markup_does_not(self):
        spoken = strip_markup(self.REPLY)
        self.assertNotIn("[[PLAN", spoken)
        self.assertNotIn("1. Create a project folder", spoken)
        self.assertTrue(spoken.startswith("That's a three step plan"))

    def test_a_closed_block_still_wins_over_the_list_boundary(self):
        text = "[[PLAN: X]]\n1. One\n2. Two\n[[/PLAN]]\n1. Not a step, just prose."
        plan = Plan()
        self.assertTrue(plan.replace_from_block(text))
        self.assertEqual(len(plan.steps), 2)

    def test_a_lone_tag_with_no_steps_is_not_a_plan(self):
        plan = Plan()
        self.assertFalse(plan.replace_from_block("[[PLAN: nothing here]] I'll just do it."))
