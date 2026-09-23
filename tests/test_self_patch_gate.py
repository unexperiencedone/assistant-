"""An automation the assistant wrote for itself waits to be looked at.

Every check here comes from a patch the promotion loop actually produced on
23 September 2026, when nothing reviewed what it wrote. Two of the three made things
worse and went straight into tracked files:

- a trigger phrase built out of "you haven't told me proper news", which is a complaint
  about the briefing and would fire at the exact moment the user says it went wrong --
  in a file whose own comment, written earlier by the same loop, explained why not to;
- a first step made optional with an unconditional duplicate added after it, so every
  briefing loaded the page twice. Its own comment admitted that.

The asymmetry that made it obvious: a LinkedIn post needed approval before going out,
and a change to the assistant's own behaviour did not.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant.automation import review, staging
from assistant.intents import match_intent

# Exactly what was written, minus the comments.
THE_BAD_PATCH = """name = "News briefing"
phrases = [
    "pull up some {topic} news",
    "{ignore} havent tell me proper {topic}",
]

[[steps]]
do = "browser_goto"
label = "Open the latest {topic} headlines"
url = "https://www.bing.com/news/search?q={topic_url}"
optional = true

[[steps]]
do = "browser_goto"
label = "Retry opening the {topic} headlines"
url = "https://www.bing.com/news/search?q={topic_url}"
"""

GOOD = """name = "Play song"
phrases = ["play {song} on spotify", "put {song} on spotify"]

