import json
import tempfile
import unittest
from pathlib import Path

from assistant.intents import match_intent
from assistant.publish import video, youtube
from assistant.publish.gate import PublishGate


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "film.mp4").write_bytes(b"\0" * 16)
        (self.folder / "thumb.png").write_bytes(b"\0" * 16)
        self.data = {"project": "AirGated", "video": "film.mp4", "thumbnail": "thumb.png",
                     "title": "I Built an Offline Attendance System", "description": "How it works.",
                     "chapters": [["00:00", "The problem"], ["00:27", "Older approaches"]],
                     "tags": ["edtech", " ", "wifi"], "visibility": "public"}

    def write(self, **changes):
        path = self.folder / "youtube.json"
        path.write_text(json.dumps({**self.data, **changes}), encoding="utf-8")
        return path

    def test_relative_paths_resolve_next_to_the_package(self):
        package = youtube.load(self.write())
        self.assertEqual(Path(package["video"]), self.folder / "film.mp4")
        self.assertEqual(package["tags"], ["edtech", "wifi"])

    def test_chapters_go_into_the_description(self):
        text = youtube.full_description(youtube.load(self.write()))
        self.assertIn("Chapters\n00:00 The problem\n00:27 Older approaches", text)

    def test_chapters_must_start_at_zero(self):
        text = youtube.full_description(youtube.load(self.write(chapters=[["00:27", "Later"]])))
        self.assertIn("00:00 Intro\n00:27 Later", text)

    def test_what_youtube_would_refuse_is_refused_here(self):
        for changes in ({"title": "x" * 101}, {"title": "a <b>"}, {"title": ""},
                        {"video": "missing.mp4"}, {"visibility": "friends"},
                        {"description": "y" * 5001}):
            with self.subTest(changes=list(changes)), self.assertRaises(youtube.PackageError):
                youtube.load(self.write(**changes))

    def test_staging_waits_at_the_gate(self):
        gate = PublishGate(self.folder / "publish.db")
        draft = youtube.stage(gate, self.write())
        self.assertEqual(draft["kind"], "youtube_video")
        self.assertEqual(draft["status"], "pending")
        self.assertEqual(Path(draft["media"][0]).name, "film.mp4")
        self.assertEqual(draft["extra"]["name"], "AirGated")


class BriefTests(unittest.TestCase):
    def test_the_brief_names_the_project_the_outputs_and_the_limits(self):
        text = video.brief("Civic Pulse", r"C:\proj\civicpulse", r"C:\video-comp", concurrency=2)
        self.assertIn(r"C:\proj\civicpulse", text)
        self.assertIn("CivicPulseExplained", text)
        self.assertIn("--concurrency=2", text)
        self.assertIn(r"out\civic-pulse\youtube.json", text.replace("/", "\\"))
        self.assertIn("youtube stage", text)
        self.assertIn("Do NOT upload", text)
        self.assertIn("evidence/", text)

    def test_voice(self):
        for said, project in (("make a video about airgated", "airgated"),
                              ("make a youtube video about my project AirGated", "AirGated"),
                              ("create an explainer video on Nova for youtube", "Nova"),
                              ("make a video about the civic pulse project", "civic pulse")):
            with self.subTest(said=said):
                intent = match_intent(said)
                self.assertEqual(intent.name, "video_project")
                self.assertEqual(intent.args["project"], project)


if __name__ == "__main__":
    unittest.main()
