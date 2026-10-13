"""Token and cost analytics (assistant/usage).

The numbers here are the user's evidence for what Nova costs, so each rule is pinned:
Claude's running total is never summed as if it were per turn, backfilled turns are
never counted twice, and a saving is only claimed where something cheaper actually
replaced something dearer.
"""

import json
import tempfile
import unittest
from pathlib import Path

from assistant.agents.claude_cli import ClaudeCliAgent
from assistant.config import ClaudeAgentSettings
from assistant.events import EventBus
from assistant.usage import backfill, normalize, report
from assistant.usage.ledger import UsageLedger
from test_server_auth import TOKEN, call, make_app


def claude_line(total, tokens=1000, text="done"):
    return {"type": "result", "total_cost_usd": total, "result": text, "num_turns": 2, "session_id": "s",
            "usage": {"input_tokens": 10, "output_tokens": tokens // 10, "cache_read_input_tokens": tokens,
                      "cache_creation_input_tokens": 50, "output_tokens_details": {"thinking_tokens": 5}},
            "modelUsage": {"claude-opus-5-5": {}}}


class TestClaudeCost(unittest.TestCase):
    def test_a_turns_cost_is_the_step_in_the_running_total(self):
        agent = ClaudeCliAgent(ClaudeAgentSettings(), Path("."), True, "")
        costs = []
        for total in (0.30, 3.12, 3.27, 3.27):
            event = next(e for e in agent.parse(claude_line(total)) if e.kind == "result")
            costs.append(event.data["cost_usd"])
        self.assertEqual([round(c, 2) for c in costs], [0.30, 2.82, 0.15, 0.0])

    def test_a_new_process_starts_counting_from_zero(self):
        agent = ClaudeCliAgent(ClaudeAgentSettings(), Path("."), True, "")
        list(agent.parse(claude_line(5.0)))
        event = next(e for e in agent.parse(claude_line(0.4)) if e.kind == "result")  # restarted with --resume
        self.assertAlmostEqual(event.data["cost_usd"], 0.4)


class TestNormalize(unittest.TestCase):
    def test_each_brain_in_its_own_words(self):
        claude = normalize.from_result("claude", {**claude_line(1.0), "cost_usd": 0.25})
        self.assertEqual((claude["input"], claude["cache_read"], claude["cache_write"], claude["thinking"]), (10, 1000, 50, 5))
        self.assertEqual(claude["cost_usd"], 0.25)  # the turn's own cost wins over the running total
        self.assertEqual(claude["model"], "claude-opus-5-5")
        agy = normalize.from_result("antigravity", {"usage": {"input_tokens": 300, "output_tokens": 20,
                                                              "thinking_tokens": 15, "cache_read_tokens": 900}})
        self.assertEqual((agy["input"], agy["output"], agy["cache_read"], agy["cost_usd"]), (300, 20, 900, 0.0))
        groq = normalize.from_result("groq", {"usage": {"prompt_tokens": 4000, "completion_tokens": 200, "rounds": 2},
                                              "model": "openai/gpt-oss-120b"})
        self.assertEqual(normalize.total(groq), 4200)

    def test_thinking_is_part_of_output_not_extra(self):
        row = normalize.from_result("claude", claude_line(1.0, tokens=1000))
        self.assertEqual(normalize.total(row), 10 + 1000 + 50 + 100)


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.bus = EventBus()
        self.ledger = UsageLedger(self.root / "usage.db", self.bus)

    def tearDown(self):
        self.ledger.close()
        self.dir.cleanup()


class TestLiveRecording(LedgerCase):
    def test_a_runner_turn_is_recorded_with_its_request_and_type(self):
        self.bus.publish("agent", kind="start", backend="Claude Code", text="play some jazz", task="t1")
        self.bus.publish("agent", kind="result", backend="Claude Code", task="t1",
                         text="Playing jazz.\n[[TASK: play_song]] (12s)", data={**claude_line(0.2), "cost_usd": 0.2})
        [turn] = self.ledger.rows("turns")
        self.assertEqual((turn["backend"], turn["task_type"], turn["request"], turn["seconds"]),
                         ("claude", "play_song", "play some jazz", 12.0))
        self.assertAlmostEqual(turn["cost_usd"], 0.2)

    def test_a_delegated_turn_is_no_longer_invisible(self):
        self.bus.publish("usage", backend="Claude Code", data={"cost_usd": 0.5, "usage": {"input_tokens": 3}},
                         request="find clients\n\n(Nova's fast model was asked...)", ok=True, text="Found five.")
        [turn] = self.ledger.rows("turns")
        self.assertTrue(turn["delegated"])
        self.assertEqual(turn["request"], "find clients")

    def test_free_answers_count_once(self):
        self.bus.publish("route", utterance="next track", route="automation", name="Next track")
        self.bus.publish("route", utterance="next track", route="macro", name="Next track")  # same request, from the runner
        self.bus.publish("route", utterance="hello", route="agent", backend="Groq")
        self.assertEqual([(f["route"], f["name"]) for f in self.ledger.rows("free")], [("automation", "Next track")])