[[steps]]
do = "open"
target = "spotify"
"""


class TheRealPatch(unittest.TestCase):
    def test_both_of_its_faults_are_caught(self) -> None:
        found = " | ".join(review.problems(THE_BAD_PATCH, match_intent))
        self.assertIn("complaint, not a request", found)
        self.assertIn("twice every time", found)

    def test_a_reasonable_automation_passes(self) -> None:
        self.assertEqual(review.problems(GOOD, match_intent), [])


class Phrases(unittest.TestCase):
    def test_a_negation_is_never_a_trigger(self) -> None:
        for phrase in ('"you havent told me proper {topic}"', '"dont give me {topic} news"',
                       '"that isnt the {topic} i wanted"', '"never mind the {topic}"'):
            body = f'phrases = [{phrase}]\n[[steps]]\ndo = "open"\ntarget = "x"\n'
            self.assertTrue(any("complaint" in p for p in review.problems(body)), phrase)

    def test_a_phrase_that_is_nearly_all_slots_is_refused(self) -> None:
        body = 'phrases = ["{a} {b}"]\n[[steps]]\ndo = "open"\ntarget = "x"\n'
        self.assertTrue(any("placeholders" in p for p in review.problems(body)))

    def test_a_phrase_an_instant_command_already_answers_is_refused(self) -> None:
        """Two things claiming one sentence is a coin toss at runtime."""
        body = 'phrases = ["open {thing}"]\n[[steps]]\ndo = "open"\ntarget = "{thing}"\n'
        found = review.problems(body, match_intent)
        self.assertTrue(any("already answered instantly" in p for p in found), found)

    def test_no_phrases_at_all_is_refused(self) -> None:
        self.assertTrue(any("no phrases" in p for p in review.problems('[[steps]]\ndo = "open"\n')))


class Steps(unittest.TestCase):
    def test_an_unconditional_duplicate_is_refused(self) -> None:
        body = ('phrases = ["do the thing now"]\n'
                '[[steps]]\ndo = "browser_goto"\nurl = "https://x"\n'
                '[[steps]]\ndo = "browser_goto"\nurl = "https://x"\n')
        self.assertTrue(any("twice every time" in p for p in review.problems(body)))

    def test_a_conditional_retry_is_allowed(self) -> None:
        """A retry is fine; a retry that always runs is not."""
        body = ('phrases = ["do the thing now"]\n'
                '[[steps]]\ndo = "browser_goto"\nurl = "https://x"\noptional = true\n'
                '[[steps]]\ndo = "browser_goto"\nurl = "https://x"\nwhen = "failed"\n')
        self.assertFalse(any("twice every time" in p for p in review.problems(body)), review.problems(body))

    def test_a_slot_no_phrase_provides_is_refused(self) -> None:
        body = 'phrases = ["play something now"]\n[[steps]]\ndo = "open"\ntarget = "{missing}"\n'
        self.assertTrue(any("{missing}" in p for p in review.problems(body)))

    def test_a_step_with_no_action_is_refused(self) -> None:
        body = 'phrases = ["do the thing now"]\n[[steps]]\nlabel = "nothing"\n'
        self.assertTrue(any("does not say what to do" in p for p in review.problems(body)))

    def test_broken_toml_is_reported_as_such(self) -> None:
        self.assertTrue(any("not valid TOML" in p for p in review.problems("phrases = [")))


class Staging(unittest.TestCase):
    """The mechanism: macros.py globs automations/*.toml at the top level only, so a
    proposal in pending/ cannot run until somebody moves it."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / staging.FOLDER).mkdir()
        (self.root / staging.FOLDER / "play_song.toml").write_text(GOOD, encoding="utf-8")
        (self.root / staging.FOLDER / "news_briefing.toml").write_text(THE_BAD_PATCH, encoding="utf-8")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_a_proposal_is_not_live(self) -> None:
        self.assertFalse((self.root / "play_song.toml").exists())
        self.assertEqual(sorted(p.name for p in self.root.glob("*.toml")), [])

    def test_it_says_what_is_waiting_and_what_is_wrong(self) -> None:
        said = staging.spoken(self.root, match_intent)
        self.assertIn("2 automations", said)
        self.assertIn("1 of them fine", said)

    def test_approving_a_bad_one_is_refused_and_it_stays_put(self) -> None:
        ok, said = staging.approve(self.root, "news_briefing", match_intent)
        self.assertFalse(ok)
        self.assertIn("complaint", said)
        self.assertFalse((self.root / "news_briefing.toml").exists())
        self.assertTrue((self.root / staging.FOLDER / "news_briefing.toml").exists())

    def test_approving_a_good_one_puts_it_in_service(self) -> None:
        ok, said = staging.approve(self.root, "play_song", match_intent)
        self.assertTrue(ok, said)
        self.assertTrue((self.root / "play_song.toml").exists())
        self.assertFalse((self.root / staging.FOLDER / "play_song.toml").exists())

    def test_rejecting_removes_it(self) -> None:
        ok, _said = staging.reject(self.root, "news_briefing")
        self.assertTrue(ok)
        self.assertFalse((self.root / staging.FOLDER / "news_briefing.toml").exists())

    def test_without_a_name_the_newest_is_meant(self) -> None:
        ok, said = staging.approve(self.root, matches_intent=match_intent)
        # The newest here is the bad one, so this must refuse rather than pick the
        # convenient one instead.
        self.assertIn(("complaint" if not ok else "Saved"), said)

    def test_an_unknown_name_is_said_so(self) -> None:
        ok, said = staging.approve(self.root, "no_such_thing", match_intent)
        self.assertFalse(ok)
        self.assertIn("no_such_thing", said)

    def test_nothing_waiting_is_not_an_error(self) -> None:
        empty = Path(tempfile.mkdtemp())
        self.assertEqual(staging.spoken(empty), "")
        self.assertFalse(staging.approve(empty)[0])

    def test_a_proposal_name_cannot_escape_the_folder(self) -> None:
        """The name comes from a task type a model chose, so it is not trusted."""
        for name in ("../../evil", "..\\evil", "with/slash", "", "x" * 80):
            self.assertIsNone(staging.path_for(self.root, name), name)
        self.assertIsNotNone(staging.path_for(self.root, "play_song"))


class ThePrompts(unittest.TestCase):
    """What the loop is told to do now."""

    def test_writing_a_new_automation_proposes_it(self) -> None:
        from assistant.automation import promotion

        prompt = promotion.write_prompt("play_song", "play some jazz")
        self.assertIn("automations/pending/play_song.toml", prompt)
        self.assertIn("do not edit any source file", prompt)
        self.assertIn("approves", prompt)

    def test_repairing_one_writes_a_copy(self) -> None:
        from assistant.automation import promotion

        prompt = promotion.fix_prompt("News briefing", "automations/news_briefing.toml",
                                      "pull up finance news", 2, "selector not found")
        self.assertIn("automations/pending/news_briefing.toml", prompt)
        self.assertIn("do not edit", prompt)

    def test_both_failures_are_named_so_they_are_not_repeated(self) -> None:
        from assistant.automation import promotion

        fix = promotion.fix_prompt("X", "automations/x.toml", "do x", 1, "boom")
        self.assertIn("repeats the previous one unconditionally", fix)
        self.assertIn("complaint", fix)
        self.assertIn("complaint", promotion.write_prompt("x", "do x"))


if __name__ == "__main__":
    unittest.main()
