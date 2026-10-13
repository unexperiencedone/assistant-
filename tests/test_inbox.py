"""Needs you: what waits on the owner, and that acting on it uses the real paths."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from assistant.events import EventBus
from assistant.inbox import InboxService
from assistant.publish import PublishService

CLEAN = '''name = "Play lofi"
description = "Plays lofi on Spotify."
phrases = ["play some lofi"]

[[steps]]
do = "media"
key = "play_pause"
'''


class Inbox(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.bus = EventBus()
        self.published = []
        self.bus.subscribe("inbox", lambda e: self.published.append(e.data))
        self.publisher = PublishService(self.root / "publish.db", self.bus)
        self.sent = []
        self.publisher.gate.register("linkedin_post", lambda d: (self.sent.append(d["id"]) or (True, "Posted.")))
        (self.root / "automations" / "pending").mkdir(parents=True)
        self.inbox = InboxService(self.bus, publisher=self.publisher, automations=self.root / "automations")

    def tearDown(self):
        self.publisher.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_draft_waits_with_its_actions_and_approving_sends_it(self):
        draft = self.publisher.gate.stage("linkedin_post", "hello", extra={"name": "Tasker"})
        items = self.inbox.items()
        self.assertEqual([i["id"] for i in items], [f"draft:{draft['id']}"])
        self.assertEqual([a["id"] for a in items[0]["actions"]], ["approve", "drop"])
        ok, _ = self.inbox.act(f"draft:{draft['id']}", "approve")
        self.assertTrue(ok)
        self.assertEqual(self.sent, [draft["id"]])
        self.assertEqual(self.inbox.items(), [])            # gone once its source no longer needs anyone
        self.assertEqual(self.published[-1]["count"], 0)

    def test_looks_good_hides_a_scheduled_post_without_unscheduling_it(self):
        draft = self.publisher.gate.stage("linkedin_post", "later", extra={"name": "Tasker"})
        self.publisher.gate.schedule(draft["id"], time.time() + 3600)
        self.assertEqual(self.inbox.items()[0]["kind"], "scheduled")
        self.inbox.act(f"draft:{draft['id']}", "ok")
        self.assertEqual(self.inbox.items(), [])
        self.assertEqual(self.publisher.gate.get(draft["id"])["status"], "scheduled")

    def test_an_automation_with_a_problem_cannot_be_kept(self):
        (self.root / "automations" / "pending" / "broken.toml").write_text('name = "x"\nphrases = []\n', encoding="utf-8")
        item = self.inbox.items()[0]
        keep = next(a for a in item["actions"] if a["id"] == "keep")
        self.assertFalse(keep["enabled"])
        ok, _ = self.inbox.act(item["id"], "keep")
        self.assertFalse(ok)

    def test_keeping_a_clean_automation_puts_it_in_service(self):
        (self.root / "automations" / "pending" / "play_lofi.toml").write_text(CLEAN, encoding="utf-8")
        item = self.inbox.items()[0]
        ok, said = self.inbox.act(item["id"], "keep")
        self.assertTrue(ok, said)
        self.assertTrue((self.root / "automations" / "play_lofi.toml").exists())
        self.assertEqual(self.inbox.items(), [])

    def test_unknown_items_are_refused(self):
        self.assertFalse(self.inbox.act("rocket:1", "launch")[0])


if __name__ == "__main__":
    unittest.main()
