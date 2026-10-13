"""Talk modes: chatting and brainstorming like you would with a person.

What matters: while a mode is on, what you say is conversation and never an action; you
don't need the wake word for each sentence; the conversation runs on the free model with
no tools attached; leaving a brainstorm keeps its ideas.
"""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from assistant import conversation
from assistant.agent_runner import AgentRunner
from assistant.agents.base import AgentResult
from assistant.agents.chat_api import GroqAgent
from assistant.config import GroqAgentSettings
from assistant.controller import Controller
from assistant.events import EventBus
from assistant.intents import match_intent
from assistant.planning import Plan
from test_runner import FakeBackend, _Speaker


class FakeChat(FakeBackend):
    """A free-model stand-in with the talk switch, recording what each turn was given."""

    name, label = "groq", "Groq"

    def __init__(self, turns):
        super().__init__(0)
        self.talk = ""
        self.turns = turns

    def spawn(self):
        return FakeChat(self.turns)

    def run(self, prompt, on_event, cancel):
        self.turns.append((prompt, self.talk))
        return AgentResult(True, "[chuckles] Fair enough.")


class TheWords(unittest.TestCase):
    def test_starting_stopping_and_recapping(self):
        cases = {
            "let's chat": ("talk_mode_on", {}),
            "can we just talk for a bit": ("talk_mode_on", {}),
            "let's brainstorm about a name for my startup": ("brainstorm_on", {"topic": "a name for my startup"}),
            "brainstorm mode": ("brainstorm_on", {}),
            "back to work": ("talk_mode_off", {}),
            "what have we got so far": ("brainstorm_recap", {}),
            "wrap it up": ("brainstorm_recap", {}),
        }
        for said, (name, args) in cases.items():
            intent = match_intent(said)
            self.assertIsNotNone(intent, said)
            self.assertEqual((intent.name, intent.args), (name, args), said)

    def test_every_ordinary_way_of_asking_to_chat(self):
        """ "let's have a talk!" went to the task agent on the first night."""
        for said in ("let's have a talk!", "talk with me", "chat with me", "I just want to talk to you",
                     "can we talk?", "wanna chat?", "let's have a conversation"):
            intent = match_intent(said)
            self.assertEqual(intent and intent.name, "talk_mode_on", said)

    def test_ordinary_requests_are_not_modes(self):
        for said in ("let's talk to the client tomorrow", "talk to ravi about the invoice", "how's it going", "stop"):
            intent = match_intent(said)
            self.assertFalse(intent and intent.name in ("talk_mode_on", "talk_mode_off"), said)


class InAMode(unittest.TestCase):
    def setUp(self):
        self.turns = []
        self.said = []
        self.bus, plan = EventBus(), Plan()
        speaker = _Speaker()
        speaker.say = self.said.append
        speaker.last_active_at = 0.0
        self.runner = AgentRunner(self.bus, plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        groq = FakeChat(self.turns)
        registry = SimpleNamespace(current=groq, backends={"groq": groq}, spawn_current=groq.spawn)
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova",
                                                             follow_up="questions", follow_up_seconds=8))
        self.automations = mock.Mock()
        self.automations.match.return_value = None
        self.automations.macros = []
        self.controller = Controller(settings, self.bus, speaker, registry, plan, self.runner,
                                     request_quit=lambda: None, show_canvas=lambda: True,
                                     automations=self.automations)
        self.events = []
        self.bus.subscribe("talk_mode", lambda e: self.events.append(e.data))

    def wait_for_turns(self, n):
        deadline = time.time() + 3
        while len(self.turns) < n and time.time() < deadline:
            time.sleep(0.02)

    def test_a_name_is_talk_not_a_phone_call(self):
        self.controller.handle("let's chat", "keyboard")
        self.controller.handle("gulzar ali", "keyboard")
        self.wait_for_turns(1)
        prompt, talk = self.turns[0]
        self.assertEqual(prompt, "gulzar ali")
        self.assertIn("casual conversation", talk)
        self.assertNotIn("gulzar ali", [c.args[0] for c in self.automations.match.call_args_list])

    def test_handing_over_the_choice_gets_a_topic_not_a_question(self):
        self.controller.handle("talk to me about anything", "keyboard")
        self.wait_for_turns(1)
        self.assertEqual(self.turns[0][0], conversation.LEAD_PROMPT)
        self.assertEqual(self.controller.talk.topic, "")

    def test_no_wake_word_needed_after_a_reply(self):
        self.controller.handle("nova let's chat", "voice")
        addressed, _ = self.controller._addressed("i had the strangest day", "voice")
        self.assertTrue(addressed)

    def test_back_to_work_ends_it_and_restores_the_scripts(self):
        self.controller.handle("let's chat", "keyboard")
        self.controller.handle("back to work", "keyboard")
        self.assertIsNone(self.controller.talk)
        self.assertEqual(self.events[-1]["kind"], "")
        self.controller.handle("open spotify", "keyboard")
        self.assertIn("open spotify", [c.args[0] for c in self.automations.match.call_args_list])

    def test_a_brainstorm_knows_its_topic(self):
        self.controller.handle("let's brainstorm about a podcast name", "keyboard")
        self.assertEqual(self.events[-1]["label"], "Brainstorming: a podcast name")
        self.controller.handle("something about coffee", "keyboard")
        self.wait_for_turns(1)
        self.assertIn("brainstorming with", self.turns[0][1])
        self.assertIn("about a podcast name", self.turns[0][1])

    def test_the_recap_is_saved(self):
        self.controller.handle("let's brainstorm about a podcast name", "keyboard")
        folder = Path(tempfile.mkdtemp())
        self.controller._recaps["t42"] = ("a podcast name", True)
        with mock.patch.object(conversation, "recap_folder", return_value=folder):
            self.controller.on_agent_finished(FakeChat([]), AgentResult(True, "# Names\n- **Bean There** (strong)"), "t42")
        saved = list(folder.glob("*.md"))
        self.assertEqual(len(saved), 1)
        self.assertIn("Bean There", saved[0].read_text(encoding="utf-8"))
        self.assertIsNone(self.controller.talk)  # "wrap it up" ends the mode once it's kept


class TheFreeModelTalks(unittest.TestCase):
    def test_no_tools_and_the_modes_instructions(self):
        agent = GroqAgent(GroqAgentSettings(), Path("."), False, "Nova")
        agent.talk = conversation.start("chat").instructions("Aakshant")
        prompt = agent._system_prompt("i had the strangest day")
        self.assertEqual(agent._offered, [])
        self.assertIn("[chuckles]", prompt)
        self.assertNotIn("delegate_to_claude", prompt)


if __name__ == "__main__":
    unittest.main()
