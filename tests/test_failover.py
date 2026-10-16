import threading
import unittest

from assistant.agents import failover
from assistant.agents.base import AgentEvent, AgentResult


class _Brain:
    def __init__(self, name, result, installed=True, tools=0):
        self.name, self.label = name, name.title()
        self.result, self.installed, self.tools = result, installed, tools
        self.prompts, self.spawned = [], []

    def resolve_executable(self):
        return "x" if self.installed else None

    def spawn(self):
        twin = _Brain(self.name, self.result, self.installed, self.tools)
        self.spawned.append(twin)
        return twin

    def run(self, prompt, on_event, cancel):
        self.prompts.append(prompt)
        for _ in range(self.tools):
            on_event(AgentEvent("tool", tool="bash"))
        return self.result


OK = AgentResult(True, "done")
LIMIT = AgentResult(False, "Claude AI usage limit reached")


class FailoverTests(unittest.TestCase):
    def run_on(self, first, other):
        backends = {first.name: first, other.name: other}
        return failover.run(first, "open my mail", lambda e: None, threading.Event(), backends)

    def test_an_answer_stays_where_it_was_sent(self):
        claude, agy = _Brain("claude", OK), _Brain("antigravity", OK)
        answered, result = self.run_on(claude, agy)
        self.assertIs(answered, claude)
        self.assertEqual(agy.spawned, [])

    def test_claude_without_an_answer_goes_to_antigravity(self):
        claude, agy = _Brain("claude", LIMIT), _Brain("antigravity", OK)
        answered, result = self.run_on(claude, agy)
        self.assertEqual(answered.name, "antigravity")
        self.assertTrue(result.ok)
        self.assertEqual(answered.prompts, ["open my mail"])

    def test_and_the_other_way(self):
        agy = _Brain("antigravity", AgentResult(False, "I can't find the Antigravity command 'agy'."), installed=False)
        claude = _Brain("claude", OK)
        answered, result = self.run_on(agy, claude)
        self.assertEqual(answered.name, "claude")
        self.assertTrue(result.ok)

    def test_cancelled_is_not_handed_over(self):
        claude = _Brain("claude", AgentResult(False, "Cancelled.", cancelled=True))
        agy = _Brain("antigravity", OK)
        answered, _ = self.run_on(claude, agy)
        self.assertIs(answered, claude)
        self.assertEqual(agy.spawned, [])

    def test_a_missing_partner_leaves_the_first_answer(self):
        claude, agy = _Brain("claude", LIMIT), _Brain("antigravity", OK, installed=False)
        answered, result = self.run_on(claude, agy)
        self.assertIs(answered, claude)
        self.assertIs(result, LIMIT)

    def test_one_handover_only_and_both_reasons(self):
        claude = _Brain("claude", AgentResult(False, "usage limit"))
        agy = _Brain("antigravity", AgentResult(False, "not signed in"))
        answered, result = self.run_on(claude, agy)
        self.assertFalse(result.ok)
        self.assertIn("usage limit", result.summary)
        self.assertIn("not signed in", result.summary)
        self.assertEqual(len(claude.prompts), 1)

    def test_work_already_started_is_mentioned(self):
        claude = _Brain("claude", AgentResult(False, "exited", detail="exit code 1"), tools=2)
        agy = _Brain("antigravity", OK)
        answered, _ = self.run_on(claude, agy)
        self.assertIn("stopped partway", answered.prompts[0])
        self.assertTrue(answered.prompts[0].endswith("open my mail"))

    def test_chat_models_have_no_partner(self):
        groq, agy = _Brain("groq", AgentResult(False, "rate limited")), _Brain("antigravity", OK)
        answered, _ = self.run_on(groq, agy)
        self.assertIs(answered, groq)


if __name__ == "__main__":
    unittest.main()
