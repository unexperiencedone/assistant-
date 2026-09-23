"""A message written to go out to somebody who is not the owner.

Every draft in here is one a live test actually produced or nearly produced. Asked to
draft outreach for the owner's web-design work, Groq wrote:

    "I'm Nova, a local web-designer ... at a special introductory rate
     (or even free for the first month)."

It signed as Nova, and it gave away a month of work nobody had authorised. The rule
against the second already existed in CUSTOMER_REGISTER -- "never commit to a price, a
deadline or a change of scope" -- and was simply never applied to drafting.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant.agents.tools import ToolContext, call, schemas
from assistant.persona import outreach
from assistant.publish import PublishService

REAL_BAD = ("I'm Nova, a local web-designer helping small Lucknow businesses get a modern site. "
            "I'd love to build you a professional site at a special introductory rate, or even "
            "free for the first month. Delivered within 3 days, and I guarantee you'll rank first.")

CLEAN = ("Hi, I'm Aakshant. I build websites for small businesses in Lucknow, and I noticed "
         "yours could use a refresh. If it's useful, I'd be glad to talk through what that "
         "would take.")


class TheDraftThatFailed(unittest.TestCase):
    def test_every_problem_in_it_is_caught(self) -> None:
        found = " | ".join(outreach.problems(REAL_BAD, nova="Nova", owner="Aakshant"))
        self.assertIn("signed as Nova", found)
        self.assertIn("free", found)
        self.assertIn("timescale", found)
        self.assertIn("guarantee", found)

    def test_a_clean_draft_passes(self) -> None:
        self.assertEqual(outreach.problems(CLEAN, owner="Aakshant"), [])
        self.assertEqual(outreach.verdict(CLEAN, owner="Aakshant"), "")


class WhatItRefuses(unittest.TestCase):
    def test_prices_in_any_currency(self) -> None:
        for said in ("It will cost 15,000 rupees.", "Around ₹8000 for the build.",
                     "$500 up front.", "About Rs. 12000."):
            self.assertTrue(outreach.problems(said), said)

    def test_giving_work_away(self) -> None:
        for said in ("The first audit is free of charge.", "I'll do a free mockup.",
                     "20% off this month.", "A complimentary consultation.",
                     "at a special rate"):
            self.assertTrue(outreach.problems(said), said)

    def test_promising_when(self) -> None:
        for said in ("Done within 3 days.", "Live by Friday.", "Same-day turnaround."):
            self.assertTrue(outreach.problems(said), said)

    def test_promising_outcomes(self) -> None:
        for said in ("I guarantee results.", "We'll rank you first on Google.",
                     "100% satisfaction."):
            self.assertTrue(outreach.problems(said), said)


class WhatItLeavesAlone(unittest.TestCase):
    """A guard that fires on ordinary sentences would be turned off, so the false
    positives matter as much as the catches."""

    def test_ordinary_outreach(self) -> None:
        for said in ("I build websites for small businesses.",
                     "I can write 500 words of copy for the homepage.",
                     "I noticed your site isn't mobile friendly.",
                     "Happy to talk through what it would take.",
                     "I've worked with a few restaurants in the city."):
            self.assertEqual(outreach.problems(said, owner="Aakshant"), [], said)

    def test_naming_nova_is_fine_when_nova_is_not_the_author(self) -> None:
        """"Nova drafted this" is not the same as "I'm Nova"."""
        self.assertEqual(outreach.problems("My assistant Nova put this together.",
                                           owner="Aakshant"), [])


class ThroughTheTool(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.pub = PublishService(Path(self.dir.name) / "p.db")
        self.ctx = ToolContext(publisher=self.pub, assistant_name="Nova",
                               profile=type("P", (), {"preferred_name": "Aakshant"})())

    def tearDown(self) -> None:
        self.pub.close()
        self.dir.cleanup()

    def test_a_bad_draft_is_not_staged_at_all(self) -> None:
        """Handed back to be rewritten, not stored for the user to catch later."""
        answer = call(self.ctx, "draft_outreach",
                      {"recipient": "a@b.in", "subject": "Website", "body": REAL_BAD})
        self.assertIn("can't go out as written", answer)
        self.assertEqual(self.pub.gate.pending(), [])

    def test_a_clean_draft_is_staged_and_not_sent(self) -> None:
        answer = call(self.ctx, "draft_outreach",
                      {"recipient": "a@b.in", "subject": "Your website", "body": CLEAN})
        self.assertIn("Nothing has been sent", answer)
        self.assertEqual(len(self.pub.gate.pending()), 1)

    def test_approving_says_plainly_that_email_is_not_wired_up(self) -> None:
        """And does not claim the draft will send itself later, which it won't."""
        call(self.ctx, "draft_outreach",
             {"recipient": "a@b.in", "subject": "Your website", "body": CLEAN})
        answer = self.pub.approve()
        self.assertIn("can't send email yet", answer)
        self.assertNotIn("it'll go out once", answer)
        self.assertEqual(self.pub.gate.pending(), [], "a refused send leaves the queue")
        self.assertTrue(self.pub.gate.recent(), "but it stays in the record")

    def test_the_tool_is_hidden_without_publishing(self) -> None:
        names = [s["function"]["name"] for s in schemas(ToolContext())]
        self.assertNotIn("draft_outreach", names)

    def test_its_description_tells_the_model_the_rules(self) -> None:
        by_name = {s["function"]["name"]: s["function"]["description"] for s in schemas(self.ctx)}
        described = by_name["draft_outreach"]
        self.assertIn("FIRST PERSON AS THE USER", described)
        self.assertIn("price", described)


class BrowserTools(unittest.TestCase):
    def test_they_are_off_unless_enabled(self) -> None:
        off = [s["function"]["name"] for s in schemas(ToolContext())]
        self.assertEqual([n for n in off if n.startswith("browse_")], [])

    def test_they_appear_when_enabled(self) -> None:
        on = [s["function"]["name"] for s in schemas(ToolContext(browser=True))]
        for name in ("browse_open", "browse_read", "browse_click", "browse_fill"):
            self.assertIn(name, on)

    def test_a_bad_url_is_refused_without_launching_anything(self) -> None:
        answer = call(ToolContext(browser=True), "browse_open", {"url": "not-a-url"})
        self.assertIn("full http", answer)


if __name__ == "__main__":
    unittest.main()
