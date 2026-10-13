"""The delivery rule: when Nova's voice laughs, smiles, softens or stresses a word.

A loose "use a tag where a person would" was used rarely and unevenly, so the rule has
structure, reaches every brain, and code catches the emotes models write anyway.
"""

import unittest
from pathlib import Path

from assistant import conversation
from assistant.agents.chat_api import GroqAgent
from assistant.agents.rules import session_instructions
from assistant.config import GroqAgentSettings
from assistant.persona import delivery


class TheRule(unittest.TestCase):
    def test_conversation_gets_the_full_table(self):
        talk = conversation.start("chat").instructions("Aakshant")
        for tag in ("[chuckles]", "[warmly]", "[emphasis]", "[excited]", "[gently]", "[thoughtful]"):
            self.assertIn(tag, talk)
        self.assertIn("Examples:", talk)

    def test_every_task_brain_gets_the_short_one(self):
        self.assertIn(delivery.TASK_GUIDE, session_instructions("Nova", Path("C:/elsewhere")))
        agent = GroqAgent(GroqAgentSettings(), Path("."), False, "Nova")
        self.assertIn(delivery.TASK_GUIDE, agent._system_prompt("what's the weather"))

    def test_the_short_one_stays_short(self):
        """It is resent on every free-model tool round, against a per-minute budget."""
        self.assertLess(len(delivery.TASK_GUIDE) // 4, 90)


class TheSafetyNet(unittest.TestCase):
    def test_emotes_become_tags(self):
        cases = {
            "*laughs* okay, fair point.": "[laughs] okay, fair point.",
            "Sure (smiles) I can do that.": "Sure [warmly] I can do that.",
            "Nice one 😂 really.": "Nice one [laughs] really.",
        }
        for said, spoken in cases.items():
            self.assertEqual(delivery.normalise(said), spoken)

    def test_a_trailing_laugh_moves_to_where_it_colours_something(self):
        self.assertEqual(delivery.normalise("Got it. That is brilliant haha"), "Got it. [laughs] That is brilliant")

    def test_other_emoji_go_and_real_tags_stay(self):
        self.assertEqual(delivery.normalise("Good night 🌙"), "Good night")
        self.assertEqual(delivery.normalise("[warmly] Morning."), "[warmly] Morning.")


if __name__ == "__main__":
    unittest.main()
