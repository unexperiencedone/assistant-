"""A request made while a long job runs still knows what was just said.

From a real session. A long research task was running, so every following request became
a parallel task -- and a parallel task started with no conversation at all:

    open kaiketsutech.online          -> "The Kaiketsu Tech site is open in Chrome."
    find the contact form and fill it -> "could you tell me the website URL?"
    kaiketsutech.online               -> "The site wasn't open anymore, so I opened it again."
    fil the contact form and submit it -> "what's the web address of the contact form?"

Three turns, each a fresh mind. Meanwhile every one of them was prefixed out loud with
"Starting that alongside compile a list of all potential customers that can possibly...",
the raw request truncated to sixty characters and read aloud verbatim. And a question
about how far the research had got was queued behind the very task it asked about.
"""

from __future__ import annotations

import os
import unittest

from assistant.config import GroqAgentSettings
from assistant.controller import spoken_label
from assistant.intents import match_intent


def setUpModule() -> None:
    os.environ.setdefault("GROQ_API_KEY", "stub-key-for-tests")


def agent():
    from assistant.agents.chat_api import GroqAgent

    return GroqAgent(GroqAgentSettings(), workspace=None, continue_session=False,
                     assistant_name="Nova")


CONVERSATION = [
    {"role": "user", "content": "open kaiketsutech.online"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "1", "function": {"name": "browse_open", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "1", "name": "browse_open", "content": "the page loaded"},
    {"role": "assistant", "content": "The Kaiketsu Tech site is open in Chrome."},
    {"role": "user", "content": "find the contact form and fill it"},
]


class AParallelTaskKeepsTheThread(unittest.TestCase):
    def setUp(self) -> None:
        self.parent = agent()
        self.parent.history = [dict(message) for message in CONVERSATION]

    def test_it_no_longer_starts_from_nothing(self) -> None:
        twin = self.parent.spawn()
        self.assertTrue(twin.history, "a follow-up with no history cannot resolve 'it'")
        self.assertTrue(any("kaiketsutech" in (m.get("content") or "") for m in twin.history))

    def test_tool_messages_are_left_behind(self) -> None:
        """Each belongs to a specific assistant tool_calls message, and a tail that
        starts mid-exchange is rejected by the API outright."""
        twin = self.parent.spawn()
        self.assertNotIn("tool", [m["role"] for m in twin.history])
        self.assertFalse([m for m in twin.history if m.get("tool_calls")])

    def test_the_parent_is_untouched(self) -> None:
        before = len(self.parent.history)
        twin = self.parent.spawn()
        twin.history.append({"role": "user", "content": "something else"})
        self.assertEqual(len(self.parent.history), before)

    def test_only_the_recent_turns_come(self) -> None:
        self.parent.history = [{"role": "user", "content": f"line {n}"} for n in range(40)]
        twin = self.parent.spawn()
        self.assertLessEqual(len(twin.history), self.parent.CARRIED_TURNS)
        self.assertIn("line 39", twin.history[-1]["content"])

    def test_an_empty_conversation_spawns_empty(self) -> None:
        self.parent.history = []
        self.assertEqual(self.parent.spawn().history, [])

    def test_the_tools_and_profile_are_still_shared(self) -> None:
        self.parent.profile = "the owner is Aakshant"
        twin = self.parent.spawn()
        self.assertIs(twin.context, self.parent.context)
        self.assertEqual(twin.profile, self.parent.profile)


class HowATaskIsNamedAloud(unittest.TestCase):
    def test_a_truncated_request_is_referred_to_not_read(self) -> None:
        said = spoken_label("compile a list of all potential customers that can possibly...")
        self.assertEqual(said, "the job already running")

    def test_a_long_label_too(self) -> None:
        self.assertEqual(spoken_label("x" * 60), "the job already running")

    def test_a_short_label_is_used_as_it_is(self) -> None:
        """Naming it is useful when two are running; only unreadable ones are hidden."""
        for label in ("the plan", "learning: play_song", "fixing: News briefing"):
            self.assertEqual(spoken_label(label), label)

    def test_nothing_is_still_something(self) -> None:
        self.assertEqual(spoken_label(""), "the job already running")
        self.assertEqual(spoken_label(None), "the job already running")


class ProgressIsAnsweredNotQueued(unittest.TestCase):
    """A question about a task must never wait behind that task."""

    def test_the_question_that_was_queued(self) -> None:
        self.assertEqual(match_intent("so how much part of the research is done").name, "status")

    def test_the_family_around_it(self) -> None:
        for said in ("status", "progress", "any progress", "how far along are you",
                     "how much is left", "are you still working", "how's it going",
                     "what's the latest on it", "what is groq doing"):
            found = match_intent(said)
            self.assertIsNotNone(found, said)
            self.assertEqual(found.name, "status", said)

    def test_a_real_question_about_cost_is_not_a_status_check(self) -> None:
        for said in ("how much does a website cost", "how many users does it have"):
            found = match_intent(said)
            self.assertNotEqual(getattr(found, "name", None), "status", said)


class EveryBrainNotJustGroq(unittest.TestCase):
    """The amnesia fix started as a chat-backend change, which left the CLI agents with
    the same bug: Claude and Antigravity spawn a twin with its own session id and no
    conversation, and a session id cannot be shared -- two processes writing one
    conversation would corrupt it. So a CLI twin is told in words instead."""

    @staticmethod
    def _cli_backend():
        from assistant.agents.base import AgentBackend

        class Fake(AgentBackend):
            name, label = "fake", "Fake"

            def __init__(self) -> None:
                super().__init__("x", None, False)

            def resolve_executable(self):
                return None

            def build_command(self, exe, task):
                return []

            def parse(self, obj):
                return ()

        return Fake()

    def test_a_cli_backend_can_be_told_what_was_said(self) -> None:
        backend = self._cli_backend()
        backend.carry("You: open kaiketsutech.online\nNova: The site is open in Chrome.")
        prompt = backend._with_carried("fill in the form")
        self.assertIn("kaiketsutech.online", prompt)
        self.assertIn("fill in the form", prompt)

    def test_it_is_carried_once_and_then_cleared(self) -> None:
        """The second request in that session has the conversation of its own by then."""
        backend = self._cli_backend()
        backend.carry("You: something")
        backend._with_carried("first")
        self.assertEqual(backend._with_carried("second"), "second")

    def test_nothing_carried_leaves_the_prompt_alone(self) -> None:
        self.assertEqual(self._cli_backend()._with_carried("just this"), "just this")

    def test_a_chat_backend_ignores_it_deliberately(self) -> None:
        """It copies the real messages in spawn, which beats a summary in the prompt."""
        backend = agent()
        backend.carry("You: something")
        self.assertEqual(backend._with_carried("the request"), "the request")

    def test_every_backend_has_the_method(self) -> None:
        from assistant.agents.registry import AgentRegistry
        from assistant.config import load_settings

        for name, backend in AgentRegistry(load_settings()).backends.items():
            self.assertTrue(hasattr(backend, "carry"), name)
            self.assertTrue(hasattr(backend, "_with_carried"), name)


if __name__ == "__main__":
    unittest.main()
