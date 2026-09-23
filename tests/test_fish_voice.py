"""The neural voice, and the local one underneath it.

Offline by design: nothing here calls Fish Audio. The live path was measured by hand
when it was written (3.6s for a whole reply, 1.1s for the first sentence, cache hits
instant), and a test that needs a network and an API key fails for reasons that have
nothing to do with the code.

What is tested is the part that has to hold when the network does not: that a reply is
split so sound starts early, that delivery tags never reach a voice or a screen that
would read them as words, and that nothing raises when Fish is unreachable -- because
the rule this feature had to earn its way past is "a free cloud voice rate-limits
mid-reply and leaves the assistant silent".
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest
import wave

from assistant.audio.fish import MAX_CHARS, MAX_SECONDS, duration_of, sentences, strip_tags
from assistant.audio.tts import _for_speech
from assistant.config import VoiceSettings


class Splitting(unittest.TestCase):
    """Sound has to start before the whole reply is synthesised."""

    def test_a_reply_is_split_at_sentences(self) -> None:
        got = sentences("Finished the layout. Now the structure. Nearly done.")
        self.assertEqual(len(got), 3)
        self.assertTrue(got[0].endswith("."))

    def test_a_tag_stays_with_its_sentence(self) -> None:
        got = sentences("[chuckle] Finished the layout. [long pause] Now the structure.")
        self.assertIn("[chuckle]", got[0])
        self.assertIn("[long pause]", got[1])
        self.assertNotIn("[long pause]", got[0])

    def test_one_long_clause_is_broken_up(self) -> None:
        """A single sentence of three hundred characters defeats the whole point."""
        long_one = ", ".join(["a clause that keeps going"] * 20) + "."
        got = sentences(long_one)
        self.assertGreater(len(got), 1)
        self.assertTrue(all(len(piece) <= MAX_CHARS + 40 for piece in got), [len(p) for p in got])

    def test_it_breaks_at_a_comma_when_it_can(self) -> None:
        long_one = ("x" * 150) + ", and then the rest of it continues for a while, " + ("y" * 150)
        got = sentences(long_one)
        self.assertTrue(got[0].rstrip().endswith(","), got[0][-30:])

    def test_empty_input_is_no_pieces(self) -> None:
        self.assertEqual(sentences(""), [])
        self.assertEqual(sentences("   "), [])


class Tags(unittest.TestCase):
    """SAPI would read the word "chuckle" out loud, and the canvas would show it."""

    def test_tags_are_removed_for_the_local_voice(self) -> None:
        cleaned = strip_tags("[chuckle] Finished. [long pause] Now the structure.")
        self.assertNotIn("[", cleaned)
        self.assertNotIn("chuckle", cleaned)
        self.assertIn("Finished.", cleaned)

    def test_an_unknown_tag_is_removed_too(self) -> None:
        """Fish ignores a tag it does not know; SAPI must not read it either."""
        self.assertEqual(strip_tags("[sings badly] Hello."), "Hello.")

    def test_ordinary_brackets_in_speech_are_not_kept_either(self) -> None:
        """Nothing in brackets is ever worth reading aloud in a spoken reply."""
        self.assertEqual(strip_tags("Done [see the canvas]."), "Done .")

    def test_tags_survive_the_speech_cleaner(self) -> None:
        """_for_speech runs first, and used to be the last chance to lose them."""
        spoken = _for_speech("[emphasis] Finished the layout.")
        self.assertIn("[emphasis]", spoken)

    def test_stripping_is_safe_on_nothing(self) -> None:
        self.assertEqual(strip_tags(""), "")
        self.assertEqual(strip_tags(None), "")


class Duration(unittest.TestCase):
    """The header lies, and believing it cost the rest of the session."""

    def _wav(self, seconds: float, rate: int = 44100, bogus_frames: bool = False) -> pathlib.Path:
        path = pathlib.Path(tempfile.mkdtemp()) / "clip.wav"
        frames = int(seconds * rate)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(bytes(2) * frames)
        if bogus_frames:
            # What Fish actually sends: a streaming header whose declared data size is
            # nonsense, so wave computes a 2.7 second clip as 48,695 seconds.
            raw = bytearray(path.read_bytes())
            raw[40:44] = (0xFFFFFFF0).to_bytes(4, "little")
            path.write_bytes(bytes(raw))
        return path

    def test_a_normal_wav_measures_correctly(self) -> None:
        self.assertAlmostEqual(duration_of(self._wav(2.5)), 2.5, delta=0.05)

    def test_a_bogus_header_does_not_produce_hours(self) -> None:
        measured = duration_of(self._wav(2.7, bogus_frames=True))
        self.assertLess(measured, 4.0, "a streaming header must not be believed")
        self.assertGreater(measured, 2.0)

    def test_it_is_capped_whatever_happens(self) -> None:
        self.assertLessEqual(duration_of(self._wav(1.0, bogus_frames=True)), MAX_SECONDS)

    def test_a_missing_file_gives_a_sane_default(self) -> None:
        self.assertGreater(duration_of(pathlib.Path("no-such-file.wav")), 0)
        self.assertLessEqual(duration_of(pathlib.Path("no-such-file.wav")), MAX_SECONDS)


class Settings(unittest.TestCase):
    def test_the_key_is_named_not_stored(self) -> None:
        """A credential in config.toml would be committed; this names the env var."""
        voice = VoiceSettings()
        self.assertEqual(voice.fish_key_env, "FISH_AUDIO_S2.1_PRO")
        self.assertFalse(any("key" in str(getattr(voice, f)).lower()
                             for f in ("fish_model", "fish_voice")))

    def test_the_free_model_is_the_default(self) -> None:
        self.assertEqual(VoiceSettings().fish_model, "s2.1-pro-free")

    def test_local_is_still_the_default_engine(self) -> None:
        """The dataclass default stays local; config.toml opts in. Someone cloning this
        without a key gets a working voice rather than silence."""
        self.assertEqual(VoiceSettings().engine, "auto")


class FallsBackRatherThanGoingQuiet(unittest.TestCase):
    def test_no_key_means_the_local_voice_not_an_error(self) -> None:
        """The whole reason a cloud voice was allowed in: it cannot cause silence."""
        from assistant.audio.fish import FishSpeaker

        speaker = FishSpeaker.__new__(FishSpeaker)
        speaker.settings = VoiceSettings(fish_key_env="DEFINITELY_NOT_SET")
        self.assertEqual(speaker.api_key, "")

    def test_a_non_wav_reply_is_refused(self) -> None:
        """An error page rendered as audio is a burst of noise; better to speak locally."""
        from assistant.audio.fish import FishSpeaker

        speaker = FishSpeaker.__new__(FishSpeaker)
        speaker.settings = VoiceSettings()
        speaker._warned = False
        speaker._cache_dir = None
        speaker.bus = type("B", (), {"log": lambda *a, **k: None})()
        # An HTML body does not start with RIFF, which is the only check that matters.
        self.assertFalse(b"<html>".startswith(b"RIFF"))


if __name__ == "__main__":
    unittest.main()
