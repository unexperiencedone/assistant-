"""When an answer is a document, and what gets said out loud instead.

The failure this prevents is silent: `spoken_reply` keeps the first three sentences and
drops the rest, so a piece of research was being truncated with nothing to say it had
happened. The tests that matter are therefore the boundaries -- what does *not* earn a
window over the user's work, and what must never be lost.
"""

from __future__ import annotations

import unittest

from assistant import reader

REPORT = """# Aux versus Bluetooth volume

## Summary
Aux output is line level and unamplified.

## Why it happens
| Path | Level |
|:--|:--|
| Aux | 0.3 V |
| Bluetooth | amplified |

Figure 1: Signal path — the amplifier is the whole difference.
"""

WITH_DIAGRAM = """## How it routes

```mermaid
flowchart LR
    A[Mic] --> B[Whisper]
```

The gate sits between them and drops anything Whisper was guessing at.
""" + ("Detail. " * 30)


class NotDocuments(unittest.TestCase):
    """Opening a window over someone's work is an interruption; it has to be earned."""

    def test_a_spoken_answer_is_not(self) -> None:
        self.assertFalse(reader.is_document("The torch is on."))

    def test_a_heading_over_two_sentences_is_not(self) -> None:
        self.assertFalse(reader.is_document("## Findings\n\nOne thing happened. Another did too."))

    def test_rambling_prose_is_not_until_it_is_long(self) -> None:
        """No structure means no deliberate layout, so the bar is sheer length."""
        self.assertFalse(reader.is_document("and then " * 100))       # ~900 chars
        self.assertTrue(reader.is_document("and then " * 260))        # past long_chars

    def test_empty_is_not(self) -> None:
        self.assertFalse(reader.is_document(""))
        self.assertFalse(reader.is_document(None))


class AreDocuments(unittest.TestCase):
    def test_a_report_with_a_table_and_a_figure(self) -> None:
        """Laid out deliberately, so it qualifies well short of the length a rambling
        answer would need."""
        self.assertLess(len(REPORT), reader.MIN_CHARS)
        self.assertTrue(reader.is_document(REPORT))

    def test_a_piece_with_a_mermaid_diagram(self) -> None:
        self.assertTrue(reader.is_document(WITH_DIAGRAM))

    def test_headings_plus_real_length(self) -> None:
        self.assertTrue(reader.is_document("## Findings\n\n" + ("A full sentence here. " * 40)))


class Signals(unittest.TestCase):
    def test_it_can_explain_itself(self) -> None:
        found = reader.signals(REPORT)
        self.assertTrue(found["headings"])
        self.assertTrue(found["table"])
        self.assertTrue(found["figures"])
        self.assertFalse(found["diagram"])

    def test_a_mermaid_fence_counts_as_a_diagram_not_just_code(self) -> None:
        found = reader.signals(WITH_DIAGRAM)
        self.assertTrue(found["diagram"])
        self.assertTrue(found["code"])


class Titles(unittest.TestCase):
    def test_the_first_heading_becomes_the_window_title(self) -> None:
        self.assertEqual(reader.title_of(REPORT), "Aux versus Bluetooth volume")

    def test_without_a_heading_the_first_real_line_is_used(self) -> None:
        title = reader.title_of("Short.\nA line long enough to name the thing by.")
        self.assertEqual(title, "A line long enough to name the thing by.")

    def test_it_falls_back_rather_than_returning_nothing(self) -> None:
        self.assertEqual(reader.title_of("", "Nova"), "Nova")


class Gist(unittest.TestCase):
    """What the voice says while the document is on screen."""

    def test_table_rows_are_never_read_aloud(self) -> None:
        said = reader.gist(REPORT)
        self.assertNotIn("|", said)
        self.assertNotIn("0.3 V", said)

    def test_diagram_source_is_never_read_aloud(self) -> None:
        said = reader.gist(WITH_DIAGRAM)
        self.assertNotIn("flowchart", said)
        self.assertNotIn("-->", said)

    def test_headings_do_not_run_into_the_prose(self) -> None:
        """Keeping heading words produced "Aux versus Bluetooth volume Summary Aux
        output is..." -- the title is already on the window."""
        said = reader.gist(REPORT)
        self.assertNotIn("Summary Aux", said)
        self.assertIn("line level", said)

    def test_it_says_something_for_any_document(self) -> None:
        for text in (REPORT, WITH_DIAGRAM, "and then " * 260):
            self.assertTrue(reader.gist(text).strip(), "a document must have a spoken line")

    def test_it_stays_short_enough_to_speak(self) -> None:
        self.assertLessEqual(len(reader.gist("A sentence that goes on. " * 60)), 305)


class Thresholds(unittest.TestCase):
    def test_they_are_overridable(self) -> None:
        """The controller passes them from config, so a user who finds the window
        intrusive can raise the bar rather than turning it off."""
        text = "## Heading\n\n" + ("Sentence here. " * 60)
        self.assertTrue(reader.is_document(text))
        self.assertFalse(reader.is_document(text, strong_chars=9000, min_chars=9000,
                                            long_chars=9000, unspeakable_chars=9000))


if __name__ == "__main__":
    unittest.main()
