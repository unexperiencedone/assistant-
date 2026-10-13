"""One judgement per turn instead of talk modes: act, or talk."""

import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from assistant import understanding
from assistant.understanding import ACT, TALK, Judgement, by_rules, judge


class Rules(unittest.TestCase):
    def test_commands_act(self):
        for said in ("open spotify", "ok play some lofi", "can you schedule standup at 10", "nova, send the report to ravi",
                     "what's the weather tomorrow", "how much is a pixel 9", "write a cover letter for the internship"):
            self.assertEqual(by_rules(said, conversing=False).kind, ACT, said)

    def test_conversation_is_talk(self):
        for said in ("hey", "thanks!", "haha", "really?", "how are you", "what do you think about vim",
                     "i'm so tired today", "that's wild", "tell me a joke"):
            self.assertEqual(by_rules(said, conversing=False).kind, TALK, said)

    def test_a_fragment_mid_conversation_is_a_reply_not_an_order(self):
        self.assertEqual(by_rules("gulzar ali", conversing=True).kind, TALK)
        self.assertIsNone(by_rules("gulzar ali", conversing=False))   # unclear: the model decides

    def test_a_brainstorm_is_talk_with_a_style(self):
        j = by_rules("let's brainstorm about a podcast name", conversing=False)
        self.assertEqual((j.kind, j.style, j.topic), (TALK, "brainstorm", "a podcast name"))


class Deciding(unittest.TestCase):
    def test_the_model_decides_only_what_the_rules_cannot(self):
        asked = []
        model = lambda text, recent: asked.append(text) or Judgement(TALK, "model", confidence=0.9)
        self.assertEqual(judge("open spotify", [], False, model).source, "rule")
        self.assertEqual(asked, [])
        self.assertEqual(judge("the thing from yesterday about the venue", [], False, model).kind, TALK)
        self.assertEqual(asked, ["the thing from yesterday about the venue"])

    def test_without_an_answer_it_leans_on_what_was_happening(self):
        failing = lambda text, recent: None
        self.assertEqual(judge("the thing from yesterday about the venue", [], True, failing).kind, TALK)
        self.assertEqual(judge("the thing from yesterday about the venue", [], False, failing).kind, ACT)

    def test_a_model_that_raises_never_breaks_a_turn(self):
        def broken(text, recent):
            raise RuntimeError("network")
        self.assertEqual(judge("the thing from yesterday about the venue", [], False, broken).source, "lean")


class Continuity(unittest.TestCase):
    def test_a_request_out_of_a_conversation_carries_it(self):
        from assistant.agent_runner import AgentRunner
        from assistant.controller import Controller
        from assistant.events import EventBus
        from assistant.planning import Plan

        prompts = []

        class Agent:
            name, label = "groq", "Groq"
            def is_available(self): return True
            def spawn(self): return self
            def carry(self, recent): pass

        bus, plan = EventBus(), Plan()
        speaker = SimpleNamespace(say=lambda *a, **k: None, is_busy=False, last_active_at=0.0)
        runner = AgentRunner(bus, plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        runner.start = lambda session, prompt, *a, **k: prompts.append(prompt) or f"t{len(prompts)}"
        agent = Agent()
        registry = SimpleNamespace(current=agent, current_name="groq", backends={"groq": agent})
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova",
                                                             follow_up="questions", follow_up_seconds=8))
        automations = mock.Mock(); automations.match.return_value = None; automations.macros = []
        controller = Controller(settings, bus, speaker, registry, plan, runner, request_quit=lambda: None,
                                show_canvas=lambda: True, automations=automations)
        controller._recent.extend([("You", "the venue fell through for saturday"), ("Nova", "Ugh. Want a backup?")])
        controller._talked_since_dispatch = True
        controller._last_talk_at = time.time()
        controller.dispatch("email the group about it", command="email the group about it")
        self.assertIn("The conversation this request came out of", prompts[-1])
        self.assertIn("venue fell through", prompts[-1])
        controller.dispatch("what time is it in tokyo", command="x")
        self.assertNotIn("The conversation this request came out of", prompts[-1])   # once, not forever


class NoWakeWord(unittest.TestCase):
    """With voice ID enrolled and enforcing, the owner's voice is enough."""

    def make(self, trusted=True):
        from assistant.agent_runner import AgentRunner
        from assistant.controller import Controller
        from assistant.events import EventBus
        from assistant.planning import Plan

        bus, plan = EventBus(), Plan()
        self.heard, self.shown = [], []
        bus.subscribe("heard", lambda e: self.heard.append(e.data))
        bus.subscribe("transcript", lambda e: self.shown.append(e.data["text"]))
        speaker = SimpleNamespace(say=lambda *a, **k: None, is_busy=False, last_active_at=0.0)
        runner = AgentRunner(bus, plan, speaker, on_finished=lambda *a: None, max_parallel=2)
        runner.start = lambda *a, **k: "t1"
        agent = SimpleNamespace(name="groq", label="Groq", is_available=lambda: True, spawn=lambda: agent,
                                carry=lambda r: None, talk="")
        registry = SimpleNamespace(current=agent, current_name="groq", backends={"groq": agent})
        settings = SimpleNamespace(assistant=SimpleNamespace(filler_after_seconds=30, wake_word="nova", follow_up="questions",
                                                             follow_up_seconds=8, wake_word_optional=True))
        self.automations = mock.Mock(); self.automations.match.return_value = None; self.automations.macros = []
        voice = SimpleNamespace(enrolled=True, enforce=True) if trusted else None
        return Controller(settings, bus, speaker, registry, plan, runner, request_quit=lambda: None,
                          show_canvas=lambda: True, automations=self.automations, voice_id=voice)

    def test_a_request_needs_no_wake_word(self):
        c = self.make()
        c.handle("open spotify", "voice")
        self.assertIn("open spotify", self.shown)

    def test_side_chatter_is_ignored_and_never_shown(self):
        c = self.make()
        c.handle("yeah i'll call you back after dinner", "voice")
        self.assertEqual(self.shown, [])
        self.assertEqual(self.heard[-1]["reason"], "not for me")

    def test_a_conversation_carries_on_without_it(self):
        c = self.make()
        c._last_talk_at = time.time()
        c.handle("the overlay thing mostly", "voice")
        self.assertIn("the overlay thing mostly", self.shown)

    def test_without_an_enforcing_voiceprint_the_wake_word_is_still_needed(self):
        c = self.make(trusted=False)
        c.handle("open spotify", "voice")
        self.assertEqual(self.shown, [])
        self.assertEqual(self.heard[-1]["reason"], "no wake word")


if __name__ == "__main__":
    unittest.main()
