"""The narrator, and the thing that makes it safe: its output is checked, not trusted.

A hallucination introduced here would be worse than one from the backend that produced
the content, because it arrives *after* everything that checks the content. So the tests
that matter are the rejections.
"""

from __future__ import annotations

import unittest

from assistant.orchestrate import Orchestrator
from assistant.persona.narrator import Narrator, acceptable, dropped, facts

SOURCE = ("Five files changed in nova_bridge.py.\n"
          "Two PRs are open.\n"
          "The invoice came to 15,000 rupees.")


def narrator(reply: str, **flags) -> Narrator:
    settings = type("S", (), {"enabled": True, **flags})()
    return Narrator(ask=lambda _prompt: reply, settings=settings)


class Facts(unittest.TestCase):
    def test_numbers_and_names_are_protected(self) -> None:
        found = facts(SOURCE)
        self.assertIn("15,000", found)
        self.assertIn("nova_bridge.py", found)
        self.assertIn("Five", found)

    def test_ordinary_words_are_not(self) -> None:
        """Requiring "The" back would reject good rewrites for no reason."""
        found = {f.lower() for f in facts("The thing that Claude did was fine.")}
        for word in ("the", "that", "claude"):
            self.assertNotIn(word, found)


class Rejections(unittest.TestCase):
    def test_a_rounded_number_is_rejected(self) -> None:
        """The exact failure the design was warned about: 15,000 -> "fifteen thousand"
        reads perfectly well and is not the same number."""
        ok, why = acceptable(SOURCE, "Five files changed in nova_bridge.py, two PRs are "
                                     "open, and the invoice was around fifteen thousand.")
        self.assertFalse(ok)
        self.assertIn("15,000", why)

    def test_a_dropped_name_is_rejected(self) -> None:
        ok, why = acceptable(SOURCE, "Five files changed, two PRs are open, and the "
                                     "invoice came to 15,000 rupees.")
        self.assertFalse(ok)
        self.assertIn("nova_bridge.py", why)

    def test_an_empty_narration_is_rejected(self) -> None:
        ok, why = acceptable(SOURCE, "   ")
        self.assertFalse(ok)
        self.assertIn("said nothing", why)

    def test_an_expanded_narration_is_rejected(self) -> None:
        """Growing means it started explaining rather than saying. The source here is
        the length that actually reaches this check -- anything under 40 characters is
        left alone earlier, as already one short sentence."""
        source = "Two PRs are open and the build is currently green."
        ok, why = acceptable(source, source + " I should say that this is quite normal "
                             "for a project at this stage, since review always takes "
                             "time and there is a great deal going on across the whole "
                             "repository at the moment, which is worth bearing in mind "
                             "before you look at any of it in detail this evening.")
        self.assertFalse(ok)
        self.assertIn("grew", why)

    def test_a_faithful_rewrite_is_accepted(self) -> None:
        ok, why = acceptable(SOURCE, "Five files changed in nova_bridge.py, two PRs are "
                                     "open, and the invoice came to 15,000 rupees.")
        self.assertTrue(ok, why)


class NeverLessThanItStarted(unittest.TestCase):
    """The whole safety model: the caller cannot end up with less than it had."""

    def test_a_rejected_narration_keeps_the_plain_text(self) -> None:
        said = narrator("around fifteen thousand rupees, roughly").say_it_better(SOURCE)
        self.assertEqual(said, SOURCE)

    def test_a_crash_keeps_the_plain_text(self) -> None:
        def explode(_prompt: str) -> str:
            raise RuntimeError("groq is down")

        voice = Narrator(ask=explode, settings=type("S", (), {"enabled": True})())
        self.assertEqual(voice.say_it_better(SOURCE), SOURCE)

    def test_no_backend_keeps_the_plain_text(self) -> None:
        self.assertEqual(Narrator(ask=None).say_it_better(SOURCE), SOURCE)

    def test_a_short_answer_is_left_alone(self) -> None:
        """Nothing to synthesise, so not worth a network call."""
        voice = narrator("I have rephrased this needlessly.")
        self.assertEqual(voice.say_it_better("Torch is on."), "Torch is on.")

    def test_disabled_keeps_the_plain_text(self) -> None:
        voice = Narrator(ask=lambda _p: "rephrased",
                         settings=type("S", (), {"enabled": False})())
        self.assertEqual(voice.say_it_better(SOURCE), SOURCE)


class WiredToGroups(unittest.TestCase):
    """Where it is actually used: a finished multi-step group, whose plain answer is
    several backends' replies stapled together."""

    @staticmethod
    def _dispatch(prompt: str, _label: str) -> str:
        return f"t{abs(hash(prompt)) % 1000}"

    def test_a_group_answer_is_narrated(self) -> None:
        calls: list[str] = []

        def narrate(plain: str) -> str:
            calls.append(plain)
            return "Five files changed and two PRs are open."

        orch = Orchestrator(self._dispatch, narrate=narrate)
        group = orch.start("two things", [("a", []), ("b", [])])
        ids = [s.task_id for s in group.steps]
        orch.finished(ids[0], True, "Five files changed.")
        answer = orch.finished(ids[1], True, "Two PRs are open.")
        self.assertEqual(answer, "Five files changed and two PRs are open.")
        self.assertIn("Five files changed.", calls[0])

    def test_a_single_step_group_is_not_narrated(self) -> None:
        """One step is one backend's own words already; nothing to stitch."""
        calls: list[str] = []
        orch = Orchestrator(self._dispatch, narrate=lambda p: calls.append(p) or "reworded")
        group = orch.start("one thing", [("only", [])])
        answer = orch.finished(group.steps[0].task_id, True, "It is done, in detail, at length.")
        self.assertEqual(calls, [])
        self.assertIn("in detail", answer)

    def test_a_narrator_returning_nothing_falls_back(self) -> None:
        orch = Orchestrator(self._dispatch, narrate=lambda _p: "")
        group = orch.start("two", [("a", []), ("b", [])])
        ids = [s.task_id for s in group.steps]
        orch.finished(ids[0], True, "First answer.")
        answer = orch.finished(ids[1], True, "Second answer.")
        self.assertIn("First answer.", answer)
        self.assertIn("Second answer.", answer)


if __name__ == "__main__":
    unittest.main()
