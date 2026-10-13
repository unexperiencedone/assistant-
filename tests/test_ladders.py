"""Fallback ladders and the click fallback: a failed approach leads to the next one.

What is pinned here is what saves turns: the right ladder (and only it) reaches the
prompt, the click fallback runs in code without a model, nothing that could send or pay
is ever clicked twice, and what worked is remembered and offered back once there is
evidence for it.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from assistant.agent_runner import AgentRunner
from assistant.agents.base import AgentResult
from assistant.automation import extension
from assistant.controller import Controller
from assistant.events import EventBus
from assistant.ladders import LadderService
from assistant.planning import Plan
from test_runner import FakeBackend, _Speaker

LADDERS = Path(__file__).resolve().parent.parent / "ladders.toml"


def service(path=LADDERS, bus=None):
    memory = Path(tempfile.mkdtemp()) / "ladders.json"
    return LadderService(path, memory, bus, main="python main.py", cli="python sysindex.py")


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.ladders = service()

    def test_each_kind_of_request_gets_its_ladder(self):
        cases = {
            "click the whatsapp button on the vini website": "click",
            "find my theory of computation notes pdf": "find_file",
            "research the best laptops for students": "research",
            "fill the contact form": "fill_form",
            "write a blog post about our assistant": "write_doc",
            "send an email to ravi about the meeting": "send_mail",
            "plan a trip to goa": "plan",
            "message mom on whatsapp": "message",
        }
        for text, kind in cases.items():
            self.assertEqual(self.ladders.kinds_for(text)[0], kind, text)

    def test_small_talk_gets_nothing(self):
        self.assertEqual(self.ladders.hint("what time is it", "claude"), ("", []))

    def test_a_second_ladder_needs_more_than_one_word(self):
        self.assertEqual(self.ladders.kinds_for("open the sih report"), ["open"])

    def test_each_brain_gets_the_form_it_can_use(self):
        cli, _ = self.ladders.hint("fill the contact form", "claude")
        chat, _ = self.ladders.hint("fill the contact form", "groq")
        self.assertIn("python main.py web open <url>, then web form", cli)
        self.assertIn("browse_form", chat)
        self.assertNotIn("{main}", cli)


class TestLearning(unittest.TestCase):
    def test_the_rung_that_worked_is_offered_back_once_there_is_evidence(self):
        bus = EventBus()
        ladders = service(bus=bus)
        for n in range(2):
            task = f"t{n}"
            ladders.track(task, ["click"])
            bus.publish("agent", kind="tool", tool="Bash", text="python main.py web open https://web.whatsapp.com/", task=task)
            bus.publish("agent", kind="tool", tool="Bash", text="python main.py web click 7", task=task)
            bus.publish("agent", kind="tool", tool="Bash", text='python main.py ui click "WhatsApp" "Send"', task=task)
            self.assertEqual(ladders.advice("click", "whatsapp"), "" if n == 0 else ladders.advice("click", "whatsapp"))
            bus.publish("agent", kind="result", text="done", task=task)
        advice = ladders.advice("click", "click send on whatsapp")
        self.assertIn("'accessibility by name' worked 2 of 2", advice)
        self.assertIn("'web click' keeps failing", advice)
        self.assertIn(advice, ladders.hint("click send on whatsapp", "claude")[0])

    def test_a_failed_task_credits_nothing(self):
        ladders = service()
        ladders.learn(["click"], ["Bash python main.py web click 3"], ok=False)
        ladders.learn(["click"], ["Bash python main.py web click 3"], ok=False)
        self.assertEqual(ladders.advice("click"), "")

    def test_an_edited_file_is_picked_up(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "ladders.toml"
        path.write_text('[x]\nwhen = ["zap"]\n[[x.rungs]]\nname = "one"\ncli = "a"\n', encoding="utf-8")
        ladders = service(path)
        self.assertEqual(ladders.kinds_for("zap it"), ["x"])
        path.write_text('[y]\nwhen = ["zap"]\n[[y.rungs]]\nname = "one"\ncli = "a"\n', encoding="utf-8")
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(ladders.kinds_for("zap it"), ["y"])


class _Recording(FakeBackend):
    prompts: list = []

    def run(self, prompt, on_event, cancel):
        _Recording.prompts.append(prompt)
        return AgentResult(True, "ok")


class TestDispatch(unittest.TestCase):
    def test_the_ladder_goes_in_front_of_the_request(self):
        bus, plan, speaker = EventBus(), Plan(), _Speaker()
        runner = AgentRunner(bus, plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        registry = SimpleNamespace(current=_Recording(0), spawn_current=lambda: _Recording(0))
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova"))
        controller = Controller(settings, bus, speaker, registry, plan, runner, request_quit=lambda: None,
                                show_canvas=lambda: True, ladders=service(bus=bus))
        _Recording.prompts.clear()
        controller.dispatch("fill the contact form on the vini website")
        deadline = time.time() + 3
        while not _Recording.prompts and time.time() < deadline:
            time.sleep(0.02)
        self.assertIn("Ladder (fill_form)", _Recording.prompts[0])
        self.assertTrue(_Recording.prompts[0].endswith("fill the contact form on the vini website"))


class TestClickFallback(unittest.TestCase):
    """The rungs after Chrome: an app link through Windows, then UI Automation."""

    def test_an_app_link_opens_in_its_app(self):
        with mock.patch("os.startfile", create=True) as start:
            result = extension.finish_click({"external": "whatsapp://send?phone=91", "page": "[4] link \"Chat\""},
                                            changes=lambda: {})
        start.assert_called_once_with("whatsapp://send?phone=91")
        self.assertIn("whatsapp app", result["page"])

    def test_a_page_cannot_launch_any_handler_it_likes(self):
        with mock.patch("os.startfile", create=True) as start:
            result = extension.finish_click({"external": "search-ms:query=x", "page": ""}, changes=lambda: {})
        start.assert_not_called()
        self.assertIn("won't hand", result["page"])

    def test_an_ignored_click_is_pressed_through_windows_accessibility(self):
        with mock.patch("assistant.automation.desktop.click") as click:
            result = extension.finish_click(
                {"unchanged": True, "name": "Send", "tabTitle": "Vini - WhatsApp", "page": "Clicked. No visible change."},
                changes=lambda: {"page": "+ [12] dialog \"Open WhatsApp?\"", "full": False})
        click.assert_called_once_with("Vini - WhatsApp", "Send", timeout=3)
        self.assertEqual(result["method"], "uia")
        self.assertIn("Windows accessibility", result["page"])

    def test_a_sending_button_is_never_pressed_twice(self):
        with mock.patch("assistant.automation.desktop.click") as click:
            result = extension.finish_click({"unchanged": True, "sensitive": True, "name": "Send", "page": "x"},
                                            changes=lambda: {})
        click.assert_not_called()
        self.assertEqual(result["page"], "x")


if __name__ == "__main__":
    unittest.main()
