"""Nova over another app: the answer, the choices, and the screen context used once."""

import unittest

from assistant.controller import CHOICES
from assistant.events import EventBus
from assistant.screen_context import describe
from assistant.state import AppState


class OverlayState(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.state = AppState(self.bus, "Nova", "groq", ".")

    def overlay(self):
        return self.state.snapshot()["overlay"]

    def test_the_next_thing_nova_says_after_a_question_is_the_answer(self):
        self.bus.publish("overlay", open=True, rev=1, context={"process": "Code.exe"}, question="", answer="", choices=[])
        self.bus.publish("transcript", role="assistant", text="before the question")
        self.assertEqual(self.overlay()["answer"], "")
        self.bus.publish("overlay", question="why so slow?", answer="", choices=[])
        self.bus.publish("transcript", role="assistant", text="Cloud files.")
        self.bus.publish("transcript", role="assistant", text="and something later")
        self.assertEqual(self.overlay()["answer"], "Cloud files.")

    def test_choices_land_only_while_open_and_at_most_three(self):
        self.bus.publish("overlay_choices", choices=["a", "b"])
        self.assertEqual(self.overlay()["choices"], [])
        self.bus.publish("overlay", open=True, rev=2)
        self.bus.publish("overlay_choices", choices=["a", "b", "c", "d"])
        self.assertEqual(self.overlay()["choices"], ["a", "b", "c"])

    def test_closing_keeps_nothing_open(self):
        self.bus.publish("overlay", open=True, rev=3)
        self.bus.publish("overlay", open=False)
        self.assertFalse(self.overlay()["open"])


class Choices(unittest.TestCase):
    def test_the_tag_is_found_and_removed(self):
        reply = "Skip cloud files.\n[[CHOICES: Apply the fix | Show me the diff | Send to Work]]"
        match = CHOICES.search(reply)
        self.assertEqual([c.strip() for c in match.group(1).split("|")], ["Apply the fix", "Show me the diff", "Send to Work"])
        self.assertEqual(CHOICES.sub("", reply).strip(), "Skip cloud files.")


class Context(unittest.TestCase):
    def test_a_private_window_says_so_and_carries_no_text(self):
        line = describe({"process": "Bitwarden.exe", "title": "Vault", "private": True, "selection": "", "excerpt": ""})
        self.assertIn("nothing was read", line)
        self.assertNotIn("'''", line)

    def test_the_selection_is_quoted_and_choices_are_asked_for(self):
        line = describe({"process": "Code.exe", "title": "projects.py", "selection": "def _walk_files", "private": False})
        self.assertIn("def _walk_files", line)
        self.assertIn("[[CHOICES:", line)


if __name__ == "__main__":
    unittest.main()
