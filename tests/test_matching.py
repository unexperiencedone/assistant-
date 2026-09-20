"""Catching a request said a different way, without catching a different request.

The matcher may only ever resolve to a script that already exists. Everything here is
about the two ways that goes wrong: missing something it should catch (a wasted model
turn) and catching something it shouldn't (running the wrong thing).
"""

import unittest
from datetime import date

from assistant.matching import (CANONICAL, CONFIDENT, SYNONYMS, best_guess, score_template,
                                score_unordered, tokens)
from assistant.routes import RouteCounter, chart, week_of

CORPUS = [
    ("automation", "play_song", "play {query} on spotify"),
    ("automation", "next_track", "next track"),
    ("automation", "wikipedia_search", "search wikipedia for {query}"),
    ("intent", "phone_find", "find my phone"),
    ("intent", "phone_torch", "turn on the torch"),
    ("intent", "phone_sms_name", "text {name} saying {body}"),
]


class TestTheSameThingSaidDifferently(unittest.TestCase):
    def guess(self, said):
        return best_guess(said, CORPUS)

    def test_filler_words_do_not_stop_a_match(self):
        found = self.guess("could you please put karan aujla on spotify")
        self.assertEqual(found.name, "play_song")
        self.assertEqual(found.args["query"], "karan aujla")
        self.assertGreaterEqual(found.score, CONFIDENT)

    def test_a_different_word_for_the_same_thing(self):
        for said in ("locate my mobile", "where's my cell", "i can't find my phone"):
            with self.subTest(said=said):
                self.assertEqual(self.guess(said).name, "phone_find")

    def test_reordering(self):
        found = self.guess("spotify play some old punk")
        self.assertEqual(found.name, "play_song")
        self.assertEqual(found.args["query"], "some old punk")

    def test_a_synonym_in_the_verb(self):
        self.assertEqual(self.guess("switch on the flashlight").name, "phone_torch")
        self.assertEqual(self.guess("skip this tune").name, "next_track")

    def test_two_slots_still_come_out_in_the_right_order(self):
        found = self.guess("text priya saying running late")
        self.assertEqual(found.name, "phone_sms_name")
        self.assertEqual(found.args, {"name": "priya", "body": "running late"})


class TestNotCatchingTheWrongThing(unittest.TestCase):
    def test_an_unrelated_question_goes_to_the_agent(self):
        for said in ("what is the capital of peru", "write me a python script",
                     "how long does it take to get to delhi", "summarise this document"):
            with self.subTest(said=said):
                self.assertIsNone(best_guess(said, CORPUS), said)

    def test_an_empty_slot_is_not_a_match(self):
        self.assertIsNone(score_template("play {query} on spotify", tokens("play on spotify")))

    def test_a_longer_different_request_containing_the_words_is_penalised(self):
        found = best_guess("play something on youtube instead of spotify", CORPUS)
        if found:
            self.assertLess(found.score, CONFIDENT,
                            "close enough to ask about, never close enough to just run")

    def test_nothing_outside_the_corpus_can_ever_be_returned(self):
        names = {name for _, name, _ in CORPUS}
        for said in ("play x on spotify", "locate my mobile", "next track"):
            found = best_guess(said, CORPUS)
            self.assertIn(found.name, names)


class TestTheSynonymTableItself(unittest.TestCase):
    def test_no_word_is_both_a_base_and_somebody_elses_variant(self):
        """The bug that made "find my phone" tokenise as "find call".

        `phone` was a base word for the device and also a variant of `call`, so one
        group ate the other. The module asserts this at import; this is the guard that
        explains why.
        """
        self.assertEqual(sorted(set(SYNONYMS) & set(CANONICAL)), [])

    def test_my_phone_keeps_its_my_however_it_was_said(self):
        self.assertEqual(tokens("my mobile"), tokens("my phone"))


class TestCountingWhichRouteAnswered(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.path = __import__("pathlib").Path(tempfile.mkdtemp()) / "routes.json"

    def test_it_counts_and_survives_a_restart(self):
        counter = RouteCounter(self.path)
        counter.record("intent")
        counter.record("agent")
        counter.record("automation")
        self.assertEqual(RouteCounter(self.path).totals(),
                         {"intent": 1, "agent": 1, "automation": 1})

    def test_the_free_share_is_what_the_design_claims(self):
        counter = RouteCounter(self.path)
        for _ in range(3):
            counter.record("automation")
        counter.record("agent")
        self.assertAlmostEqual(RouteCounter.free_share(counter.totals()), 0.75)

    def test_no_data_is_reported_as_no_data_not_as_zero(self):
        self.assertIsNone(RouteCounter.free_share({}))
        self.assertIn("Nothing counted yet", chart(RouteCounter(self.path)))

    def test_weeks_are_kept_apart(self):
        counter = RouteCounter(self.path)
        counter.record("agent", when=date(2026, 9, 1))
        counter.record("intent", when=date(2026, 9, 20))
        self.assertEqual(len(counter.weeks), 2)
        self.assertIn(week_of(date(2026, 9, 1)), counter.weeks)


if __name__ == "__main__":
    unittest.main()
