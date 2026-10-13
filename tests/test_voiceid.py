"""Phase 1 of voice recognition: only the owner's voice speaks to Nova.

The rules pinned here: nothing is checked until a voiceprint exists; enrolment needs
whole sentences and sets its own bar from them, within bounds; a voice below the bar is
dropped with its score, a short one gets a little leniency so "stop" still works; a
model failure lets the request through rather than losing it; every score is logged.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from assistant.audio import voiceid
from assistant.audio.voiceid import VoiceID, fbank

MODEL = Path(__file__).resolve().parent.parent / "data" / "models" / "wespeaker_en_voxceleb_resnet34.onnx"
SECOND = 16000 * 2  # bytes of 16 kHz 16-bit audio


def unit(*values):
    v = np.zeros(256, dtype=np.float32)
    v[: len(values)] = values
    return v / np.linalg.norm(v)


OWNER = unit(1, 0.1)
OWNER_VARIANTS = [unit(1, 0.1 + d) for d in (0.0, 0.05, -0.05, 0.1)]
STRANGER = unit(0.2, 1)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.voice = VoiceID(self.dir / "model.onnx", self.dir / "voiceprint.json", self.dir / "scores.jsonl")
        self.said = []
        self.voice.on_enrolled = self.said.append

    def enrol(self):
        self.voice.start_enrolment()
        for vec in OWNER_VARIANTS:
            self.voice.hear(b"\0" * 2 * SECOND, vec=vec)


class Enrolment(Fixture):
    def test_nothing_is_checked_before_a_voiceprint_exists(self):
        self.assertEqual(self.voice.hear(b"\0" * SECOND).kind, "off")
        self.assertFalse(self.voice.wanted)

    def test_four_whole_sentences_make_a_voiceprint(self):
        self.enrol()
        self.assertTrue(self.voice.enrolled)
        saved = json.loads((self.dir / "voiceprint.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["samples"], 4)
        self.assertTrue(voiceid.THRESHOLD_BOUNDS[0] <= saved["threshold"] <= voiceid.THRESHOLD_BOUNDS[1])
        self.assertIn("I know your voice now", self.said[0])

    def test_a_fragment_is_not_a_sample(self):
        self.voice.start_enrolment()
        verdict = self.voice.hear(b"\0" * SECOND, vec=OWNER)
        self.assertEqual(verdict.kind, "enrol")
        self.assertIn("too short", verdict.reason)
        self.assertFalse(self.voice.enrolled)

    def test_forgetting_deletes_it(self):
        self.enrol()
        self.voice.forget()
        self.assertFalse(self.voice.enrolled)
        self.assertFalse((self.dir / "voiceprint.json").exists())


class Checking(Fixture):
    def setUp(self):
        super().setUp()
        self.enrol()

    def test_the_owner_is_heard_and_a_stranger_is_not(self):
        self.assertEqual(self.voice.hear(b"\0" * 2 * SECOND, vec=OWNER).kind, "accept")
        verdict = self.voice.hear(b"\0" * 2 * SECOND, vec=STRANGER)
        self.assertEqual(verdict.kind, "reject")
        self.assertIn("not your voice", verdict.reason)

    def test_a_short_phrase_gets_a_little_leniency(self):
        # A voice scoring just under the normal bar but inside the short-phrase leniency.
        borderline = self.voice.threshold - voiceid.SHORT_LENIENCY / 2
        owner_axis = np.asarray(self.voice.profile["embedding"], dtype=np.float32)
        vec = borderline * owner_axis + np.sqrt(1 - borderline ** 2) * unit(0, 0, 1)
        self.assertEqual(self.voice.hear(b"\0" * int(0.6 * SECOND), vec=vec).kind, "accept")
        self.assertEqual(self.voice.hear(b"\0" * 2 * SECOND, vec=vec).kind, "reject")

    def test_log_only_mode_drops_nothing_but_still_scores(self):
        self.voice.enforce = False
        self.assertEqual(self.voice.hear(b"\0" * 2 * SECOND, vec=STRANGER).kind, "accept")
        rows = [json.loads(l) for l in (self.dir / "scores.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse(rows[-1]["matched"])

    def test_a_model_failure_never_loses_a_request(self):
        with mock.patch.object(VoiceID, "embed", side_effect=RuntimeError("onnx broke")):
            self.assertEqual(self.voice.hear(b"\0" * 2 * SECOND).kind, "accept")

    def test_the_report_is_the_evidence_for_phase_two(self):
        self.voice.hear(b"\0" * 2 * SECOND, vec=OWNER, text="open spotify")
        self.voice.hear(b"\0" * 2 * SECOND, vec=STRANGER, text="tv chatter")
        report = self.voice.report()
        self.assertIn("2 utterances checked: 1 matched, 1 dropped", report)
        self.assertIn("tv chatter", report)


class TheFirstNight(Fixture):
    """The owner was turned away at 0.57: the bar was at its 0.62 cap, and the clip was
    12 seconds of music around one second of "play the song"."""

    def test_a_very_consistent_enrolment_no_longer_sets_a_bar_too_high(self):
        self.voice.profile = {"embedding": OWNER.tolist(), "consistency": 0.853, "threshold": 0.62}
        self.assertEqual(self.voice.threshold, voiceid.THRESHOLD_BOUNDS[1])
        self.assertLess(self.voice.threshold, 0.57)

    def test_a_clip_that_is_mostly_not_speech_is_cut_to_the_speech(self):
        twelve_seconds = b"\1\0" * (16000 * 12)
        cropped = voiceid.speech_only(twelve_seconds, ((3.0, 4.2),))
        self.assertAlmostEqual(len(cropped) / SECOND, 1.2 + 2 * voiceid.SPAN_PAD, places=2)

    def test_a_clip_that_is_mostly_speech_is_left_alone(self):
        self.assertIsNone(voiceid.speech_only(b"\0" * (2 * SECOND), ((0.1, 1.9),)))


class Features(unittest.TestCase):
    def test_shape_and_mean_normalised(self):
        tone = (np.sin(np.arange(16000) * 2 * np.pi * 220 / 16000) * 8000).astype(np.float32)
        feats = fbank(tone)
        self.assertEqual(feats.shape, (98, 80))
        self.assertLess(abs(float(feats.mean())), 1e-3)

    @unittest.skipUnless(MODEL.exists(), "speaker model not downloaded")
    def test_the_model_gives_a_unit_voiceprint(self):
        voice = VoiceID(MODEL, Path(tempfile.mkdtemp()) / "p.json", Path(tempfile.mkdtemp()) / "s.jsonl")
        noise = (np.random.default_rng(0).standard_normal(32000) * 3000).astype(np.int16).tobytes()
        vec = voice.embed(noise)
        self.assertEqual(vec.shape, (256,))
        self.assertAlmostEqual(float(np.linalg.norm(vec)), 1.0, places=4)


class TheMicrophone(unittest.TestCase):
    """The microphone hands the voiceprint, computed beside Whisper, to the check."""

    def test_a_stranger_is_turned_into_a_reason(self):
        import speech_recognition as sr

        from assistant.audio.microphone import MicrophoneListener

        mic = MicrophoneListener.__new__(MicrophoneListener)
        mic.voice = mock.Mock(wanted=True)
        mic.voice.hear.return_value = voiceid.Verdict("reject", 0.2, "not your voice (0.20 < 0.50)")
        mic.bus = mock.Mock()
        mic._voice_job = None
        mic._last_audio = sr.AudioData(b"\0" * SECOND, 16000, 2)
        from assistant.audio.gate import Transcript

        verdict = mic._voice_verdict(Transcript("open spotify", spans=((0.0, 0.9),)))
        self.assertEqual(verdict.kind, "reject")
        mic.voice.hear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
