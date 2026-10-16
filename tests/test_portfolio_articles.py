import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from assistant.intents import match_intent
from assistant.publish import articles
from assistant.publish.gate import PublishGate


class BriefTests(unittest.TestCase):
    def test_depth_is_the_brief(self):
        text = articles.brief("AirGated", r"C:\attendance_system", r"C:\portfolio")
        self.assertIn("EVERY documentation page", text)
        self.assertIn("lib/writing/airgated.ts".replace("/", "\\"), text.replace("/", "\\"))
        self.assertIn("npm run build", text)
        self.assertIn('linkedin articles-ready "AirGated"', text)
        self.assertIn("Do NOT commit, push or deploy", text)
        self.assertIn("evidence/", text)

    def test_a_project_not_on_disk_is_cloned(self):
        text = articles.brief("Lightweight emotion detection", None, r"C:\portfolio",
                              repo_url="https://github.com/u/lightweight-emotion-detection")
        self.assertIn("clone https://github.com/u/lightweight-emotion-detection", text)
        self.assertIn('"lightweight-emotion-detection"', text)

    def test_the_post_rides_along(self):
        text = articles.brief("Nova", None, r"C:\portfolio", post_text="I built a voice assistant.")
        self.assertIn("I built a voice assistant.", text)

    def test_repo_url(self):
        self.assertEqual(articles.repo_url("Code: https://github.com/a/b-c.\n#x"), "https://github.com/a/b-c.")
        self.assertEqual(articles.repo_url("no link"), "")


class StageTests(unittest.TestCase):
    def test_the_push_waits_at_the_gate(self):
        folder = Path(tempfile.mkdtemp())
        (folder / ".git").mkdir()
        publisher = SimpleNamespace(gate=PublishGate(folder / "publish.db"))
        said = articles.stage(publisher, folder, "AirGated")
        draft = publisher.gate.pending(5)[0]
        self.assertEqual(draft["kind"], "github_push")
        self.assertEqual(Path(draft["target"]), folder)
        self.assertEqual(draft["extra"]["name"], "AirGated articles")
        self.assertIn("waiting in Studio", said)

    def test_not_a_repo(self):
        folder = Path(tempfile.mkdtemp())
        publisher = SimpleNamespace(gate=PublishGate(folder / "publish.db"))
        self.assertIn("isn't a git repo", articles.stage(publisher, folder, "X"))
        self.assertEqual(publisher.gate.pending(5), [])


class VoiceTests(unittest.TestCase):
    def test_voice(self):
        for said, project in (("write the portfolio articles for airgated", "airgated"),
                              ("rewrite the articles about Ved Code", "Ved Code"),
                              ("write the story and technical pieces for the nutrisafe project", "nutrisafe")):
            with self.subTest(said=said):
                intent = match_intent(said)
                self.assertEqual(intent.name, "portfolio_articles")
                self.assertEqual(intent.args["project"], project)


if __name__ == "__main__":
    unittest.main()
