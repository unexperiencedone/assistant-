"""Two bugs from one evening of asking for Gulzar.

1. "gulzar ali" on its own -- a name -- was routed by the classifier to "call {name}",
   and Nova went to phone someone. A request needs something left over once its values
   are taken out.
2. "play gulzar ali songs" searched Spotify and then pressed the first button named
   exactly "Play": the player bar, which resumed the old track. Nova said "Playing gulzar
   ali songs" without ever checking. The live Spotify names below are from that window.
"""

import unittest
from unittest import mock

from assistant.automation import desktop
from assistant.classify import validate
from assistant.matching import slots_are_sane

CORPUS = [("intent", "phone_call_name", "call {name}"), ("automation", "Play something on Spotify", "play {query}")]

SPOTIFY_BUTTONS = [
    "Liked Songs Pinned Playlist • insane",
    "Play Album • Ed Sheeran",            # an album called "Play", in the sidebar
    "Play",                                 # the player bar, paused
    "Play Gulzar - old songs",             # Spotify's top result
    "Play Zindagi Gulzar Hai - Written by Ali Zafar",
    "Play Ghulam Ali",
]


class ANameIsNotARequest(unittest.TestCase):
    def test_a_bare_name_is_not_a_call(self):
        self.assertFalse(slots_are_sane("gulzar ali", "call {name}", {"name": "gulzar ali"}))
        guess = validate({"script": "phone_call_name", "args": {"name": "gulzar ali"}, "confidence": 0.9},
                         CORPUS, said="gulzar ali")
        self.assertIsNone(guess)

    def test_politeness_or_the_wake_word_does_not_make_it_one(self):
        for said in ("please gulzar ali", "nova gulzar ali"):
            self.assertFalse(slots_are_sane(said, "call {name}", {"name": "gulzar ali"}), said)

    def test_a_reworded_request_still_routes(self):
        self.assertTrue(slots_are_sane("ring my brother", "call {name}", {"name": "brother"}))
        self.assertTrue(slots_are_sane("bollywood news", "{topic} news", {"topic": "bollywood"}))


class TheSearchResultNotThePlayerBar(unittest.TestCase):
    def test_spotifys_top_result_wins(self):
        self.assertEqual(desktop.best_match(SPOTIFY_BUTTONS, "gulzar ali songs", "Play ", " • "),
                         "Play Gulzar - old songs")

    def test_the_bar_and_the_sidebar_never_win(self):
        self.assertIsNone(desktop.best_match(["Play", "Play Album • Ed Sheeran"], "ed sheeran", "Play ", " • "))

    def test_no_words_to_go_on_means_nothing_clicked(self):
        """ "play the song" left no words, and the first card was a stale earlier result."""
        self.assertIsNone(desktop.best_match(SPOTIFY_BUTTONS, "the song", "Play ", " • "))

    def test_nothing_related_means_nothing_clicked(self):
        self.assertIsNone(desktop.best_match(SPOTIFY_BUTTONS, "arijit", "Play ", " • "))


class ASpokenNameIsSpeltLoosely(unittest.TestCase):
    """ "play tera didarva": Spotify spells it "didar", "didarwa", "deedar"."""

    FRESH = ["Play", "Play Album • Ed Sheeran", "Play Tera didar hua", "Play Tere Darwaze Pe Ek Arzi"]

    def test_close_spellings_count(self):
        self.assertEqual(desktop.best_match(self.FRESH, "tera didarva", "Play ", " • "), "Play Tera didar hua")
        self.assertEqual(desktop.best_match(["Play Didarwa Chamako"], "didarva", "Play ", " • "), "Play Didarwa Chamako")

    def test_on_a_fresh_page_the_top_result_beats_failing(self):
        names = ["Play", "Play Something Completely Different"]
        self.assertIsNone(desktop.best_match(names, "tera didarva", "Play ", " • "))
        self.assertEqual(desktop.best_match(names, "tera didarva", "Play ", " • ", fallback_first=True),
                         "Play Something Completely Different")

    def test_a_search_link_that_was_ignored_is_sent_again(self):
        reopened = []  # Spotify keeps the old search until the link is sent again
        with mock.patch.object(desktop, "read", side_effect=lambda *a: "tera didarva" if reopened else "didarwa"), \
                mock.patch.object(desktop.time, "sleep"):
            value = desktop.wait_value("exe:spotify", "What do you want to play?", "Tera Didarva",
                                       timeout=0.05, reopen=lambda: reopened.append(1))
        self.assertEqual(value, "tera didarva")
        self.assertEqual(reopened, [1])


class AfterAnAutomationFails(unittest.TestCase):
    def test_keep_that_optimization_is_heard_as_approval(self):
        from assistant.intents import match_intent

        self.assertEqual(match_intent("Keep that Optimization").name, "approve_automation")

    def test_a_finished_run_says_what_it_did(self):
        """So a model running it as a tool knows it is done and doesn't start the song again."""
        from pathlib import Path

        from assistant.automation.macros import Macro, MacroRunner
        from assistant.events import EventBus

        macro = Macro(name="Play", description="", phrases=["play {query}"],
                      steps=[{"do": "say", "text": "Playing {query}."}], path=Path("play.toml"))
        ok, summary = MacroRunner(EventBus(), open_app=lambda n: True, say=lambda t: None).run(macro, {"query": "x"})
        self.assertTrue(ok)
        self.assertIn('It said: "Playing x."', summary)
        self.assertIn("don't repeat it", summary)


class SuccessIsWhatPlays(unittest.TestCase):
    def window(self, *titles):
        titles = list(titles)
        win = mock.Mock()
        win.window_text.side_effect = lambda: titles.pop(0) if len(titles) > 1 else titles[0]
        return mock.patch.object(desktop, "find_window", return_value=win)

    def test_the_new_track_is_reported(self):
        with self.window("Spotify Premium", "Spotify Premium", "Lata Mangeshkar - Tujhse Naraz Nahin Zindagi"):
            self.assertEqual(desktop.wait_title("exe:spotify", "Spotify Premium", r"^Spotify( Premium| Free)?$", 3),
                             "Lata Mangeshkar - Tujhse Naraz Nahin Zindagi")

    def test_nothing_starting_is_a_failure_not_a_claim(self):
        with self.window("Spotify Premium"):
            with self.assertRaises(desktop.AutomationError):
                desktop.wait_title("exe:spotify", "Spotify Premium", r"^Spotify( Premium| Free)?$", 0.5)


if __name__ == "__main__":
    unittest.main()
