import os
import unittest
from unittest import mock

from assistant.agents import keypool
from assistant.titles import cite, short_title


class KeyPoolTests(unittest.TestCase):
    ENV = {"TESTPOOL_KEY": "a", "TESTPOOL_KEY_2": "b", "TESTPOOL_KEY_3": "c", "TESTPOOL_KEYS": "d, a"}

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, self.ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pool = keypool.KeyPool("TESTPOOL_KEY")

    def test_keys_are_gathered_once_each(self) -> None:
        self.assertEqual(self.pool.keys, ["a", "b", "c", "d"])

    def test_each_lane_leans_on_its_own_key(self) -> None:
        self.assertEqual([self.pool.pick(lane) for lane in keypool.LANES], ["a", "b", "c", "d"])

    def test_a_rate_limited_key_is_skipped_by_everyone(self) -> None:
        self.pool.cool("a", 60)
        self.assertEqual(self.pool.pick("chat"), "b")
        self.assertEqual(self.pool.pick("parallel"), "b")

    def test_all_cooling_means_no_key_and_a_wait(self) -> None:
        for key in self.pool.keys:
            self.pool.cool(key, 60)
        self.assertEqual(self.pool.pick("chat"), "")
        self.assertGreater(self.pool.wait_seconds(), 50)

    def test_status_never_names_a_key(self) -> None:
        self.pool.cool("b", 60)
        self.assertNotIn("b", self.pool.status().replace("cooling", ""))


class RetrySecondsTests(unittest.TestCase):
    def test_groq_sentence(self) -> None:
        self.assertAlmostEqual(keypool.retry_seconds("Please try again in 10.3275s. Need more"), 10.3275)
        self.assertEqual(keypool.retry_seconds("try again in 1m30s"), 90)
        self.assertEqual(keypool.retry_seconds("try again in 2m"), 120)

    def test_header_wins_and_default_otherwise(self) -> None:
        self.assertEqual(keypool.retry_seconds("try again in 5s", "12"), 12)
        self.assertEqual(keypool.retry_seconds("slow down"), keypool.DEFAULT_COOLDOWN)


class TitleTests(unittest.TestCase):
    def test_filler_is_dropped(self) -> None:
        self.assertEqual(short_title("Thought of day please"), "Thought of day")
        self.assertEqual(short_title("hey nova can you please play big hailey for me"), "Play big hailey")

    def test_a_cut_transcript_loses_its_half_word(self) -> None:
        self.assertEqual(short_title("not big hailey but be..."), "Not big hailey…")

    def test_internal_prompts_are_named_not_quoted(self) -> None:
        self.assertEqual(short_title("You've now handled requests like this three times"), "Saving an automation")
        self.assertEqual(short_title("learning: play_song"), "Learning play song")

    def test_cite_prefers_the_task_type(self) -> None:
        self.assertEqual(cite("Thought of day please", "daily_quote"), "Daily quote")
        self.assertEqual(cite("Thought of day please"), "Thought of day")


if __name__ == "__main__":
    unittest.main()
