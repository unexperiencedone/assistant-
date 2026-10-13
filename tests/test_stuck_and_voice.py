"""The night Nova stayed "working" for 274 seconds, and the voice that kept changing.

1. Two jobs handed to one Claude session at once both waited on its single reply stream.
   Claude answered both in one reply, one job took it, and the other waited forever.
2. "keep it" -- the answer to "it won't run until you tell me to keep it" -- went to a
   model, which ended up grepping the code to approve the automation.
3. With no voice id, Fish picks a voice per request, and replies go a sentence at a time.
"""

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from assistant.agents.base import AgentResult
from assistant.agents.claude_cli import ClaudeCliAgent
from assistant.config import ClaudeAgentSettings, VoiceSettings
from assistant.intents import match_intent


class OneTurnPerSession(unittest.TestCase):
    def test_a_second_turn_waits_for_the_first(self):
        agent = ClaudeCliAgent(ClaudeAgentSettings(), Path("."), True, "")
        order = []

        def slow(prompt, on_event, cancel):
            order.append(f"start {prompt}")
            time.sleep(0.3)
            order.append(f"end {prompt}")
            return AgentResult(True, prompt)

        with mock.patch.object(agent, "_run_turn", side_effect=slow):
            first = threading.Thread(target=agent.run, args=("one", None, threading.Event()))
            first.start()
            time.sleep(0.05)
            self.assertTrue(agent.busy)
            agent.run("two", None, threading.Event())
            first.join()
        self.assertEqual(order, ["start one", "end one", "start two", "end two"])

    def test_waiting_can_still_be_cancelled(self):
        agent = ClaudeCliAgent(ClaudeAgentSettings(), Path("."), True, "")
        agent._turn.acquire()
        cancel = threading.Event()
        cancel.set()
        self.assertTrue(agent.run("two", None, cancel).cancelled)
        agent._turn.release()


class DelegatingToABusySession(unittest.TestCase):
    def test_it_gets_its_own_session_which_is_closed_after(self):
        from assistant.app import VoiceAssistant

        busy = mock.Mock(busy=True, label="Claude Code")
        fresh = mock.Mock(busy=False, label="Claude Code")
        fresh.run.return_value = AgentResult(True, "done")
        busy.spawn.return_value = fresh
        app = SimpleNamespace(registry=SimpleNamespace(backends={"claude": busy}), bus=mock.Mock(),
                              runner=SimpleNamespace(cancel_event=threading.Event()))
        self.assertEqual(VoiceAssistant._delegate(app, "claude", "close the tab", "missing"), "done")
        busy.run.assert_not_called()
        fresh.close.assert_called_once()


class KeepIt(unittest.TestCase):
    def test_keep_it_approves_the_proposal(self):
        for said in ("keep it", "yes keep it", "save that", "keep that automation", "approve this one"):
            intent = match_intent(said)
            self.assertEqual(intent and intent.name, "approve_automation", said)

    def test_keep_going_is_not_an_approval(self):
        intent = match_intent("keep going with the report")
        self.assertNotEqual(intent and intent.name, "approve_automation")


class OneVoice(unittest.TestCase):
    def test_every_request_names_the_voice_and_steadies_it(self):
        from assistant.audio import fish

        settings = VoiceSettings()
        settings.fish_voice = "be5f87d18c6e4319a672608cf1d92d08"
        speaker = fish.FishSpeaker.__new__(fish.FishSpeaker)
        speaker.settings = settings
        speaker._warn = lambda error: None
        sent = {}

        def capture(request, timeout):
            sent.update(json.loads(request.data))
            raise OSError("stop here")

        with mock.patch.object(fish.FishSpeaker, "api_key", "k", create=True), \
                mock.patch("urllib.request.urlopen", side_effect=capture):
            speaker._stream("[chuckles] Hello.", None)
        self.assertEqual(sent["reference_id"], settings.fish_voice)
        self.assertEqual(sent["temperature"], 0.6)


if __name__ == "__main__":
    unittest.main()
