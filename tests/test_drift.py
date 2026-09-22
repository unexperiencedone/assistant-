"""The voice check: what counts as drift, and what is left alone."""

import shutil
import tempfile
import unittest
from pathlib import Path

from assistant.persona import drift


class Faults(unittest.TestCase):
    def test_a_good_reply_has_nothing_wrong_with_it(self):
        self.assertEqual(drift.faults("I'm Nova. I live on this laptop and your phone."), [])

    def test_a_borrowed_introduction_is_caught(self):
        self.assertIn("introduced itself as something other than Nova",
                      drift.faults("I'm Claude, an AI assistant."))

    def test_denying_the_stack_is_caught_because_it_is_a_lie(self):
        found = drift.faults("I don't use any outside AI. Everything runs here.")
        self.assertTrue(any("denied what it is built on" in f for f in found))

    def test_an_honest_answer_about_the_stack_is_left_alone(self):
        honest = "Kaiketsu Tech builds me on models from a few providers."
        self.assertEqual(drift.faults(honest), [])

    def test_blaming_the_plumbing_is_caught(self):
        self.assertIn("blamed the plumbing instead of owning the failure",
                      drift.faults("The API returned an error, please try again."))

    def test_flattery_is_caught(self):
        self.assertIn("opened with flattery", drift.faults("Great question! The torch is on."))

    def test_third_person_is_caught(self):
        self.assertIn("talked about itself in the third person",
                      drift.faults("The system will attempt the call again."))

    def test_a_long_ramble_is_caught(self):
        rambling = " ".join(f"This is sentence number {n}." for n in range(8))
        self.assertTrue(any("ran past" in f for f in drift.faults(rambling)))

    def test_silence_is_a_fault(self):
        self.assertEqual(drift.faults("   "), ["said nothing at all"])


class Checking(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_a_clean_run_scores_one(self):
        run = drift.check(lambda ask: "I'm Nova. I don't know that yet, but I can check.")
        self.assertEqual(run["score"], 1.0)
        self.assertIn("Same voice as before", drift.spoken(run))

    def test_a_drifted_run_names_what_slipped(self):
        run = drift.check(lambda ask: "Certainly! I'm Claude, and the API returned an error.")
        self.assertEqual(run["clean"], 0)
        said = drift.spoken(run)
        self.assertIn("0 of", said)

    def test_a_backend_that_throws_is_a_fault_not_a_crash(self):
        def explode(ask):
            raise RuntimeError("no backend")

        run = drift.check(explode)
        self.assertEqual(run["clean"], 0)
        self.assertTrue(all(r["faults"] for r in run["results"]))

    def test_runs_accumulate_so_drift_is_visible_over_time(self):
        path = self.folder / "drift.json"
        drift.record(path, drift.check(lambda ask: "I'm Nova."))
        comparison = drift.record(path, drift.check(lambda ask: "Certainly! I'm Claude."))
        self.assertIsNotNone(comparison["previous"])
        self.assertLess(comparison["change"], 0)


if __name__ == "__main__":
    unittest.main()
