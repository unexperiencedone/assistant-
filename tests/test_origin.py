"""Where a request came from, and where its answer goes.

A request made on the phone has to be answered on the phone — including minutes later,
when the agent turn it started finally finishes, and including when the desktop has
been used for something else in between.
"""

import unittest
from types import SimpleNamespace

from assistant.agent_runner import AgentRunner
from assistant.controller import Controller
from assistant.events import EventBus
from assistant.planning import Plan
from assistant.ui.server import origin_of
from test_runner import FakeBackend, _Speaker


class _Registry:
    def __init__(self):
        self.current = FakeBackend(0.1)

    def spawn_current(self):
        return FakeBackend(0.1)


class TestWhereARequestCameFrom(unittest.TestCase):
    def test_this_machine_is_the_desktop(self):
        self.assertEqual(origin_of("127.0.0.1"), "desktop")
        self.assertEqual(origin_of("::1"), "desktop")
        self.assertEqual(origin_of(None), "desktop")

    def test_anywhere_else_is_remote(self):
        self.assertEqual(origin_of("100.88.150.39"), "phone")   # over Tailscale
        self.assertEqual(origin_of("192.168.1.14"), "phone")    # over the LAN

    def test_the_client_can_say_so_itself(self):
        self.assertEqual(origin_of("127.0.0.1", "phone"), "phone")
        self.assertEqual(origin_of("100.88.150.39", "desktop"), "desktop")

    def test_a_claim_that_means_nothing_falls_back_to_the_address(self):
        self.assertEqual(origin_of("100.88.150.39", "kitchen-toaster"), "phone")
        self.assertEqual(origin_of("127.0.0.1", ""), "desktop")


class TestTheReplyGoesBack(unittest.TestCase):
    def setUp(self):
        self.spoken: list[str] = []
        self.bus, self.plan = EventBus(), Plan()
        speaker = _Speaker()
        speaker.say = self.spoken.append
        self.transcripts: list[dict] = []
        self.bus.subscribe("transcript", lambda event: self.transcripts.append(event.data))
        runner = AgentRunner(self.bus, self.plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova",
                                                             follow_up="questions"))
        self.controller = Controller(settings, self.bus, speaker, _Registry(), self.plan, runner,
                                     request_quit=lambda: None, show_canvas=lambda: True)

    def test_a_desktop_request_is_spoken_aloud(self):
        self.controller.reply_to = "desktop"
        self.controller.say("Playing Midnight City.")
        self.assertEqual(self.spoken, ["Playing Midnight City."])
        self.assertEqual(self.transcripts[-1]["origin"], "desktop")

    def test_a_phone_request_does_not_talk_to_an_empty_room(self):
        self.controller.reply_to = "phone"
        self.controller.say("Playing Midnight City.")
        self.assertEqual(self.spoken, [], "the PC speaker stays quiet for a phone request")
        self.assertEqual(self.transcripts[-1]["origin"], "phone",
                         "the reply is marked so the phone reads it out instead")

    def test_a_phone_question_does_not_open_the_desktop_microphone(self):
        self.controller.reply_to = "phone"
        self.controller.awaiting_reply = False
        self.controller.say("Which playlist?")
        self.assertFalse(self.controller.awaiting_reply)

    def test_a_desktop_question_still_does(self):
        self.controller.reply_to = "desktop"
        self.controller.awaiting_reply = False
        self.controller.say("Which playlist?")
        self.assertTrue(self.controller.awaiting_reply)

    def test_a_task_is_answered_where_it_was_asked_however_late(self):
        # Asked on the phone...
        self.controller.reply_to = "phone"
        self.controller.task_origins["t1"] = self.controller.reply_to
        self.controller.task_labels["t1"] = "play a song"
        # ...then the desktop is used for something else while it runs...
        self.controller.reply_to = "desktop"
        # ...and the phone's turn finally comes back.
        self.controller.reply_to = self.controller.task_origins.pop("t1", "desktop")
        self.controller.say("Done.")
        self.assertEqual(self.spoken, [])
        self.assertEqual(self.transcripts[-1]["origin"], "phone")


if __name__ == "__main__":
    unittest.main()
