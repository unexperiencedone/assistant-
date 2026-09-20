"""Garbage speech is dropped, and only speech addressed to Nova is acted on."""

import time
import unittest
from types import SimpleNamespace

from assistant.agent_runner import AgentRunner
from assistant.audio.gate import Transcript, rejection
from assistant.controller import Controller
from assistant.events import EventBus
from assistant.intents import strip_wake_word
from assistant.planning import Plan
from test_dispatch import _Registry
from test_runner import _Speaker


class NoiseFilter(unittest.TestCase):
    def test_real_requests_pass(self):
        for text in ("Hey Nova, open Spotify", "play perfect on spotify", "what time is it?",
                     "Nova, plan a three hour lecture on theory of computation"):
            self.assertIsNone(rejection(Transcript(text, avg_logprob=-0.4, compression_ratio=1.3)), text)

    def test_video_outros_are_dropped(self):
        # All of these reached the agent before, from background audio.
        for text in ("We'll see you in the next video.", "and I will see you in a minute.",
                     "Thanks for watching!", "and we'll see you in a few more. Bye-bye.",
                     "Don't forget to like and subscribe"):
            self.assertIsNotNone(rejection(Transcript(text)), text)

    def test_repetition_is_dropped(self):
        self.assertEqual("one word repeated", rejection(Transcript("Okay. Okay. Okay. Okay. Good. Okay.")))
        self.assertEqual("sentences repeated", rejection(Transcript(
            "We are on a seat line. We are on a seat line. We are on a seat line.")))

    def test_whisper_scores_are_used(self):
        self.assertIn("low confidence", rejection(Transcript("keep on tingling", avg_logprob=-1.3)))
        self.assertEqual("looping transcript", rejection(Transcript("ya ya sub kul", compression_ratio=2.9)))
        self.assertEqual("filler", rejection(Transcript("Hmm.")))
        self.assertEqual("no words", rejection(Transcript("...")))


class WakeWord(unittest.TestCase):
    def test_must_open_the_sentence(self):
        self.assertEqual((True, "open spotify"), strip_wake_word("Hey Nova, open spotify", "nova"))
        self.assertEqual((True, "play music"), strip_wake_word("okay nova play music", "nova"))
        self.assertEqual((True, "status"), strip_wake_word("um, okay Nova. status", "nova"))
        self.assertFalse(strip_wake_word("I told nova to go away", "nova")[0])
        self.assertFalse(strip_wake_word("Supernova open", "nova")[0])
        self.assertFalse(strip_wake_word("November rain", "nova")[0])

    def test_common_mishearings_count(self):
        self.assertEqual((True, "open chrome"), strip_wake_word("OK, hey Noah open chrome", "nova"))
        self.assertEqual((True, "what time is it"), strip_wake_word("Novah what time is it", "nova"))
        self.assertEqual((True, "go"), strip_wake_word("Jarvis, go", "nova", aliases=["jarvis"]))

    def test_no_wake_word_configured_accepts_everything(self):
        self.assertEqual((True, "open spotify"), strip_wake_word("open spotify", ""))


class Addressing(unittest.TestCase):
    def setUp(self):
        self.said: list[str] = []
        self.bus, self.plan = EventBus(), Plan()
        speaker = _Speaker()
        speaker.say = self.said.append
        speaker.last_active_at = 0.0
        speaker.stop = lambda: None
        self.speaker = speaker
        self.runner = AgentRunner(self.bus, self.plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        self.settings = SimpleNamespace(assistant=SimpleNamespace(
            filler_after_seconds=30, wake_word="nova", wake_aliases=[], follow_up="questions", follow_up_seconds=8))
        self.controller = Controller(self.settings, self.bus, speaker, _Registry(0.05), self.plan, self.runner,
                                     request_quit=lambda: None, show_canvas=lambda: True)
        self.ignored: list[str] = []
        self.bus.subscribe("heard", lambda e: self.ignored.append(e.data["reason"]))

    def tearDown(self):
        self.runner.cancel(everything=True)

    def test_speech_without_wake_word_is_ignored(self):
        self.controller.handle("what time is it", "voice")
        self.assertEqual([], self.said)
        self.assertEqual(["no wake word"], self.ignored)

    def test_typed_text_never_needs_it(self):
        self.controller.handle("what time is it", "keyboard")
        self.assertTrue(self.said and self.said[0].startswith("It's"), self.said)
        self.controller.handle("hey nova, what time is it", "canvas")  # and the wake word is still removed
        self.assertTrue(self.said[-1].startswith("It's") and len(self.said) == 2, self.said)

    def test_bare_wake_word_opens_a_short_window(self):
        self.controller.handle("Hey Nova", "voice")
        self.assertEqual(["Yes?"], self.said)
        self.controller.handle("what time is it", "voice")
        self.assertTrue(self.said[-1].startswith("It's"), self.said)
        # The window closes once used: background talk right after isn't a command.
        self.controller.handle("what time is it", "voice")
        self.assertEqual(2, len(self.said))

    def test_window_expires(self):
        self.controller.handle("Nova", "voice")
        self.controller.awaiting_since = time.time() - 60
        self.controller.handle("what time is it", "voice")
        self.assertEqual(["Yes?"], self.said)

    def test_a_statement_does_not_open_the_window(self):
        self.controller.handle("nova what time is it", "voice")
        self.controller.handle("what time is it", "voice")
        self.assertEqual(1, len(self.said))

    def test_urgent_commands_need_addressing_too(self):
        self.assertFalse(self.controller.try_urgent("goodbye", "voice"))  # a TV saying goodbye must not quit
        self.assertTrue(self.controller.try_urgent("nova, stop", "voice"))


if __name__ == "__main__":
    unittest.main()
