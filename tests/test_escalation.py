"""When the fast tier can't finish, the job moves up a tier.

The cascade is only honest if its bottom failing means the work rises, not that the
work stops. Before this existed, running out of tool rounds ended the turn by asking
the user to say "use claude" -- making them do the routing, which is the one decision
they should never have to make.
"""

from __future__ import annotations

import os
import threading
import unittest

from assistant.agents.chat_api import ChatApiError, GroqAgent
from assistant.agents.tools import ToolContext
from assistant.config import GroqAgentSettings


def setUpModule() -> None:
    """The real key lives in .env, which only load_settings reads. Every test here
    stubs the HTTP call anyway, so any non-empty key gets past the availability check."""
    os.environ.setdefault("GROQ_API_KEY", "stub-key-for-tests")


def agent(delegate=None, **overrides) -> GroqAgent:
    settings = GroqAgentSettings(max_tool_calls=2, **overrides)
    made = GroqAgent(settings, workspace=None, continue_session=False, assistant_name="Nova")
    made.context = ToolContext(delegate=delegate)
    return made


class OutOfSteps(unittest.TestCase):
    """The commonest "too complex for the fast tier" signal there is."""

    def setUp(self) -> None:
        self.calls: list[str] = []

    def _endless_tool_calls(self, *_args, **_kwargs) -> dict:
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "list_windows", "arguments": "{}"}}]}

    def test_hands_over_to_claude(self) -> None:
        def delegate(task: str) -> str:
            self.calls.append(task)
            return "Claude finished it."

        fast = agent(delegate=delegate)
        fast._chat = self._endless_tool_calls
        result = fast.run("do something hard", lambda e: None, threading.Event())
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "Claude finished it.")
        self.assertEqual(result.data.get("escalated"), "it used all 2 of its steps without finishing")

    def test_claude_is_told_what_was_already_tried(self) -> None:
        """Otherwise the expensive tier repeats the cheap tier's dead work."""
        def delegate(task: str) -> str:
            self.calls.append(task)
            return "done"

        fast = agent(delegate=delegate)
        fast._chat = self._endless_tool_calls
        fast.run("do something hard", lambda e: None, threading.Event())
        self.assertIn("do something hard", self.calls[0])
        self.assertIn("list_windows", self.calls[0])

    def test_the_handover_is_visible(self) -> None:
        """Reported as a tool call, so the canvas shows it and the recipe store records
        that this kind of request needed Claude."""
        events: list[tuple[str, str]] = []
        fast = agent(delegate=lambda task: "done")
        fast._chat = self._endless_tool_calls
        fast.run("do something hard", lambda e: events.append((e.kind, e.tool)), threading.Event())
        self.assertIn(("tool", "delegate_to_claude"), events)

    def test_without_claude_it_says_so_honestly(self) -> None:
        fast = agent(delegate=None)
        fast._chat = self._endless_tool_calls
        result = fast.run("do something hard", lambda e: None, threading.Event())
        self.assertIn("without finishing", result.summary)

    def test_the_switch_turns_it_off(self) -> None:
        fast = agent(delegate=lambda task: "Claude finished it.", escalate_to_claude=False)
        fast._chat = self._endless_tool_calls
        result = fast.run("do something hard", lambda e: None, threading.Event())
        self.assertNotIn("Claude finished it.", result.summary)


class ProviderUnreachable(unittest.TestCase):
    """Groq being rate-limited is not a reason for the user to get nothing."""

    def test_hands_over_when_the_api_fails(self) -> None:
        def failing(*_args, **_kwargs):
            raise ChatApiError("Groq is rate-limited.")

        fast = agent(delegate=lambda task: "Claude answered instead.")
        fast._chat = failing
        result = fast.run("what changed in my repo", lambda e: None, threading.Event())
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "Claude answered instead.")

    def test_reports_the_real_reason_when_claude_also_fails(self) -> None:
        """A failed handover must not replace the real reason with a vaguer one."""
        def failing(*_args, **_kwargs):
            raise ChatApiError("Groq is rate-limited.")

        def broken_delegate(_task: str) -> str:
            raise RuntimeError("claude is not installed")

        fast = agent(delegate=broken_delegate)
        fast._chat = failing
        result = fast.run("anything", lambda e: None, threading.Event())
        self.assertFalse(result.ok)
        self.assertIn("rate-limited", result.summary)

    def test_an_empty_claude_reply_is_not_treated_as_success(self) -> None:
        def failing(*_args, **_kwargs):
            raise ChatApiError("Groq is rate-limited.")

        fast = agent(delegate=lambda task: "   ")
        fast._chat = failing
        result = fast.run("anything", lambda e: None, threading.Event())
        self.assertFalse(result.ok)


