"""A plan is not an automation, and the two must never be answered for each other.

Observed: asked to clear the plans, Nova replied "the system can list but not delete the
saved automations". Every word of that was true and it was about the wrong thing. The
phrasing had missed the local rule, fallen through to an agent, and the agent had reached
for the only tool whose name looked close.

Two layers are tested here, because the fix needed both: the rules must catch the
phrasings locally, and if one still slips past, the agent must have the right tool and an
unambiguous description of it.
"""

from __future__ import annotations

import unittest

from assistant.agents.tools import ToolContext, call, schemas
from assistant.intents import match_intent, normalize
from assistant.planning import Plan


class Phrasings(unittest.TestCase):
    def test_the_phrase_that_failed(self) -> None:
        """The exact sentence from the transcript. "do one thing" is not filler the
        stripper knew about, so nothing matched and an agent got the question."""
        self.assertEqual(match_intent("okay do one thing clear all the plans that were made").name,
                         "clear_plan")

    def test_lead_ins_are_stripped(self) -> None:
        for said in ("can you clear the plans", "could you please clear the plans",
                     "just drop the plan", "i want you to clear the plan",
                     "go ahead and clear the plans", "let's clear the plans"):
            found = match_intent(said)
            self.assertIsNotNone(found, f"no rule matched {said!r}")
            self.assertEqual(found.name, "clear_plan", said)

    def test_a_lead_in_does_not_eat_the_instruction(self) -> None:
        """Stripping must not swallow words that carry meaning."""
        self.assertEqual(normalize("just drop the plan"), "drop the plan")
        self.assertEqual(normalize("can you open spotify"), "open spotify")
        self.assertEqual(normalize("play some music"), "play some music")

    def test_clearing_and_reading_both_match(self) -> None:
        for said in ("clear the plans", "drop that plan", "wipe the plans",
                     "scrap all the plans", "remove the plan"):
            self.assertEqual(match_intent(said).name, "clear_plan", said)
        for said in ("list the plans", "read the plan", "show me the plan",
                     "what's the plan", "read it back"):
            self.assertEqual(match_intent(said).name, "read_plan", said)


class DraftsAndPlansDoNotCollide(unittest.TestCase):
    """A rule added for the publishing gate was stealing plan phrasings: "drop that
    plan" matched publish_discard, because "drop that" is also how you bin a draft."""

    def test_dropping_a_plan_is_not_dropping_a_draft(self) -> None:
        self.assertEqual(match_intent("drop that plan").name, "clear_plan")

    def test_dropping_a_draft_still_works(self) -> None:
        for said in ("drop that", "drop the draft", "discard it", "bin the post",
                     "don't post that"):
            self.assertEqual(match_intent(said).name, "publish_discard", said)

    def test_it_does_not_claim_other_nouns_either(self) -> None:
        """A step, a goal or an automation is not a draft; falling through is honest,
        answering about drafts is not."""
        for said in ("drop that step", "forget that goal", "drop that automation"):
            found = match_intent(said)
            self.assertNotEqual(getattr(found, "name", None), "publish_discard", said)


class TheAgentsTools(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = Plan()
        self.plan.add_steps(["scaffold the site", "write the copy"], title="client site")

        class Automations:
            macros = [type("M", (), {"name": "calculator demo", "phrases": ["calculator demo"]})()]

        self.ctx = ToolContext(plan=self.plan, automations=Automations())

    def test_the_agent_can_read_and_clear_a_plan(self) -> None:
        """It could do neither before, which is why it answered about automations."""
        names = [s["function"]["name"] for s in schemas(self.ctx)]
        self.assertIn("read_plan", names)
        self.assertIn("clear_plan", names)

    def test_reading_a_plan_returns_the_plan(self) -> None:
        answer = call(self.ctx, "read_plan", {})
        self.assertIn("client site", answer)
        self.assertIn("scaffold the site", answer)
        self.assertNotIn("calculator demo", answer)

    def test_clearing_a_plan_leaves_automations_alone(self) -> None:
        answer = call(self.ctx, "clear_plan", {})
        self.assertTrue(self.plan.is_empty)
        self.assertIn("untouched", answer)
        self.assertIn("calculator demo", call(self.ctx, "list_automations", {}))

    def test_an_empty_plan_says_so_and_points_at_the_other_thing(self) -> None:
        self.plan.clear()
        answer = call(self.ctx, "read_plan", {})
        self.assertIn("no plan", answer.lower())
        self.assertIn("automations", answer.lower())

    def test_the_descriptions_distinguish_them(self) -> None:
        """The model reads these, so this is where the confusion actually got fixed."""
        by_name = {s["function"]["name"]: s["function"]["description"] for s in schemas(self.ctx)}
        self.assertIn("NOT the plan", by_name["list_automations"])
        self.assertIn("read_plan", by_name["list_automations"])

    def test_status_reports_the_plan(self) -> None:
        class Runner:
            def status_sentence(self) -> str:
                return ""

        answer = call(ToolContext(plan=self.plan, runner=Runner()), "nova_status", {})
        self.assertIn("client site", answer)

    def test_plan_tools_are_hidden_when_there_is_no_plan_object(self) -> None:
        names = [s["function"]["name"] for s in schemas(ToolContext())]
        self.assertNotIn("read_plan", names)
        self.assertNotIn("clear_plan", names)


if __name__ == "__main__":
    unittest.main()
