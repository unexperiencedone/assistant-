"""Projects -> LinkedIn: what gets claimed, what gets escaped, and what counts as posted."""

import csv
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from assistant.intents import match_intent
from assistant.publish import PublishService, linkedin, linkedin_auth, projects
from assistant.publish.ledger import Ledger
from assistant.publish.showcase import Showcase, compose, parse


def settings(roots, **over):
    base = dict(roots=[str(r) for r in roots], ignore=["client*"], depth=2,
                ledger_path="ledger.json", auto_draft=False, check_hours=12, min_gap_days=4,
                max_images=4, hashtags=True)
    base.update(over)
    return SimpleNamespace(**base)


class Folder(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def project(self, name, readme):
        folder = self.root / name
        folder.mkdir(parents=True)
        (folder / "README.md").write_text(readme, encoding="utf-8")
        return folder


class Reading(Folder):
    def test_lists_come_from_the_readme_only(self):
        folder = self.project("tasker", "# Tasker\n\nA tiny task tracker for people who hate task trackers.\n\n"
                                        "## Features\n- Add tasks\n- Food input:\n- [x] Sync\n- [ ] Sharing\n\n"
                                        "## Roadmap\n- Mobile app\n")
        project = projects.read(folder)
        self.assertEqual(project.built, ["Sync", "Add tasks"])     # "Food input:" is a label, not a feature
        self.assertEqual(project.partial, ["Sharing"])
        self.assertEqual(project.future, ["Mobile app"])
        self.assertIn("task tracker", project.description)

    def test_secrets_and_emails_never_reach_a_draft(self):
        folder = self.project("leaky", "# Leaky\n\nA demo that calls an API with a long enough description.\n\n"
                                       "api_key = sk-abcdefghijklmnopqrstuvwxyz123456\nMail me: me@example.com\n")
        project = projects.read(folder)
        self.assertNotIn("sk-abcdefghijklmnop", project.readme)
        self.assertNotIn("me@example.com", project.readme)

    def test_a_scaffold_readme_is_not_a_project_description(self):
        folder = self.project("vite-app", "# React + Vite\n\nThis template provides a minimal setup to get React working.")
        self.assertFalse(projects.worth_posting(projects.read(folder))[0])

    def test_folders_inside_a_repo_are_part_of_it(self):
        repo = self.project("big", "# Big\n\nA large project with its own sub folders and docs.")
        (repo / ".git").mkdir()
        (repo / "docs").mkdir()
        (repo / "docs" / "README.md").write_text("# Docs\n\nDocumentation for the big project here.")
        self.project("clientsite", "# Client\n\nSomeone else's work that must never be read.")
        found = [p.name for p in projects.discover([str(self.root)], ["client*"])]
        self.assertEqual(found, ["big"])


class Writing(unittest.TestCase):
    def project(self, **over):
        values = dict(key="k", name="Tasker", path="C:/x", description="A task tracker.",
                      languages=["Python"], built=["Sync"], bugs=[])
        values.update(over)
        return projects.Project(**values)

    def test_empty_sections_are_left_out_not_invented(self):
        text = compose(self.project(), {})
        self.assertIn("Built:\n- Sync", text)
        self.assertNotIn("Next:", text)
        self.assertNotIn("Partly there:", text)
        self.assertNotIn("What broke", text)

    def test_the_model_cannot_add_a_bug_story_the_history_lacks(self):
        phrased = parse("HOOK: Hi\nWHAT: It tracks.\nHOW: Python.\nBUGS:\n- Fixed a race", self.project())
        self.assertEqual(phrased["bugs"], [])

    def test_merge_noise_is_not_a_bug_story(self):
        phrased = parse("BUGS:\n- Resolved merge conflicts\n- Fixed the cache", self.project(bugs=["fix cache"]))
        self.assertEqual(phrased["bugs"], ["Fixed the cache"])

    def test_a_team_project_is_written_as_we(self):
        self.assertTrue(compose(self.project(contributors=3), {}).startswith("We built Tasker."))

    def test_collaborators_are_credited_by_share(self):
        team = [{"name": "Anant", "github": "AnantTirupati", "commits": 11, "share": 0.79, "areas": ["server"]},
                {"name": "sam", "github": "", "commits": 1, "share": 0.02, "areas": []}]
        text = compose(self.project(contributors=2, team=team), {})
        self.assertIn("Built with:\n- Anant (github.com/AnantTirupati): 11 commits -- mostly server", text)
        self.assertIn("Thanks to sam for contributing.", text)
        self.assertNotIn("@", text.replace("#", ""))   # no emails, no fake mentions

    def test_little_text_escapes_markup_but_keeps_hashtags(self):
        out = linkedin.little_text("Built (fast) with C# @home #Python")
        self.assertIn("\\(fast\\)", out)
        self.assertIn("C\\#", out)
        self.assertIn("\\@home", out)
        self.assertIn("{hashtag|\\#|Python}", out)


class LedgerAndGate(Folder):
    def test_seeding_from_the_export_marks_what_was_posted(self):
        tasker = projects.read(self.project("tasker", "# Tasker\n\nA tiny task tracker for people who hate them."))
        other = projects.read(self.project("weather-bot", "# Weather bot\n\nTells you the weather every morning."))
        shares = self.root / "Shares.csv"
        with open(shares, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, ["Date", "ShareLink", "ShareCommentary", "SharedUrl"])
            writer.writeheader()
            writer.writerow({"Date": "2026-05-01 10:00:00", "ShareCommentary": "Shipped Tasker today!"})
        ledger = Ledger(self.root / "ledger.json")
        self.assertEqual(ledger.seed_from_export(shares, [tasker, other]), ["Tasker"])
        self.assertTrue(ledger.covered(tasker.key))
        self.assertFalse(ledger.covered(other.key))
        self.assertIsNone(ledger.days_since_last())   # an export entry doesn't pace new posts

    def test_the_ledger_is_written_only_after_linkedin_confirms(self):
        self.project("tasker", "# Tasker\n\nA tiny task tracker for people who hate them.")
        publisher = PublishService(self.root / "publish.db")
        showcase = Showcase(settings([self.root]), publisher, self.root)
        publisher.showcase = showcase
        original = linkedin.sender
        try:
            showcase.draft()
            draft = publisher.gate.pending()[0]
            self.assertEqual(draft["extra"]["name"], "Tasker")   # the README title, not the folder
            linkedin.sender = lambda d: (False, "LinkedIn said 500")
            publisher.approve()
            self.assertEqual(showcase.ledger.load(), {})
            showcase.draft()
            linkedin.sender = lambda d: (True, "Posted to LinkedIn (urn:li:share:1).")
            publisher.approve()
            entry = next(iter(showcase.ledger.load().values()))
            self.assertEqual(entry["post"], "urn:li:share:1")
            self.assertIn("already on LinkedIn", showcase.draft("tasker"))
        finally:
            linkedin.sender = original
            publisher.close()


class Scheduling(Folder):
    def test_a_scheduled_post_goes_out_once_and_only_when_due(self):
        publisher = PublishService(self.root / "publish.db")
        sent = []
        original = linkedin.sender
        linkedin.sender = lambda d: (sent.append(d["id"]) or (True, "Posted to LinkedIn (urn:li:share:9)."))
        try:
            draft = publisher.gate.stage("linkedin_post", "Thursday's post")
            ok, said = publisher.gate.schedule(draft["id"], time.time() + 3600)
            self.assertTrue(ok)
            self.assertEqual(publisher.gate.send_due(), [])            # not yet
            self.assertEqual(publisher.gate.pending(), [])             # approved, so not "waiting on you"
            publisher.gate.send_due(now=time.time() + 7200)
            publisher.gate.send_due(now=time.time() + 9000)            # a second tick never resends
            self.assertEqual(sent, [draft["id"]])
            self.assertEqual(publisher.gate.get(draft["id"])["status"], "sent")
        finally:
            linkedin.sender = original
            publisher.close()

    def test_unscheduling_puts_it_back_in_your_hands(self):
        publisher = PublishService(self.root / "publish.db")
        try:
            draft = publisher.gate.stage("linkedin_post", "maybe later")
            publisher.gate.schedule(draft["id"], time.time() + 3600)
            publisher.gate.unschedule(draft["id"])
            self.assertEqual(publisher.gate.get(draft["id"])["status"], "pending")
        finally:
            publisher.close()


class Token(Folder):
    def test_an_expired_stored_token_is_not_used(self):
        path = self.root / "token.json"
        path.write_text(json.dumps({"access_token": "old", "expires_at": time.time() - 10}))
        self.assertNotEqual(linkedin_auth.current(path), "old")
        path.write_text(json.dumps({"access_token": "new", "expires_at": time.time() + 86400 * 30}))
        self.assertEqual(linkedin_auth.current(path), "new")


class Voice(unittest.TestCase):
    def test_phrases(self):
        self.assertEqual(match_intent("post my new project").name, "showcase_draft")
        self.assertEqual(match_intent("post nutrisafe on linkedin").args, {"project": "nutrisafe"})
        self.assertEqual(match_intent("which projects haven't I posted").name, "showcase_unposted")
        self.assertEqual(match_intent("post it").name, "publish_approve")


if __name__ == "__main__":
    unittest.main()