class ClaimedButNotDone(unittest.TestCase):
    """A small model will describe a handover instead of performing one.

    Observed live: asked to refactor a module, Groq replied "I've handed the refactor
    request to Claude; it will update the audio module" and never called the tool. That
    is the one failure the user cannot detect for themselves, so the claim is made true
    rather than trusted.
    """

    @staticmethod
    def _says(text: str):
        def reply(*_args, **_kwargs) -> dict:
            return {"role": "assistant", "content": text}
        return reply

    def test_a_false_claim_becomes_a_real_handover(self) -> None:
        handed: list[str] = []
        fast = agent(delegate=lambda task: handed.append(task) or "Claude did it.")
        fast._chat = self._says("I've handed the refactor request to Claude; it will update the module.")
        result = fast.run("refactor the audio module", lambda e: None, threading.Event())
        self.assertTrue(handed, "the claim should have triggered a real delegation")
        self.assertEqual(result.summary, "Claude did it.")

    def test_the_phrasings_it_actually_uses(self) -> None:
        for claim in ("I've passed that to Claude.",
                      "Claude will take care of the refactor.",
                      "I'll get Claude to do that.",
                      "Sending this over to Claude now.",
                      "I asked Claude to handle it."):
            handed: list[str] = []
            fast = agent(delegate=lambda task: handed.append(task) or "done")
            fast._chat = self._says(claim)
            fast.run("do the thing", lambda e: None, threading.Event())
            self.assertTrue(handed, f"missed a claimed handover: {claim!r}")

    def test_an_ordinary_answer_is_left_alone(self) -> None:
        """The guard must not fire on a reply that merely mentions Claude, or on any
        normal answer -- escalating those would spend a slow turn for nothing."""
        for innocent in ("It's twenty past four.",
                         "Claude is the other model I can call on if you want.",
                         "I opened Spotify for you.",
                         "Jensen Huang is the chief executive of Nvidia."):
            handed: list[str] = []
            fast = agent(delegate=lambda task: handed.append(task) or "done")
            fast._chat = self._says(innocent)
            result = fast.run("something", lambda e: None, threading.Event())
            self.assertEqual(handed, [], f"should not have escalated: {innocent!r}")
            self.assertEqual(result.summary, innocent)

    def test_a_real_delegation_is_not_repeated(self) -> None:
        """Once the tool has genuinely run, a reply describing it is the truth."""
        handed: list[str] = []
        step = {"n": 0}

        def chat(*_args, **_kwargs) -> dict:
            step["n"] += 1
            if step["n"] == 1:
                return {"role": "assistant", "content": "",
                        "tool_calls": [{"id": "1", "function": {"name": "delegate_to_claude",
                                                                "arguments": '{"task": "refactor"}'}}]}
            return {"role": "assistant", "content": "I handed that to Claude and it's done."}

        fast = agent(delegate=lambda task: handed.append(task) or "Claude did it.")
        fast._chat = chat
        fast.run("refactor the audio module", lambda e: None, threading.Event())
        self.assertEqual(len(handed), 1, "the handover should happen once, not twice")


class CancellingNeverEscalates(unittest.TestCase):
    def test_a_cancelled_turn_does_not_start_claude(self) -> None:
        """Saying "stop" must not quietly start a slower, costlier turn."""
        started: list[str] = []
        cancel = threading.Event()
        cancel.set()
        fast = agent(delegate=lambda task: started.append(task) or "done")
        result = fast.run("do something hard", lambda e: None, cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()
