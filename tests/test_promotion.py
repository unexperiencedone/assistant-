"""Tests for assistant.automation.promotion: the [[TASK]] marker and the count-based
decision (none / ask / auto) that drives writing automations for the user automatically."""

import tempfile
import unittest
from pathlib import Path

from assistant.automation import promotion


class TaskMarker(unittest.TestCase):
    def test_extracts_and_strips_the_marker(self):
        task_type, cleaned = promotion.extract_task("Playing Believer for you. [[TASK: play_song]]")
        self.assertEqual(task_type, "play_song")
        self.assertEqual(cleaned, "Playing Believer for you.")

    def test_no_marker_is_left_untouched(self):
        task_type, cleaned = promotion.extract_task("Just a normal reply.")
        self.assertEqual(task_type, "")
        self.assertEqual(cleaned, "Just a normal reply.")

    def test_marker_is_case_insensitive_and_lowercased(self):
        task_type, _ = promotion.extract_task("Done. [[task: Search_Wikipedia]]")
        self.assertEqual(task_type, "search_wikipedia")


class PromotionDecisions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = promotion.PromotionStore(
            Path(self._tmp.name) / "task_frequency.json", auto_after=3, ask_min_turns=4, ask_min_cost_usd=0.01)

    def tearDown(self):
        self._tmp.cleanup()

    def test_cheap_repeats_stay_quiet_until_the_threshold(self):
        self.assertEqual(self.store.record("play_song", "play believer", 0.0, 1), promotion.DECISION_NONE)
        self.assertEqual(self.store.record("play_song", "play thunder", 0.0, 1), promotion.DECISION_NONE)
        self.assertEqual(self.store.record("play_song", "play radioactive", 0.0, 1), promotion.DECISION_AUTO)

    def test_an_expensive_turn_asks_before_the_threshold(self):
        self.assertEqual(self.store.record("write_report", "draft the weekly report", 0.05, 9),
                          promotion.DECISION_ASK)
        # Controller marks it asked once Nova actually poses the question; after that,
        # don't nag again on the next occurrence.
        self.store.mark_asked("write_report")
        self.assertEqual(self.store.record("write_report", "draft another report", 0.05, 9),
                          promotion.DECISION_NONE)

    def test_promoted_types_stop_being_tracked(self):
        self.store.mark_promoted("open_settings_page")
        self.assertEqual(self.store.record("open_settings_page", "open display settings", 0.0, 1),
                          promotion.DECISION_NONE)

    def test_persists_across_instances(self):
        self.store.record("play_song", "play believer", 0.0, 1)
        self.store.record("play_song", "play thunder", 0.0, 1)
        reopened = promotion.PromotionStore(self.store.path, auto_after=3)
        self.assertEqual(reopened.record("play_song", "play radioactive", 0.0, 1), promotion.DECISION_AUTO)


if __name__ == "__main__":
    unittest.main()


class TestSlowTurnsAreWorthSaving(unittest.TestCase):
    """A turn can be cheap in money and tool calls and still be worth never repeating,
    because you sat and waited for it."""

    def store(self, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return promotion.PromotionStore(Path(directory.name) / "freq.json", auto_after=99,
                              ask_min_turns=4, ask_min_cost_usd=0.01, ask_min_seconds=90, **kwargs)

    def test_a_long_turn_is_worth_asking_about(self):
        store = self.store()
        self.assertEqual(store.record("slow_thing", "do the slow thing", 0.0, 1, 95),
                         promotion.DECISION_ASK)

    def test_a_quick_cheap_turn_is_not(self):
        store = self.store()
        self.assertEqual(store.record("quick_thing", "do the quick thing", 0.0, 1, 4),
                         promotion.DECISION_NONE)

    def test_the_duration_is_optional(self):
        store = self.store()
        self.assertEqual(store.record("unknown_thing", "no timing given", 0.0, 1),
                         promotion.DECISION_NONE)


class TestNeverAskAfterItJustTaughtItself(unittest.TestCase):
    """The one question nobody wants: "want me to save that?", one sentence after the
    reply already said it saved something."""

    def test_a_reply_that_taught_something_is_recognised(self):
        self.assertTrue(promotion.taught_itself(
            'I also taught Nova the word "drop", so saying drop the plans works instantly next time.'))
        self.assertTrue(promotion.taught_itself("Added it as an instant command."))
        self.assertTrue(promotion.taught_itself("Learned that one, it won't need me again."))

    def test_an_ordinary_reply_is_not(self):
        self.assertFalse(promotion.taught_itself("Playing Midnight City on Spotify."))
        self.assertFalse(promotion.taught_itself("Cleared the plan, the board is empty now."))
        self.assertFalse(promotion.taught_itself(""))


class TestTheWaitWorthAskingAbout(unittest.TestCase):
    def store(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return promotion.PromotionStore(Path(directory.name) / "freq.json", auto_after=99,
                                        ask_min_turns=4, ask_min_cost_usd=0.01, ask_min_seconds=123)

    def test_two_minutes_three_seconds_is_the_line(self):
        store = self.store()
        self.assertEqual(store.record("slow", "the slow one", 0.0, 1, 124), promotion.DECISION_ASK)

    def test_anything_quicker_is_not_worth_a_question(self):
        store = self.store()
        self.assertEqual(store.record("brisk", "the brisk one", 0.0, 1, 100), promotion.DECISION_NONE)
