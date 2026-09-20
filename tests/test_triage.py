"""Which requests are errands (parallel-safe) and which are big jobs."""

import unittest

from assistant.triage import HEAVY, LIGHT, weight


class Triage(unittest.TestCase):
    def check(self, expected, phrases):
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(expected, weight(phrase))

    def test_errands(self):
        self.check(LIGHT, [
            "open the budget spreadsheet",
            "play Bohemian Rhapsody on Spotify",
            "search for the invoice from March",
            "find my resume",
            "where is the presentation I made last week",
            "send an email to Rahul saying I'll be late",
            "show me the Downloads folder",
            "what is twelve times twelve",
            "check the weather",
            "pause the music",
            "switch to Chrome",
            "how many files are in Downloads",
        ])

    def test_big_jobs(self):
        self.check(HEAVY, [
            "write a detailed article about the history of the printing press",
            "tidy the Downloads folder",
            "clean up the duplicates in OneDrive",
            "refactor the audio module so the speaker is easier to test",
            "build me a small website for the club",
            "research the best laptops under fifty thousand rupees and compare them",
            "fix the failing test in the planner",
            "summarize this document",
            "organize my photos by year",
            "install python and set up a virtual environment",
        ])

    def test_an_errand_that_turns_into_a_project(self):
        self.assertEqual(HEAVY, weight("open Word and then write a two page essay about monsoons"))

    def test_executing_a_plan_is_always_heavy(self):
        self.assertEqual(HEAVY, weight("go ahead", executing_plan=True))

    def test_long_rambling_requests_are_heavy(self):
        self.assertEqual(HEAVY, weight(
            "so I need you to look at the folder on my desktop with the college stuff in it and "
            "tell me which of those submissions I still have not finished yet"))

    def test_empty_is_heavy(self):
        self.assertEqual(HEAVY, weight("   "))


if __name__ == "__main__":
    unittest.main(verbosity=2)