class TestBackfill(LedgerCase):
    def write_turn(self, folder, number, lines):
        path = self.root / "sessions" / "2026-10-01" / folder / f"turn-{number:02d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    def test_running_totals_become_per_turn_costs_and_nothing_is_counted_twice(self):
        for number, total in ((1, 0.30), (2, 3.12), (10, 3.27)):  # turn-10 sorts after turn-2 by number
            self.write_turn("claude-abc", number, [
                {"ts": 1000.0 + number, "kind": "meta", "data": {"backend": "claude", "prompt": f"job {number}"}},
                {"ts": 1001.0 + number, "kind": "raw", "data": claude_line(total)},
                {"ts": 1002.0 + number, "kind": "result", "data": {"summary": "ok", "ok": True, "seconds": 3}},
            ])
        self.ledger.set_meta("live_since", "999999999999")
        self.assertEqual(backfill.sessions(self.ledger, self.root / "sessions"), 3)
        self.assertEqual(backfill.sessions(self.ledger, self.root / "sessions"), 0)  # second run adds nothing
        costs = [round(t["cost_usd"], 2) for t in self.ledger.rows("turns")]
        self.assertEqual(costs, [0.30, 2.82, 0.15])

    def test_turns_after_live_recording_began_are_left_to_the_live_rows(self):
        self.write_turn("groq-session", 1, [
            {"ts": 50.0, "kind": "meta", "data": {"backend": "groq", "prompt": "hi"}},
            {"ts": 51.0, "kind": "raw", "data": {"usage": {"prompt_tokens": 100, "completion_tokens": 5}}},
        ])
        self.ledger.set_meta("live_since", "10")
        self.assertEqual(backfill.sessions(self.ledger, self.root / "sessions"), 0)

    def test_weekly_route_counts_are_imported_once(self):
        routes = self.root / "routes.json"
        routes.write_text(json.dumps({"weeks": {"2026-W38": {"intent": 4, "agent:claude": 2}}}), encoding="utf-8")
        self.assertEqual(backfill.routes(self.ledger, routes), 1)
        self.assertEqual(backfill.routes(self.ledger, routes), 0)
        self.assertEqual(sum(f["count"] for f in self.ledger.rows("free")), 4)


class TestReport(LedgerCase):
    def claude(self, cost, task="", ok=True):
        self.ledger.record_turn("claude", {**claude_line(cost), "cost_usd": cost}, text=f"[[TASK: {task}]]" if task else "", ok=ok)

    def test_a_free_turn_is_worth_a_claude_turn_of_its_kind(self):
        for cost in (0.10, 0.20, 0.30):
            self.claude(cost, "news_briefing")
        self.claude(2.0, "big_job")
        self.ledger.record_turn("groq", {"usage": {"prompt_tokens": 5000, "completion_tokens": 100}},
                                text="[[TASK: news_briefing]]")
        source = next(s for s in report.summary(self.ledger, None)["saved"]["sources"] if s["source"] == "free_model")
        self.assertEqual(source["count"], 1)
        self.assertAlmostEqual(source["usd"], 0.20)  # the news median, not the all-Claude median

    def test_escalated_or_failed_free_turns_save_nothing(self):
        self.claude(0.5)
        self.ledger.record_turn("groq", {"usage": {"prompt_tokens": 10}, "escalated": "out of steps"})
        self.ledger.record_turn("groq", {"usage": {"prompt_tokens": 10}}, ok=False)
        saved = report.summary(self.ledger, None)["saved"]
        self.assertEqual(saved["usd"], 0.0)

    def test_a_script_only_saves_money_for_kinds_claude_really_did(self):
        self.claude(0.4, "news_briefing")
        self.ledger.record_free("automation", "News briefing")
        self.ledger.record_free("intent", "volume_up")
        summary = report.summary(self.ledger, None, aliases={"News briefing": "news_briefing"},
                                 automated={"news_briefing"})
        scripted = next(s for s in summary["saved"]["sources"] if s["source"] == "scripted")
        self.assertEqual(scripted["count"], 2)
        self.assertAlmostEqual(scripted["usd"], 0.4)  # volume_up would have gone to the free model: $0

    def test_failed_turns_and_quiet_days_are_shown(self):
        self.claude(0.7, ok=False)
        summary = report.summary(self.ledger, 7)
        self.assertAlmostEqual(summary["spent"]["failed_usd"], 0.7)
        self.assertTrue(all("claude" in d for d in summary["daily"]))
        self.assertIn("SAVED (estimated)", report.text(summary))


class TestEndpoint(unittest.TestCase):
    def test_usage_says_when_it_is_off(self):
        response = call(make_app(), "GET", "/api/usage", headers={"x-nova-token": TOKEN})
        self.assertEqual(response.json(), {"enabled": False})


if __name__ == "__main__":
    unittest.main()
