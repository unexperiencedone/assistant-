"""Capture and edit: where files land, and what the ffmpeg pipeline actually produces.

The render tests make their own two-second clip with ffmpeg's test pattern generator
rather than recording the screen, so they are quick and do not depend on what happens
to be on the display while they run.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from assistant.capture import edit, inbox
from assistant.capture.screen import ScreenRecorder, monitors

HAVE_FFMPEG = edit.available()


def make_clip(path: Path, seconds: int = 2) -> bool:
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size=640x360:rate=25:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True, timeout=120)
    return done.returncode == 0 and path.exists()


class Inbox(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_two_captures_in_the_same_second_do_not_collide(self):
        first = inbox.new_path(self.folder, "screen", ".mp4")
        first.write_bytes(b"x")
        second = inbox.new_path(self.folder, "screen", ".mp4")
        self.assertNotEqual(first, second)

    def test_latest_finds_the_newest_media_file(self):
        (self.folder / "notes.txt").write_text("not media")
        clip = inbox.new_path(self.folder, "screen", ".mp4")
        clip.write_bytes(b"x" * 10)
        self.assertEqual(inbox.latest(self.folder), clip)

    def test_describe_says_a_size_not_a_path(self):
        clip = inbox.new_path(self.folder, "screen", ".mp4")
        clip.write_bytes(b"x" * 2048)
        said = inbox.describe(clip)
        self.assertIn(clip.name, said)
        self.assertNotIn(str(self.folder), said)


class FilterEscaping(unittest.TestCase):
    def test_a_windows_font_path_is_escaped_for_the_filter_parser(self):
        escaped = edit._filter_path("C:/Windows/Fonts/segoeui.ttf")
        self.assertNotIn("C:", escaped)
        self.assertIn("C\\:", escaped)

    def test_backslashes_become_forward_slashes(self):
        self.assertNotIn("\\W", edit._filter_path("C:\\Windows\\Fonts\\segoeui.ttf"))


class Screens(unittest.TestCase):
    def test_every_monitor_has_a_positive_size(self):
        for left, top, width, height in monitors():
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)

    def test_stopping_when_nothing_is_recording_says_so(self):
        folder = Path(tempfile.mkdtemp())
        try:
            ok, detail = ScreenRecorder(folder).stop()
            self.assertFalse(ok)
            self.assertIn("wasn't recording", detail)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_an_impossible_screen_number_is_refused_before_ffmpeg_starts(self):
        folder = Path(tempfile.mkdtemp())
        try:
            recorder = ScreenRecorder(folder)
            if not edit.available():
                self.skipTest("ffmpeg is not installed")
            ok, detail = recorder.start(monitor=99)
            self.assertFalse(ok)
            self.assertIn("isn't one of them", detail)
            self.assertFalse(any(folder.iterdir()))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg and ffprobe are not installed")
class Rendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp())
        cls.clip = cls.folder / "source.mp4"
        if not make_clip(cls.clip):
            raise unittest.SkipTest("ffmpeg could not make a test clip")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_probe_reads_the_real_size_and_length(self):
        info = edit.probe(self.clip)
        self.assertEqual((info["width"], info["height"]), (640, 360))
        self.assertAlmostEqual(info["duration"], 2.0, delta=0.5)

    def test_a_trim_is_shorter_than_what_it_came_from(self):
        ok, said, out = edit.trim(self.clip, self.folder, 0.2, 1.0)
        self.assertTrue(ok, said)
        self.assertLess(edit.probe(out)["duration"], 2.0)

    def test_reframing_produces_the_platform_shape(self):
        ok, said, out = edit.reframe(self.clip, self.folder, "vertical")
        self.assertTrue(ok, said)
        info = edit.probe(out)
        self.assertEqual((info["width"], info["height"]), (1080, 1920))

    def test_an_unknown_shape_is_refused_by_name(self):
        ok, said, out = edit.reframe(self.clip, self.folder, "cinemascope")
        self.assertFalse(ok)
        self.assertIn("vertical, square and landscape", said)

    def test_a_caption_renders_rather_than_failing_on_the_font_path(self):
        ok, said, out = edit.caption(self.clip, self.folder, "Nova: a test caption")
        self.assertTrue(ok, said)
        self.assertGreater(out.stat().st_size, 1024)

    def test_a_thumbnail_is_a_real_image(self):
        ok, said, out = edit.thumbnail(self.clip, self.folder, 0.5)
        self.assertTrue(ok, said)
        self.assertGreater(out.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
