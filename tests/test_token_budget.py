"""What a chat turn is allowed to cost.

The loop in `assistant/agents/chat_api.py` resends the whole history on every round, so
the price of a tool result is its size times the number of rounds still to come -- not
its size. Ten rounds of full-size results against an 8,000-tokens-per-minute free tier is
the rate limit, and the model was never the problem.

Three budgets hold that down, and each of these tests is one of them. The last class is
the one that matters: it asserts the shape of the growth, because a cap that is merely
present but applied in the wrong place leaves the curve quadratic and the symptom
identical.
"""

from __future__ import annotations

import os
import threading
import unittest

from assistant.agents.chat_api import GroqAgent, _fit
from assistant.agents.tools import ToolContext
from assistant.config import GroqAgentSettings


def setUpModule() -> None:
    os.environ.setdefault("GROQ_API_KEY", "stub-key-for-tests")


def agent(**overrides) -> GroqAgent:
    settings = GroqAgentSettings(**overrides)
    made = GroqAgent(settings, workspace=None, continue_session=False, assistant_name="Nova")
    made.context = ToolContext()
    return made


class Fit(unittest.TestCase):
    """Shortening a result keeps both ends, because the answer is often the last line."""

    def test_short_text_is_untouched(self) -> None:
        self.assertEqual("already small", _fit("already small", 2000))

    def test_the_limit_is_respected(self) -> None:
        self.assertLessEqual(len(_fit("x" * 5000, 500)), 500)

    def test_both_ends_survive(self) -> None:
        text = "FIRST" + ("-" * 4000) + "LAST"
        cut = _fit(text, 400)
        self.assertTrue(cut.startswith("FIRST"), cut[:20])
        self.assertTrue(cut.endswith("LAST"), cut[-20:])

    def test_it_says_how_much_went_missing(self) -> None:
        """A model that needs the rest should call the tool again more narrowly."""
        self.assertIn("characters cut", _fit("y" * 3000, 300))

    def test_no_limit_means_no_cut(self) -> None:
        self.assertEqual("z" * 100, _fit("z" * 100, 0))


class FreshResults(unittest.TestCase):
    """The copy that gets resent is capped. The copy the user sees is not."""

    def setUp(self) -> None:
        self.huge = "R" * 20_000
        self.seen: list[str] = []

    def _one_call_then_answer(self, *_args, **_kwargs) -> dict:
        """First round asks for a tool, second answers in words."""
        if any(m.get("role") == "tool" for m in self.fast.history):
            return {"role": "assistant", "content": "Done."}
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "list_windows", "arguments": "{}"}}]}

    def _run(self, **overrides) -> None:
        import assistant.agents.tools as tool_registry
        self.fast = agent(max_tool_calls=4, **overrides)
        self.fast._chat = self._one_call_then_answer
        original = tool_registry.call
        tool_registry.call = lambda *a, **k: self.huge
        try:
            self.fast.run("list the windows", lambda e: self.seen.append(e.text or ""), threading.Event())
        finally:
            tool_registry.call = original

    def test_the_history_copy_is_capped(self) -> None:
        self._run(tool_result_chars=1000)
        results = [m for m in self.fast.history if m.get("role") == "tool"]
        self.assertTrue(results)
        self.assertLessEqual(len(results[0]["content"]), 1000)

    def test_the_canvas_still_gets_the_whole_thing(self) -> None:
        """Capping what is resent must not cap what the user is shown."""
        self._run(tool_result_chars=1000)
        self.assertIn(self.huge, self.seen)


