"""Usage ranking: decay per kind, decay-aware SQL ordering, stale choices, corrections, commands."""

import math
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from assistant.system.commands import normalize_command, worth_keeping
from assistant.system.ranking import HalfLives, decayed, rank_key
from assistant.system.search import Hit
from assistant.system.service import LocalSystem

DAY = 86400


def _settings(folder: Path):
    return SimpleNamespace(db_path=str(folder / "index.db"), roots=[], exclude_dirs=[], max_depth=2,
                           index_hidden=False, reindex_minutes=30, full_rescan_hours=24, enabled=True,
                           half_life_days=14, half_life_by_kind={"app": 30, "file": 7, "command": 10})


class RankKey(unittest.TestCase):
    def test_orders_exactly_like_the_decayed_score(self):
        half_life = 7 * DAY
        now = time.time()
        rows = [(5.0, now - 30 * DAY), (1.0, now - 1 * DAY), (2.5, now - 8 * DAY), (0.4, now)]
        by_decay = sorted(rows, key=lambda r: decayed(r[0], r[1], now, half_life), reverse=True)
        by_key = sorted(rows, key=lambda r: rank_key(r[0], r[1], half_life), reverse=True)
        self.assertEqual(by_decay, by_key)
        # ...and stays that way later, without recomputing anything.
        later = now + 90 * DAY
        self.assertEqual(sorted(rows, key=lambda r: decayed(r[0], r[1], later, half_life), reverse=True), by_key)

    def test_raw_score_order_would_be_wrong(self):
        half_life = 7 * DAY
        now = time.time()
        old_heavy, recent_light = (10.0, now - 60 * DAY), (1.0, now)
        self.assertGreater(old_heavy[0], recent_light[0])
        self.assertGreater(rank_key(*recent_light, half_life), rank_key(*old_heavy, half_life))

    def test_half_lives_per_kind(self):
        h = HalfLives({"app": 30}, default_days=14)
        self.assertEqual(30 * DAY, h.seconds("app"))
        self.assertEqual(14 * DAY, h.seconds("folder"))


class Commands(unittest.TestCase):
    def test_spellings_of_one_command_merge(self):
        for said in ("Can you open Spotify please.", "okay, open spotify", "open Spotify for me!"):
            self.assertEqual("open spotify", normalize_command(said))

    def test_noise_and_long_requests_are_not_buttons(self):
        self.assertFalse(worth_keeping(normalize_command("We'll see you in the next video.")))
        self.assertFalse(worth_keeping(normalize_command(
            "brainstorm an idea on isro's remote sensing problem for analysing optical and sar images")))
        self.assertTrue(worth_keeping(normalize_command("Play the song on Spotify")))


class LocalRanking(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.system = LocalSystem(_settings(self.folder), self.folder)
        conn = self.system.db.connect()
        with conn:
            for i, name in enumerate(["Spotify", "Spotify Helper"], 1):
                conn.execute("INSERT INTO items (id, kind, name, path, seen_at) VALUES (?, 'app', ?, ?, 0)",
                             (i, name, f"shell:AppsFolder\\app{i}"))

    def tearDown(self):
        self.system.db.connect().close()
        self.system.db._local.conn = None
        shutil.rmtree(self.folder, ignore_errors=True)

    def hit(self, id_):
        r = self.system.db.connect().execute("SELECT * FROM items WHERE id = ?", (id_,)).fetchone()
        return Hit(r["id"], r["kind"], r["name"], r["path"], r["size"], 0.0, 0.0)

    def test_commands_become_chips_only_when_repeated(self):
        self.system.record_command("Can you open Spotify please.")
        self.assertEqual([], self.system.top_commands())
        self.system.record_command("open spotify")
        self.assertEqual(["open spotify"], self.system.top_commands())
        self.assertFalse(self.system.record_command("Thanks for watching!"))

    def test_a_stale_remembered_choice_stops_winning(self):
        self.system.record_use(self.hit(2), "spotify", "app")
        self.assertEqual(2, self.system.resolve("spotify", "app")[0].id)
        with self.system.db.connect() as conn:  # that one pick was 45 days ago (app half-life is 30)
            conn.execute("UPDATE query_cache SET last_used = ?", (int(time.time() - 45 * DAY),))
        self.assertIsNone(self.system._cached_query("app:spotify"))

    def test_a_correction_demotes_and_forgets(self):
        wrong, right = self.hit(2), self.hit(1)
        for _ in range(3):
            self.system.record_use(wrong, "spotify", "app")
        before = self.system.db.connect().execute("SELECT score FROM items WHERE id = 2").fetchone()["score"]
        self.system.record_rejection(wrong, "spotify", "app")
        self.system.record_use(right, "spotify", "app")
        after = self.system.db.connect().execute("SELECT score FROM items WHERE id = 2").fetchone()["score"]
        self.assertTrue(math.isclose(after, before / 2, rel_tol=0.01), (before, after))
        self.assertEqual(1, self.system.resolve("spotify", "app")[0].id)

    def test_rank_keys_follow_a_half_life_change(self):
        self.system.record_use(self.hit(1))
        key = lambda: self.system.db.connect().execute("SELECT rank_key FROM items WHERE id = 1").fetchone()[0]
        old = key()
        self.system.half_lives = HalfLives({"app": 60}, 14)
        self.system._sync_rank_keys()
        self.assertNotEqual(old, key())


if __name__ == "__main__":
    unittest.main()
