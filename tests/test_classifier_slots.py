"""The free-model router chooses a script; it does not get to compose the arguments.

`classify.py` says "the model is a pointer, not a source", and honoured that for the
script name -- checked against the corpus -- while taking the slot values verbatim.
Asked "brief me on recent news", it picked the right script and returned
topic="brief me on recent", which the automation then said out loud:

    "Here's the latest on brief me on recent. Top story: ..."

Two fixes, tested here. The values are now checked against what was actually said, and
that phrasing is an instant intent so it never reaches the router at all.
"""

from __future__ import annotations

import dataclasses
import unittest

from assistant.classify import slots_are_sane, validate
from assistant.intents import match_intent

TEMPLATE = "pull up some {topic} news"
CORPUS = [("macro", "News briefing", TEMPLATE)]


def answer(args: dict, confidence: float = 0.95) -> dict:
    return {"script": "News briefing", "confidence": confidence, "args": args}


class SlotSanity(unittest.TestCase):
    def test_the_value_that_was_said_out_loud(self) -> None:
        self.assertFalse(slots_are_sane("brief me on recent news", TEMPLATE,
                                        {"topic": "brief me on recent"}))

    def test_a_real_subject_passes(self) -> None:
        for said, value in (("pull up some finance news", "finance"),
                            ("pull up some artificial intelligence news", "artificial intelligence"),
                            ("show me cricket news", "cricket")):
            self.assertTrue(slots_are_sane(said, TEMPLATE, {"topic": value}), value)

    def test_nothing_may_be_invented(self) -> None:
        """A value has to come out of the request, not out of the model."""
        self.assertFalse(slots_are_sane("pull up some finance news", TEMPLATE,
                                        {"topic": "stock market"}))

    def test_a_value_may_not_be_a_whole_sentence(self) -> None:
        said = "pull up some news about what is happening in the markets today please"
        self.assertFalse(slots_are_sane(said, TEMPLATE,
                                        {"topic": "what is happening in the markets today"}))

    def test_a_value_may_not_start_like_an_instruction(self) -> None:
        """An imperative verb or the first person means the value is the request."""
        for value in ("me finance", "tell finance", "show finance", "my finance"):
            self.assertFalse(slots_are_sane("tell me show my finance news", TEMPLATE,
                                            {"topic": value}), value)

    def test_a_determiner_is_allowed_to_start_one(self) -> None:
        """"some old punk" and "the beatles" are real queries. Rejecting them broke a
        matcher test that was right: the check is for a swallowed instruction, not for
        a noun phrase beginning with a small word."""
        for said, value in (("play some old punk on spotify", "some old punk"),
                            ("put the beatles on spotify", "the beatles")):
            self.assertTrue(slots_are_sane(said, "play {topic} on spotify",
                                           {"topic": value}), value)

    def test_a_value_may_not_contain_the_templates_own_words(self) -> None:
        """"{topic} news" with topic="recent news" means the split went wrong."""
        self.assertFalse(slots_are_sane("pull up some recent news", TEMPLATE,
                                        {"topic": "recent news"}))

    def test_an_empty_value_is_not_sane(self) -> None:
        self.assertFalse(slots_are_sane("pull up some news", TEMPLATE, {"topic": "  "}))


class ThroughValidate(unittest.TestCase):
    def test_a_bad_value_makes_the_whole_guess_none(self) -> None:
        """"None" is a first-class answer here: the request goes on down the cascade
        instead of running a script with nonsense in it."""
        self.assertIsNone(validate(answer({"topic": "brief me on recent"}), CORPUS,
                                   said="brief me on recent news"))

    def test_a_good_value_still_gets_through(self) -> None:
        guess = validate(answer({"topic": "finance"}), CORPUS, said="pull up some finance news")
        self.assertIsNotNone(guess)
        self.assertEqual(guess.args, {"topic": "finance"})

    def test_the_check_is_skipped_when_there_is_nothing_to_check_against(self) -> None:
        """Older callers pass no utterance; they must keep working."""
        self.assertIsNotNone(validate(answer({"topic": "finance"}), CORPUS))

    def test_a_script_outside_the_corpus_is_still_refused(self) -> None:
        self.assertIsNone(validate({"script": "Wipe the disk", "confidence": 1.0, "args": {}},
                                   CORPUS, said="anything"))


class NewsPhrasings(unittest.TestCase):
    """The cheapest fix is for the phrasing never to reach a model at all."""

    def _args(self, said: str) -> dict:
        found = match_intent(said)
        self.assertIsNotNone(found, f"nothing matched {said!r}")
        self.assertEqual(found.name, "news_briefing", said)
        return dataclasses.asdict(found).get("args") or {}

    def test_the_phrasing_that_broke(self) -> None:
        self.assertEqual(self._args("brief me on recent news"), {})

    def test_the_family_around_it(self) -> None:
        for said in ("brief me on the news", "brief me on news", "the latest news",
                     "news briefing", "give me the news", "what's the news"):
            self.assertEqual(self._args(said), {}, said)

    def test_a_topic_is_still_picked_up(self) -> None:
        self.assertEqual(self._args("brief me on news about ai"), {"topic": "ai"})
        self.assertEqual(self._args("news briefing for finance"), {"topic": "finance"})

    def test_a_complaint_about_the_news_is_not_a_request_for_news(self) -> None:
        """Both of these were said in anger at a briefing and must not fetch another."""
        for said in ("this is not news this is more of an article",
                     "you haven't told me proper news"):
            self.assertIsNone(match_intent(said), said)


if __name__ == "__main__":
    unittest.main()
