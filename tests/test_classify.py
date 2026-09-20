"""The free-model router: it may point at a script, never invent one.

No network here. `classify()` is one HTTP call wrapped around `validate()`, and
`validate()` is where every decision that matters is made — so that is what is tested,
with the replies a model actually produces, including the bad ones.
"""

import unittest

from assistant.classify import _extract_json, catalogue, slots_of, validate

CORPUS = [
    ("automation", "play_song", "play {query} on spotify"),
    ("automation", "play_song", "put on {query}"),
    ("intent", "phone_find", "find my phone"),
    ("intent", "phone_sms_name", "text {name} saying {body}"),
]


class TestWhatTheModelIsShown(unittest.TestCase):
    def test_one_line_per_script_not_per_phrase(self):
        listed = catalogue(CORPUS)
        self.assertEqual(len(listed.splitlines()), 3, listed)
        self.assertIn("play {query} on spotify | put on {query}", listed)

    def test_slots_are_read_from_the_template(self):
        self.assertEqual(slots_of("text {name} saying {body}"), {"name", "body"})
        self.assertEqual(slots_of("find my phone"), set())


class TestTakingTheAnswer(unittest.TestCase):
    def test_a_good_pick(self):
        found = validate({"script": "play_song", "args": {"query": "karan aujla"},
                          "confidence": 0.9}, CORPUS)
        self.assertEqual((found.name, found.args["query"]), ("play_song", "karan aujla"))

    def test_none_means_hand_it_to_the_agent(self):
        for answer in ({"script": "none", "confidence": 1.0}, {"script": "", "confidence": 1.0},
                       {"script": "null"}, {}):
            self.assertIsNone(validate(answer, CORPUS), answer)

    def test_a_script_that_does_not_exist_is_refused(self):
        self.assertIsNone(validate({"script": "delete_everything", "args": {},
                                    "confidence": 1.0}, CORPUS))

    def test_an_argument_the_template_never_declared_is_dropped(self):
        found = validate({"script": "play_song",
                          "args": {"query": "x", "volume": "11", "rm": "-rf"},
                          "confidence": 0.9}, CORPUS)
        self.assertEqual(found.args, {"query": "x"})

    def test_a_slot_it_failed_to_fill_is_not_a_match(self):
        self.assertIsNone(validate({"script": "phone_sms_name", "args": {"name": "priya"},
                                    "confidence": 0.95}, CORPUS),
                          "half a text message is worse than none")

    def test_low_confidence_is_treated_as_none(self):
        self.assertIsNone(validate({"script": "play_song", "args": {"query": "x"},
                                    "confidence": 0.2}, CORPUS))

    def test_a_missing_or_junk_confidence_does_not_crash_it(self):
        for value in (None, "high", ""):
            self.assertIsNone(validate({"script": "play_song", "args": {"query": "x"},
                                        "confidence": value}, CORPUS), value)

    def test_args_that_are_not_even_a_mapping(self):
        self.assertIsNone(validate({"script": "play_song", "args": "query=x",
                                    "confidence": 0.9}, CORPUS))

    def test_a_script_with_no_slots_needs_no_args(self):
        found = validate({"script": "phone_find", "confidence": 0.8}, CORPUS)
        self.assertEqual((found.name, found.args), ("phone_find", {}))


class TestTakingJsonOutOfProse(unittest.TestCase):
    def test_bare_json(self):
        self.assertEqual(_extract_json('{"script": "none"}'), {"script": "none"})

    def test_json_in_a_code_fence(self):
        self.assertEqual(_extract_json('```json\n{"script": "phone_find"}\n```'),
                         {"script": "phone_find"})

    def test_nothing_usable_is_not_an_exception(self):
        self.assertEqual(_extract_json("I think you want to find your phone."), {})
        self.assertEqual(_extract_json(""), {})


if __name__ == "__main__":
    unittest.main()
