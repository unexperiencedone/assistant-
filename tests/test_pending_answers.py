"""A question Nova asked must never eat the next thing you say.

"Want me to save that as an instant command?" followed by "play karan aujla" was being
read as a "no" — the request vanished and Nova answered the wrong question.
"""

import unittest
from types import SimpleNamespace

from assistant.agent_runner import AgentRunner
from assistant.controller import Controller, yes_or_no
from assistant.events import EventBus
from assistant.planning import Plan
from test_runner import FakeBackend, _Speaker


class _Registry:
    def __init__(self):
        self.current = FakeBackend(0.1)

    def spawn_current(self):
        return FakeBackend(0.1)


class TestWhatCountsAsAnAnswer(unittest.TestCase):
    def test_plain_agreement(self):
        for said in ("yes", "yeah", "sure", "ok", "go ahead", "do it", "send it", "save it"):
            self.assertIs(yes_or_no(said), True, said)

    def test_plain_refusal(self):
        for said in ("no", "nope", "nah", "not now", "skip", "cancel", "never mind", "no thanks"):
            self.assertIs(yes_or_no(said), False, said)

    def test_a_request_is_not_an_answer(self):
        for said in ("play karan aujla", "Lee, Karanojla song", "what's the time",
                     "open spotify", "news briefing", "status"):
            self.assertIsNone(yes_or_no(said), said)


class TestThePendingQuestionLapses(unittest.TestCase):
    def setUp(self):
        self.said: list[str] = []
        bus, plan = EventBus(), Plan()
        speaker = _Speaker()
        speaker.say = self.said.append
        self.dispatched: list[str] = []
        runner = AgentRunner(bus, plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova",
                                                             follow_up="off"),
                                   promotion=SimpleNamespace(enabled=True))
        self.controller = Controller(settings, bus, speaker, _Registry(), plan, runner,
                                     request_quit=lambda: None, show_canvas=lambda: True)
        # Typed input, the way the canvas and the phone send it: no wake word needed.
        # Stand in for the agent: record what would have been sent, run nothing.
        self.controller.dispatch = lambda text, **kwargs: self.dispatched.append(text) or "t1"

    def test_a_real_request_after_the_question_is_handled_as_a_request(self):
        self.controller._pending_promotion = ("play_song", "play a song")
        self.controller.handle("play karan aujla", "canvas", "desktop")
        self.assertEqual(self.dispatched, ["play karan aujla"], "the request went through")
        self.assertNotIn("No problem, I'll keep asking each time for now.", self.said)
        self.assertIsNone(self.controller._pending_promotion, "the question lapsed")

    def test_a_real_no_still_answers_it(self):
        self.controller._pending_promotion = ("play_song", "play a song")
        self.controller.handle("no thanks", "canvas", "desktop")
        self.assertEqual(self.dispatched, [], "nothing was dispatched")
        self.assertIn("No problem, I'll keep asking each time for now.", self.said)

    def test_a_real_yes_saves_it(self):
        self.controller._pending_promotion = ("play_song", "play a song")
        self.controller.handle("yes please do", "canvas", "desktop")
        self.assertIn("Okay, saving it.", self.said)

    def test_an_ambiguous_reply_never_sends_a_text_message(self):
        self.controller._pending_phone = ("sms_send", {"number": "+91 1", "text": "hi"}, "text +91 1")
        self.controller.handle("play karan aujla", "canvas", "desktop")
        self.assertIsNone(self.controller._pending_phone, "the confirmation was dropped, not assumed")
        self.assertEqual(self.dispatched, ["play karan aujla"])


if __name__ == "__main__":
    unittest.main()
