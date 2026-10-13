"""Prompting another AI app by voice: the Claude desktop app and the Antigravity IDE.

"Open <folder> in antigravity and prompt it to <task>" needs two things automations
couldn't do -- find a folder by name, and start a program with arguments -- and the
review had to tell a value saved by an earlier step from a phrase slot it was missing.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.automation import review
from assistant.automation.macros import Macro, MacroRunner, load_macros
from assistant.events import EventBus
from assistant.intents import match_intent

AUTOMATIONS = Path(__file__).resolve().parent.parent / "automations"


class TheSavedAutomations(unittest.TestCase):
    def test_both_pass_their_own_review(self):
        for name in ("antigravity_prompt.toml", "prompt_claude_app.toml"):
            text = (AUTOMATIONS / name).read_text(encoding="utf-8")
            self.assertEqual(review.problems(text, match_intent), [], name)

    def test_the_phrases_pull_out_folder_and_prompt(self):
        macros = {m.path.name: m for m in load_macros(AUTOMATIONS)}
        agy = macros["antigravity_prompt.toml"].match("open school sample in antigravity and prompt it to add a footer")
        self.assertEqual(agy, {"folder": "school sample", "query": "add a footer"})
        claude = macros["prompt_claude_app.toml"].match("prompt claude to write a haiku")
        self.assertEqual(claude, {"query": "write a haiku"})

    def test_ask_claude_still_means_novas_own_agent(self):
        self.assertEqual(match_intent("ask claude to fix the bug").name, "delegate")


class TheOpenCommandLetsGo(unittest.TestCase):
    def test_open_and_prompt_is_not_opening_a_file(self):
        """The target came out as "claude and prompt it to write a poem"."""
        self.assertIsNone(match_intent("open claude and prompt it to write a poem"))
        self.assertIsNone(match_intent("open nova in antigravity and tell it to add tests"))
        self.assertEqual(match_intent("open spotify").name, "open_item")


class TheReview(unittest.TestCase):
    def test_a_saved_value_is_not_a_missing_slot(self):
        text = '''name = "x"
phrases = ["open {folder} in it"]
[[steps]]
do = "find"
query = "{folder}"
save_as = "path"
[[steps]]
do = "open"
program = "editor.exe"
args = ["-n", "{path}"]
[[steps]]
do = "wait_window"
title = "{path_name} - Editor"
'''
        self.assertEqual(review.problems(text), [])

    def test_a_slot_nobody_provides_is_still_caught_inside_args(self):
        text = '''name = "x"
phrases = ["open {folder} in it"]
[[steps]]
do = "open"
program = "editor.exe"
args = ["{nowhere}"]
'''
        self.assertTrue(any("nowhere" in p for p in review.problems(text)))

    def test_key_names_are_not_slots(self):
        text = '''name = "x"
phrases = ["send {query} now"]
[[steps]]
do = "keys"
window = "App"
keys = "{ENTER}"
'''
        self.assertEqual(review.problems(text), [])


class TheNewSteps(unittest.TestCase):
    def runner(self):
        return MacroRunner(EventBus(), open_app=lambda n: True, say=lambda t: None)

    def test_find_saves_the_path_and_its_name(self):
        runner = self.runner()
        values = {}
        with mock.patch.object(MacroRunner, "_find", return_value=r"C:\Users\me\OneDrive\school-sample"):
            runner._execute({"do": "find", "query": "school sample", "save_as": "path"}, values, None)
        self.assertEqual(values["path_name"], "school-sample")

    def test_a_folder_that_isnt_there_stops_the_run(self):
        runner = self.runner()
        with mock.patch.object(MacroRunner, "_find", return_value=None):
            with self.assertRaisesRegex(Exception, "couldn't find a folder called nowhere"):
                runner._execute({"do": "find", "query": "nowhere", "save_as": "path"}, {}, None)

    def test_a_program_gets_arguments_never_a_shell(self):
        runner = self.runner()
        with mock.patch("subprocess.Popen") as popen:
            runner._execute({"do": "open", "program": "editor.exe", "args": ["-n", "{path}"]},
                            {"path": r"C:\a & del everything"}, None)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["editor.exe", "-n", r"C:\a & del everything"])  # one argument, inert
        self.assertNotIn("shell", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