class StaleResults(unittest.TestCase):
    """A result matters in full for one round. After that, resending it buys nothing."""

    def test_older_results_shrink_but_stay(self) -> None:
        """Deleting one would break the tool_call_id pairing the API requires, so the
        message survives and only its content does not."""
        fast = agent(stale_tool_chars=100)
        fast.history = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "a"}}]},
            {"role": "tool", "tool_call_id": "1", "name": "a", "content": "A" * 5000},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "2", "function": {"name": "b"}}]},
            {"role": "tool", "tool_call_id": "2", "name": "b", "content": "B" * 5000},
        ]
        before = len(fast.history)
        fast._digest_stale_tools()
        self.assertEqual(before, len(fast.history))
        self.assertLessEqual(len(fast.history[2]["content"]), 100)   # superseded
        self.assertEqual(5000, len(fast.history[4]["content"]))      # this round's

    def test_it_is_safe_to_run_twice(self) -> None:
        fast = agent(stale_tool_chars=100)
        fast.history = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "a"}}]},
            {"role": "tool", "tool_call_id": "1", "name": "a", "content": "A" * 5000},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "2", "function": {"name": "b"}}]},
        ]
        fast._digest_stale_tools()
        once = fast.history[2]["content"]
        fast._digest_stale_tools()
        self.assertEqual(once, fast.history[2]["content"])


class Trimming(unittest.TestCase):
    """The message count was never a budget: ten small messages trim like ten big ones."""

    def test_the_character_budget_bites(self) -> None:
        fast = agent(history_messages=100, history_chars=4000)
        fast.history = [{"role": "user", "content": "Q" * 1000} for _ in range(20)]
        fast._trim_history()
        self.assertLessEqual(fast._size(), 5000)

    def test_it_never_starts_on_a_tool_result(self) -> None:
        """A tool message with no assistant message asking for it is rejected outright."""
        fast = agent(history_messages=4, history_chars=1000)
        fast.history = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "a"}}]},
            {"role": "tool", "tool_call_id": "1", "name": "a", "content": "result"},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "three"},
        ]
        fast._trim_history()
        self.assertNotIn(fast.history[0].get("role"), ("tool", "assistant"))

    def test_a_short_history_is_left_alone(self) -> None:
        fast = agent()
        fast.history = [{"role": "user", "content": "hello"}]
        fast._trim_history()
        self.assertEqual(1, len(fast.history))


class GrowthAcrossRounds(unittest.TestCase):
    """The regression that started all of this.

    A cap that exists but is applied after the history has already been sent leaves the
    curve exactly as it was. So this measures the thing itself: what the last round of a
    long turn actually carries. Without the budgets it is ten full results; with them it
    is one, plus nine one-line stubs.
    """

    def test_the_last_round_is_not_carrying_every_earlier_result(self) -> None:
        import assistant.agents.tools as tool_registry
        sizes: list[int] = []
        fast = agent(max_tool_calls=10, tool_result_chars=1500, stale_tool_chars=200,
                     history_chars=12_000)

        def endless(messages, *_args, **_kwargs) -> dict:
            sizes.append(sum(len(str(m.get("content") or "")) for m in messages))
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": str(len(sizes)), "function": {"name": "list_windows",
                                                                       "arguments": "{}"}}]}

        fast._chat = endless
        original = tool_registry.call
        tool_registry.call = lambda *a, **k: "W" * 20_000
        try:
            fast.run("keep going", lambda e: None, threading.Event())
        finally:
            tool_registry.call = original

        self.assertEqual(10, len(sizes))
        # Ten uncapped 20 KB results would put the last round past 200,000 characters.
        self.assertLess(sizes[-1], 20_000, f"history grew to {sizes[-1]} characters")
        # And it must not grow in proportion to the rounds: the second half of the turn
        # costs roughly what the first half did.
        self.assertLess(sizes[-1], sizes[4] * 2)


class Usage(unittest.TestCase):
    """What the turn cost, from the provider rather than an estimate."""

    def test_counts_accumulate_across_rounds(self) -> None:
        fast = agent()
        fast._note_usage({"usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050}})
        fast._note_usage({"usage": {"prompt_tokens": 1400, "completion_tokens": 30, "total_tokens": 1430}})
        self.assertEqual(2400, fast._usage["prompt_tokens"])
        self.assertEqual(2480, fast._usage["total_tokens"])
        self.assertEqual(2, fast._usage["rounds"])

    def test_a_provider_that_reports_nothing_is_not_an_error(self) -> None:
        fast = agent()
        fast._note_usage({})
        self.assertEqual({"rounds": 1}, fast._usage)


if __name__ == "__main__":
    unittest.main()
