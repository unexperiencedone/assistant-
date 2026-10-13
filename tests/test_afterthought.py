"""AfterThought: consent at the watcher, secrets stripped, silence by default, learning from answers.

Synthetic observations and the hashing embedder: no desktop, no model, no network.
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from assistant.afterthought import AfterThoughtService, consolidate, entities, privacy, rank
from assistant.afterthought.chunker import Chunker
from assistant.afterthought.embed import HashEmbedder
from assistant.events import EventBus

DAY = 86400


class Settings:
    enabled = True
    apps = ["chrome"]
    idle_boundary_seconds = 30
    min_dwell_seconds = 20
    min_chars = 40
    text_chars = 4000
    capture_clipboard = True
    capture_text = True
    remember_conversations = True
    deny_apps: list = []
    deny_titles: list = []
    suggest = True
    threshold = 0.5
    voice_threshold = 0.6
    voice = True
    recall_threshold = 0.2
    cooldown_seconds = 0
    same_memory_hours = 6
    short_term_days = 7
    promote_refs = 2
    consolidate_hour = 3


NOTES = ("Sep 4 standup. Rollout of the payments dashboard delayed to Oct 15 because the Razorpay webhook keeps "
         "failing in staging. Client Acme Corp not yet informed. Rahul owns the fix.")


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.now = time.time() - 3 * DAY
        self.tray, self.asked = [], []
        self.free = True
        self.svc = AfterThoughtService(
            self.folder / "m.db", EventBus(), Settings(), notify=self.tray.append,
            offer=lambda question, answer: self.asked.append((question, answer)),
            can_speak=lambda: self.free, embedder=HashEmbedder(), clock=lambda: self.now)
        self.svc.store.set_meta("consolidated_at", self.now + 30 * DAY)   # no nightly pass mid-test
        self.seq = 0

    def tearDown(self):
        self.svc.store.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def work(self, process, title, seconds, text="", clip=None):
        end = self.now + seconds
        while self.now < end:
            obs = {"ts": self.now, "process": process, "title": title, "idle": 0, "text": text, "clip_seq": self.seq}
            if clip and self.now + 2 >= end:
                self.seq += 1
                obs.update(clip=clip, clip_seq=self.seq)
            self.svc.tick(obs)
            self.now += 2

    def leave(self):
        self.svc.tick({"ts": self.now, "process": "", "title": "", "idle": 60, "text": "", "clip_seq": self.seq})


class Consent(Base):
    def test_only_opted_in_apps_are_remembered(self):
        self.work("chrome.exe", "Standup notes - Google Chrome", 60, text=NOTES)
        self.work("notepad.exe", "secret plans - Notepad", 60, text="Plans for a surprise party with Rahul " * 3)
        self.leave()
        apps = {m["app"] for m in self.svc.store.listing()}
        self.assertEqual(apps, {"Chrome"})

    def test_private_windows_are_skipped_before_anything_is_read(self):
        self.work("chrome.exe", "New Incognito Tab - Google Chrome", 60, text=NOTES)
        self.work("chrome.exe", "HDFC Bank NetBanking - Google Chrome", 60, text=NOTES)
        self.leave()
        self.assertEqual(self.svc.store.listing(), [])

    def test_switching_an_app_on_by_voice(self):
        self.assertEqual(self.svc.set_consent("vs code", True), "VS Code")
        self.work("code.exe", "webhook.py - payments - Visual Studio Code", 60,
                  text="def handle_razorpay_webhook(event): verify the signature, then retry failed payments")
        self.leave()
        self.assertEqual([m["app"] for m in self.svc.store.listing()], ["VS Code"])


class Privacy(unittest.TestCase):
    def test_secrets_are_stripped(self):
        text = privacy.redact("password: hunter2 and card 4111 1111 1111 1111, OTP is 482913, key sk-abcdefghijklmnopqrstuv")
        for secret in ("hunter2", "4111", "482913", "sk-abc"):
            self.assertNotIn(secret, text)

    def test_ordinary_text_survives(self):
        self.assertEqual(privacy.redact("Rollout moved to Oct 15 for Acme Corp"), "Rollout moved to Oct 15 for Acme Corp")


class Chunks(unittest.TestCase):
    def test_a_flicker_is_not_a_chunk_and_idle_closes_one(self):
        chunker = Chunker(idle_boundary=30, min_dwell=20)
        chunker.observe(0, "Chrome", "a", 0)
        self.assertEqual(chunker.observe(6, "Code", "b", 0), [])          # 6 s in Chrome: passing by
        chunker.observe(40, "Code", "b", 0)
        closed = chunker.observe(80, "Code", "b", 35)                     # idle past the boundary
        self.assertEqual([c["context"] for c in closed], ["b"])


class Entities(unittest.TestCase):
    def test_a_name_inside_a_longer_one_counts(self):
        self.assertEqual(entities.overlap(["acme corp", "rahul"], ["client acme corp", "rahul", "oct 15"]), 2)


class Suggestions(Base):
    def seed(self):
        self.work("chrome.exe", "Standup notes - Google Docs - Google Chrome", 120, text=NOTES)
        self.leave()
        self.now += 3 * DAY

    def test_a_related_context_brings_the_memory_up_with_its_source(self):
        self.seed()
        self.work("chrome.exe", "Compose: Acme Corp payments dashboard rollout - Gmail - Google Chrome", 30,
                  text="Hi Acme Corp, following up on the payments dashboard rollout and the Razorpay webhook. Rahul")
        self.assertEqual(len(self.tray), 1)
        self.assertIn("Standup notes", self.tray[0])

    def test_an_unrelated_context_stays_silent(self):
        self.seed()
        self.work("chrome.exe", "IPL 2026 points table - Google Chrome", 30,
                  text="Mumbai Indians beat Chennai Super Kings by six wickets at Wankhede last night.")
        self.assertEqual(self.tray, [])

    def test_what_you_just_did_is_not_suggested_back(self):
        self.work("chrome.exe", "Standup notes - Google Docs - Google Chrome", 120, text=NOTES)
        self.work("chrome.exe", "Payments rollout follow-up - Google Chrome", 30, text=NOTES)
        self.assertEqual(self.tray, [])

    def test_strong_matches_are_asked_aloud_only_when_nova_is_free(self):
        self.seed()
        self.free = False
        self.work("chrome.exe", "Compose: payments dashboard rollout - Gmail - Google Chrome", 30, text=NOTES)
        self.assertEqual(self.asked, [])
        self.assertEqual(len(self.tray), 1)

    def test_answers_move_the_ranking(self):
        self.seed()
        self.work("chrome.exe", "Compose: payments dashboard rollout - Gmail - Google Chrome", 30, text=NOTES)
        self.assertTrue(self.asked)
        memory_id = self.svc.store.suggestions(1)[0]["memory_id"]
        before = rank.agreement(self.svc.store.get(memory_id))
        self.asked[0][1](False)
        self.assertLess(rank.agreement(self.svc.store.get(memory_id)), before)


class Recall(Base):
    def test_asking_finds_it_and_two_references_promote_it(self):
        self.work("chrome.exe", "Standup notes - Google Chrome", 120, text=NOTES)
        self.leave()
        hits = self.svc.recall("when is the payments dashboard rollout")
        self.assertTrue(hits)
        self.assertIn("Oct 15", hits[0]["focus"])
        self.svc.recall("payments dashboard rollout Razorpay")
        self.assertEqual(self.svc.store.counts()["long"], 1)


class Consolidation(Base):
    def test_related_chunks_merge_and_a_lone_trivial_one_expires(self):
        for title in ("Standup notes - Google Chrome", "Razorpay webhook docs - Google Chrome"):
            self.work("chrome.exe", title, 40, text=NOTES)
        self.work("chrome.exe", "Weather - Google Chrome", 30, text="Kanpur weather today is sunny and warm, 33 degrees.")
        self.leave()
        self.now += DAY
        report = consolidate.run(self.svc.store, self.now)
        self.assertEqual(report["add"], 1)
        long = self.svc.store.listing(tier="long")
        self.assertEqual(len(long), 1)
        self.assertGreaterEqual(len(long[0]["sources"]), 2)
        self.now += 8 * DAY
        consolidate.run(self.svc.store, self.now)
        self.assertEqual(self.svc.store.counts()["short"], 0)
        self.assertEqual(self.svc.store.counts()["long"], 1)

    def test_forgetting_an_entry_forgets_what_it_was_made_from(self):
        self.work("chrome.exe", "Standup notes - Google Chrome", 120, text=NOTES)
        self.leave()
        self.now += DAY
        consolidate.run(self.svc.store, self.now)
        entry = self.svc.store.listing(tier="long")[0]
        self.svc.forget(entry["id"])
        self.assertEqual(self.svc.store.counts(), {"short": 0, "long": 0})


class Store(unittest.TestCase):
    def test_search_is_cosine_over_unit_vectors(self):
        ids = np.array([1, 2])
        matrix = np.array([[1, 0], [0, 1]], dtype=np.float32)
        self.assertEqual(rank.search(np.array([0.6, 0.8], dtype=np.float32), ids, matrix)[0][0], 2)


if __name__ == "__main__":
    unittest.main()
