"""The neural voice, and the local one underneath it.

Offline by design: nothing here calls Fish Audio. A test that needs a network and an
API key fails for reasons that have nothing to do with the code.

What is left after the speaker moved to streaming raw PCM is the part that still has
logic in it: that delivery tags never reach a voice or a screen that would read them as
words, that a PCM clip can be given a correct WAV header when one is needed, and that
an absent key means the local voice rather than an exception -- because the rule this
feature had to earn its way past is "a free cloud voice rate-limits mid-reply and
leaves the assistant silent".

Gone with the rewrite: the sentence splitter, because one streamed request starts sound
sooner than several requests did, and the duration workaround, because raw PCM has no
header to lie about its length.
"""

from __future__ import annotations

import pathlib
import struct
import tempfile
import unittest
import wave

from assistant.audio.fish import RATE, strip_tags, wav_header
from assistant.audio.tts import _for_speech
from assistant.config import VoiceSettings


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


class WavHeader(unittest.TestCase):
    """PCM has no header, so one is written when a clip must become a real file.

    The API's own WAV header declares a 446 KB clip as 4,294,967,040 bytes, which
    Windows refuses to play at all -- the reason the speaker streams PCM instead.
    """

    def test_the_sizes_are_the_real_ones(self) -> None:
        header = wav_header(1000)
        self.assertEqual(struct.unpack("<I", header[4:8])[0], 1036)
        self.assertEqual(struct.unpack("<I", header[40:44])[0], 1000)

    def test_it_is_a_riff_wave(self) -> None:
        header = wav_header(0)
        self.assertTrue(header.startswith(b"RIFF"))
        self.assertEqual(header[8:12], b"WAVE")

    def test_the_rate_matches_what_the_api_sends(self) -> None:
        self.assertEqual(struct.unpack("<I", wav_header(0)[24:28])[0], RATE)

    def test_wave_can_open_what_it_writes(self) -> None:
        """The point of writing our own: unlike the API's, this file plays anywhere."""
        path = pathlib.Path(tempfile.mkdtemp()) / "clip.wav"
        audio = bytes(RATE * 2)          # one second of silence
        path.write_bytes(wav_header(len(audio)) + audio)
        with wave.open(str(path)) as handle:
            self.assertEqual(handle.getframerate(), RATE)
            self.assertAlmostEqual(handle.getnframes() / RATE, 1.0, delta=0.01)


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

    def test_which_voice_answered_is_announced_every_time(self) -> None:
        """It was logged once per run, so every later fallback was invisible and "is
        this Fish or SAPI" could not be answered from the outside."""
        from assistant.audio.fish import FishSpeaker

        said: list[dict] = []
        speaker = FishSpeaker.__new__(FishSpeaker)
        speaker.bus = type("B", (), {"publish": staticmethod(
            lambda topic, **data: said.append({"topic": topic, **data}))})()
        speaker._spoke("local", "no key")
        speaker._spoke("fish", "cached")
        self.assertEqual([entry["engine"] for entry in said], ["local", "fish"])
        self.assertEqual({entry["topic"] for entry in said}, {"voice"})


if __name__ == "__main__":
    unittest.main()
