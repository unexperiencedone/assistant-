"""The publishing gate: what it stages, what it refuses, and what it admits went wrong."""

import shutil
import tempfile
import unittest
from pathlib import Path

from assistant.events import EventBus
from assistant.publish import PublishGate, PublishService, describe


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.bus = EventBus()
        self.announced = []
        self.bus.subscribe("publish", lambda e: self.announced.append(e.data))
        self.gate = PublishGate(self.folder / "publish.db", self.bus)

    def tearDown(self):
        self.gate.close()
        shutil.rmtree(self.folder, ignore_errors=True)


class Staging(Base):
    def test_staging_sends_nothing(self):
        sent = []
        self.gate.register("linkedin_post", lambda draft: (sent.append(draft) or (True, "sent")))
        self.gate.stage("linkedin_post", "Shipped the journal today.")
        self.assertEqual(sent, [])
        self.assertEqual(len(self.gate.pending()), 1)

    def test_a_draft_survives_a_restart(self):
        self.gate.stage("linkedin_post", "still here tomorrow")
        self.gate.close()
        reopened = PublishGate(self.folder / "publish.db", self.bus)
        self.assertEqual(len(reopened.pending()), 1)
        self.assertIn("still here tomorrow", reopened.pending()[0]["text"])
        reopened.close()

    def test_the_description_is_judgeable_without_opening_anything(self):
        draft = self.gate.stage("instagram_post", "a caption", target="your account",
                                media=["a.mp4"])
        line = describe(draft)
        self.assertIn("Instagram", line)
        self.assertIn("1 file", line)
        self.assertIn("a caption", line)


class Approving(Base):
    def test_approval_sends_exactly_one_draft(self):
        sent = []
        self.gate.register("linkedin_post", lambda draft: (sent.append(draft["text"]) or (True, "Posted.")))
        self.gate.stage("linkedin_post", "first")
        self.gate.stage("linkedin_post", "second")
        ok, detail = self.gate.approve()
        self.assertTrue(ok)
        self.assertEqual(sent, ["first"])
        self.assertEqual(len(self.gate.pending()), 1)

    def test_a_draft_cannot_be_sent_twice(self):
        self.gate.register("linkedin_post", lambda draft: (True, "Posted."))
        draft = self.gate.stage("linkedin_post", "once only")
        self.gate.approve(draft["id"])
        ok, detail = self.gate.approve(draft["id"])
        self.assertFalse(ok)
        self.assertIn("already sent", detail)

    def test_a_platform_refusal_is_recorded_as_a_failure_not_a_send(self):
        self.gate.register("linkedin_post", lambda draft: (False, "LinkedIn said 401"))
        draft = self.gate.stage("linkedin_post", "nope")
        ok, detail = self.gate.approve(draft["id"])
        self.assertFalse(ok)
        self.assertIn("401", detail)
        self.assertEqual(self.gate.get(draft["id"])["status"], "failed")

    def test_a_sender_that_throws_is_a_failure_not_a_send(self):
        def explode(draft):
            raise RuntimeError("no network")

        self.gate.register("linkedin_post", explode)
        draft = self.gate.stage("linkedin_post", "boom")
        ok, detail = self.gate.approve(draft["id"])
        self.assertFalse(ok)
        self.assertEqual(self.gate.get(draft["id"])["status"], "failed")
        self.assertIn("no network", detail)

    def test_a_draft_with_no_sender_stays_put(self):
        draft = self.gate.stage("linkedin_post", "nothing wired up")
        ok, detail = self.gate.approve(draft["id"])
        self.assertFalse(ok)
        self.assertIn("no way to send", detail)

    def test_discarding_leaves_nothing_waiting(self):
        self.gate.register("linkedin_post", lambda draft: (True, "Posted."))
        self.gate.stage("linkedin_post", "on reflection, no")
        self.gate.discard()
        self.assertEqual(self.gate.pending(), [])
        self.assertIn("Nothing is waiting", self.gate.spoken())

    def test_approving_nothing_says_so(self):
        ok, detail = self.gate.approve()
        self.assertFalse(ok)
        self.assertIn("nothing waiting", detail.lower())


class Service(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.service = PublishService(self.folder / "publish.db")

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_drafting_says_plainly_that_nothing_went_out(self):
        said = self.service.draft_linkedin("Shipped the journal today.")
        self.assertIn("Nothing has gone out", said)

    def test_an_arm_with_no_credentials_says_what_is_missing(self):
        state = self.service.readiness()
        self.assertIn("linkedin", state)
        ok, detail = state["linkedin"]
        if not ok:
            self.assertIn("LINKEDIN_ACCESS_TOKEN", detail)

    def test_a_release_without_a_tag_refuses_rather_than_guessing(self):
        draft = self.service.gate.stage("github_release", "some notes", target="me/repo")
        ok, detail = self.service.gate.approve(draft["id"])
        self.assertFalse(ok)
        self.assertIn("no tag", detail)


if __name__ == "__main__":
    unittest.main()
