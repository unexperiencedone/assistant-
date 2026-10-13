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

from assistant.controller import MAX_SPOKEN_SENTENCES, SKIPPED_NOTE, spoken_reply

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

    def test_a_reply_that_short_is_spoken_whole(self) -> None:
        """Four sentences, 300 characters: nothing needs to give way any more."""
        self.assertEqual(spoken_reply(THE_WORD_REPLY), THE_WORD_REPLY)

    def test_when_it_is_too_long_the_middle_gives_way_and_says_so(self) -> None:
        reply = "Opening line. " + ("A middle sentence that goes on and on. " * 40) + "Here is the offer."
        said = spoken_reply(reply)
        self.assertTrue(said.startswith("Opening line."))
        self.assertTrue(said.endswith("Here is the offer."))
        self.assertIn(SKIPPED_NOTE, said)

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


THE_PAIN_REPLY = (
    'Their latest video, out today, is "Insane Pain Facts, From Phantom Limbs to Brain Freeze." '
    "It's a countdown of 20 facts about pain. Pain is your body's alarm system. Chronic pain, stress, "
    "smoking and a bad diet all make it worse, and moving around or exercising helps because it releases "
    "endorphins. It also covers phantom limb pain, which over 80 percent of amputees report, brain freeze, "
    "gout, and black mamba venom being studied as a painkiller, then ends with naked mole rats and the rare "
    "people who can't feel pain at all."
)


class ShortEnoughIsSpokenWhole(unittest.TestCase):
    """A 527-character summary lost "Chronic pain, stress..." to the sentence cap, and what
    was left said "It also covers" about a sentence nobody heard."""

    def test_the_pain_summary_is_spoken_in_full(self) -> None:
        self.assertEqual(spoken_reply(THE_PAIN_REPLY), THE_PAIN_REPLY)

    def test_a_brief_profile_speaks_a_summary_whole_too(self) -> None:
        """Brief is asked of the writer, not enforced by cutting what was written."""
        from assistant.profile.render import SPOKEN_CHARS

        said = spoken_reply(THE_PAIN_REPLY, max_sentences=2, whole_chars=SPOKEN_CHARS["brief"])
        self.assertEqual(said, THE_PAIN_REPLY)

    def test_a_quote_ends_a_sentence(self) -> None:
        said = spoken_reply('He said "stop." Then a long middle. ' * 30 + "End?", max_sentences=3)
        self.assertTrue(said.startswith('He said "stop." Then'))


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
        said = spoken_reply("word " * 400)  # one sentence, past what is spoken whole
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
