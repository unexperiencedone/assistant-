"""Shortening a reply for speech without losing the point of it.

From a real reply, four sentences long:

    The customer segments document is open in Word now. At first it wouldn't open
    because the tool that wrote it left a hidden copy of Word running, which kept the
    file locked. I closed that leftover. There's also an empty Word window from the
    first try, and I can fix the writer so it doesn't leave Word running again if you
    want.

Three sentences were spoken and the fourth was dropped in silence -- the one containing
the offer, the only sentence that needed an answer. Keeping the first N sentences means
the *end* is always what goes missing, and the end is where a conclusion or a question
lives. Detail belongs in the middle of a reply; a decision to make does not.

It also broke listening. `say` opens the follow-up window only when what was spoken
ends in a question mark, so a question truncated away left Nova having asked nothing
and not waiting for a reply.
"""

from __future__ import annotations

import unittest

from assistant.controller import MAX_SPOKEN_SENTENCES, spoken_reply

THE_WORD_REPLY = (
    "The customer segments document is open in Word now. At first it wouldn't open "
    "because the tool that wrote it left a hidden copy of Word running, which kept the "
    "file locked. I closed that leftover. There's also an empty Word window from the "
    "first try, and I can fix the writer so it doesn't leave Word running again if you want."
)


class TheEndingSurvives(unittest.TestCase):
    def test_the_offer_is_no_longer_dropped(self) -> None:
        said = spoken_reply(THE_WORD_REPLY)
        self.assertIn("if you want", said)

    def test_the_opening_survives_too(self) -> None:
        self.assertTrue(spoken_reply(THE_WORD_REPLY).startswith("The customer segments"))

    def test_the_middle_is_what_gives_way(self) -> None:
        """"I closed that leftover" is detail; the offer is not."""
        said = spoken_reply(THE_WORD_REPLY)
        self.assertNotIn("I closed that leftover", said)

    def test_a_closing_question_always_makes_it(self) -> None:
        """And with it, the follow-up window: `say` opens the microphone only when what
        was spoken ends in a question mark."""
        for extra in (4, 10, 30):
            reply = " ".join(f"Sentence {n}." for n in range(extra)) + " Shall I carry on?"
            self.assertTrue(spoken_reply(reply).rstrip().endswith("?"), extra)


class ShortRepliesAreLeftAlone(unittest.TestCase):
    def test_one_sentence(self) -> None:
        self.assertEqual(spoken_reply("Done."), "Done.")

    def test_up_to_the_cap(self) -> None:
        reply = " ".join(f"Line {n}." for n in range(MAX_SPOKEN_SENTENCES))
        self.assertEqual(spoken_reply(reply), reply)

    def test_nothing_in_nothing_out(self) -> None:
        self.assertEqual(spoken_reply(""), "")
        self.assertEqual(spoken_reply("   \n  "), "")


class TheCharacterBudget(unittest.TestCase):
    def test_it_is_respected(self) -> None:
        reply = "Opening line. " + ("A middle sentence that goes on and on. " * 40) + "Shall I continue?"
        said = spoken_reply(reply)
        self.assertLessEqual(len(said), 450)

    def test_whole_sentences_give_way_rather_than_half_a_word(self) -> None:
        reply = "Opening line. " + ("A middle sentence that goes on and on. " * 40) + "Shall I continue?"
        said = spoken_reply(reply)
        self.assertNotIn("...", said, "a word was cut instead of a sentence being dropped")
        self.assertTrue(said.startswith("Opening line."))
        self.assertTrue(said.endswith("Shall I continue?"))

    def test_one_enormous_sentence_has_nothing_to_drop(self) -> None:
        """Then, and only then, a word is cut and it says so."""
        said = spoken_reply("word " * 200)
        self.assertLessEqual(len(said), 450)
        self.assertTrue(said.endswith("..."))


class StillStripsWhatItAlwaysDid(unittest.TestCase):
    def test_code_fences_go(self) -> None:
        self.assertNotIn("print", spoken_reply("Here it is. ```python\nprint(1)\n``` Done."))

    def test_markdown_marks_go(self) -> None:
        said = spoken_reply("**Done.** The `file` is open.")
        self.assertNotIn("*", said)
        self.assertNotIn("`", said)

    def test_bullets_become_sentences(self) -> None:
        said = spoken_reply("Findings:\n- first thing\n- second thing")
        self.assertNotIn("-", said)
        self.assertIn("first thing", said)


if __name__ == "__main__":
    unittest.main()
