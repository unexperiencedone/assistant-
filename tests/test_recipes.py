"""Recipes: what worked, recorded and fed back.

The two properties that matter are here. A recipe must come back for a request that
really is similar, and must stay silent otherwise -- a confident hint about the wrong
task is worse than no hint, because a model follows it anyway.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant.events import EventBus
from assistant.recipes import RecipeService, RecipeStore


class StoreBasics(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = RecipeStore(Path(self.dir.name) / "recipes.db")

    def tearDown(self) -> None:
        self.store.close()
        self.dir.cleanup()

    def test_remembers_a_success(self) -> None:
        self.store.remember("play_song", "play some jazz on spotify",
                            tools=["open_item", "click_control"], backend="Groq", seconds=3.5)
        row = self.store.get("play_song")
        self.assertEqual(row["tools"], ["open_item", "click_control"])
        self.assertEqual(row["wins"], 1)

    def test_a_repeat_updates_instead_of_duplicating(self) -> None:
        """One row per kind of task: the newest working route is the one worth keeping."""
        self.store.remember("play_song", "play jazz", tools=["open_item"])
        self.store.remember("play_song", "play some blues", tools=["run_automation"])
        rows = self.store.all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tools"], ["run_automation"])
        self.assertEqual(rows[0]["wins"], 2)

    def test_pitfalls_accumulate_rather_than_replace(self) -> None:
        """An old dead end is still a dead end, so failures build up where successes
        overwrite. This is the half a successful transcript can never teach."""
        self.store.note_failure("play_song", "the window is called Spotify Premium, not Spotify")
        self.store.note_failure("play_song", "clicking Play before the app loads does nothing")
        row = self.store.get("play_song")
        self.assertIn("Spotify Premium", row["pitfalls"])
        self.assertIn("before the app loads", row["pitfalls"])
        self.assertEqual(row["fails"], 2)

    def test_the_same_failure_twice_is_one_lesson(self) -> None:
        note = "the window is called Spotify Premium"
        self.store.note_failure("play_song", note)
        self.store.note_failure("play_song", note)
        row = self.store.get("play_song")
        self.assertEqual(row["pitfalls"].count("Spotify Premium"), 1)
        self.assertEqual(row["fails"], 2)

    def test_a_failure_is_kept_with_no_success_yet(self) -> None:
        self.store.note_failure("brand_new", "that approach times out")
        self.assertIsNotNone(self.store.get("brand_new"))

    def test_nothing_is_stored_without_a_task_type(self) -> None:
        self.store.remember("", "some request", tools=["x"])
        self.assertEqual(self.store.all(), [])


class Matching(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.bus = EventBus()
        self.service = RecipeService(Path(self.dir.name) / "recipes.db", self.bus)

    def tearDown(self) -> None:
        self.service.close()
        self.dir.cleanup()

    def test_a_similar_request_gets_the_recipe(self) -> None:
        self.service.store.remember("repo_summary", "summarise what changed in my nova repo",
                                    tools=["delegate_to_claude"], backend="Claude", seconds=40)
        hint = self.service.hint("summarise what changed in the nova repo today")
        self.assertIn("delegate_to_claude", hint)
        self.assertIn("evidence, not a script", hint)

    def test_an_unrelated_request_gets_nothing(self) -> None:
        """The important half: silence when unsure."""
        self.service.store.remember("repo_summary", "summarise what changed in my nova repo",
                                    tools=["delegate_to_claude"])
        for unrelated in ("turn on the torch", "what time is it",
                          "play some jazz", "text mum saying running late"):
            self.assertEqual(self.service.hint(unrelated), "", f"{unrelated} should not match")

    def test_one_shared_word_is_not_enough(self) -> None:
        self.service.store.remember("repo_summary", "summarise what changed in my nova repo",
                                    tools=["delegate_to_claude"])
        self.assertEqual(self.service.hint("summarise this article for me"), "")

    def test_pitfalls_reach_the_hint(self) -> None:
        self.service.store.remember("play_song", "play some jazz on spotify", tools=["open_item"])
        self.service.store.note_failure("play_song", "the window is called Spotify Premium")
        hint = self.service.hint("play some jazz on spotify")
        self.assertIn("dead ends", hint)
        self.assertIn("Spotify Premium", hint)


class ToolCollection(unittest.TestCase):
    """Tool calls are collected off the bus, so nothing extra has to be instrumented."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.bus = EventBus()
        self.service = RecipeService(Path(self.dir.name) / "recipes.db", self.bus)

    def tearDown(self) -> None:
        self.service.close()
        self.dir.cleanup()

    def test_collects_in_order_per_task(self) -> None:
        self.bus.publish("agent", kind="tool", tool="web_search", task="t1")
        self.bus.publish("agent", kind="tool", tool="read_page", task="t1")
        self.assertEqual(self.service.tools_for("t1"), ["web_search", "read_page"])

    def test_parallel_tasks_do_not_mix(self) -> None:
        self.bus.publish("agent", kind="tool", tool="web_search", task="t1")
        self.bus.publish("agent", kind="tool", tool="open_item", task="t2")
        self.assertEqual(self.service.tools_for("t1"), ["web_search"])
        self.assertEqual(self.service.tools_for("t2"), ["open_item"])

    def test_a_repeated_call_is_one_step(self) -> None:
        for _ in range(4):
            self.bus.publish("agent", kind="tool", tool="web_search", task="t1")
        self.assertEqual(self.service.tools_for("t1"), ["web_search"])

    def test_remember_picks_up_the_collected_tools(self) -> None:
        self.bus.publish("agent", kind="tool", tool="web_search", task="t9")
        self.service.remember("news_lookup", "whats happening in the news", task_id="t9")
        self.assertEqual(self.service.store.get("news_lookup")["tools"], ["web_search"])
        # and the task's calls are released once recorded
        self.assertEqual(self.service.tools_for("t9"), [])


if __name__ == "__main__":
    unittest.main()
