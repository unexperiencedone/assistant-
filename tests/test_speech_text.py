"""Turning a written reply into something a voice reads correctly.

Every case here was heard wrong out loud. The pattern behind all of them is the same:
a rule that is right between words is wrong inside a figure, because punctuation inside
a number is not punctuation.
"""

from __future__ import annotations

import unittest

from assistant.audio.tts import _for_speech


class Numbers(unittest.TestCase):
    def test_a_thousands_separator_is_removed(self) -> None:
        """"Spotify is backing 10,000 new artists" was read as "ten, zero zero zero":
        the rule that spaces after a comma split the number into two tokens."""
        self.assertIn("10000", _for_speech("Spotify is backing 10,000 new artists."))
        self.assertNotIn("10, 000", _for_speech("Spotify is backing 10,000 new artists."))

    def test_bigger_numbers_too(self) -> None:
        self.assertIn("1250000", _for_speech("About 1,250,000 users."))

    def test_a_comma_between_words_keeps_its_pause(self) -> None:
        """The pause is how a local voice gets its rhythm; only figures are affected."""
        self.assertIn("Wait, 5", _for_speech("Wait, 5 minutes."))
        self.assertIn("read, then", _for_speech("Steps: read, then write."))


class Times(unittest.TestCase):
    def test_a_clock_colon_is_not_spaced(self) -> None:
        """"2:53" became "2: 53" and was read with a pause in the middle of the time."""
        self.assertIn("2:53", _for_speech("It's 2:53 AM."))
        self.assertNotIn("2: 53", _for_speech("It's 2:53 AM."))

    def test_a_leading_zero_goes(self) -> None:
        """"02:53" is read as "zero two"; nobody says the hour that way."""
        self.assertIn("2:53", _for_speech("It is about 02:53 AM."))
        self.assertNotIn("02:53", _for_speech("It is about 02:53 AM."))
        self.assertIn("9:00", _for_speech("The meeting is at 09:00."))

    def test_a_ratio_survives(self) -> None:
        self.assertIn("3:1", _for_speech("Ratio 3:1 now."))

    def test_a_colon_between_words_still_gets_its_space(self) -> None:
        self.assertIn("Steps: read", _for_speech("Steps:read, then write."))

    def test_the_whole_sentence_that_was_wrong(self) -> None:
        said = _for_speech("It is about 02:53 AM IST on 24 September 2026, "
                           "and Spotify is backing 10,000 new artists.")
        self.assertIn("2:53", said)
        self.assertIn("10000", said)
        self.assertNotIn("02:", said)
        self.assertNotIn(", 000", said)


class NothingUnspeakableGetsThrough(unittest.TestCase):
    def test_no_control_characters(self) -> None:
        """A placeholder character was used here briefly and never restored, which would
        have sent a null byte to the voice."""
        for raw in ("It's 2:53 AM.", "10,000 users", "a:b", "1:2:3", ""):
            said = _for_speech(raw)
            self.assertFalse([c for c in said if ord(c) < 32 and c != "\n"], repr(said))

    def test_markdown_and_links_are_gone(self) -> None:
        said = _for_speech("**Done** — see https://example.com/a/b and `code`.")
        self.assertNotIn("*", said)
        self.assertNotIn("http", said)
        self.assertIn("a link", said)

    def test_it_always_ends_with_a_stop(self) -> None:
        self.assertTrue(_for_speech("no full stop here").endswith("."))


if __name__ == "__main__":
    unittest.main()
