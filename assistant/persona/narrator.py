"""Turning several raw results into one thing Nova would actually say.

This is not a voice-consistency layer, though it was first proposed as one. That argument
was weak: Groq answers most turns and already speaks as Nova from its own system prompt,
so paraphrasing its reply through a second call buys a little polish for a whole extra
round-trip. What it is for is the two places where the text reaching the user was never
written in one voice by anything:

1. **A finished orchestrator group** (`assistant/orchestrate`), whose answer is the raw
   replies of two or three separate tasks stapled together with mechanical sentences
   like "I couldn't finish: X." Nobody would say that out loud. Stitching those into one
   sentence is synthesis, not rephrasing, and no amount of local string handling does it.
2. **A reply from a backend that was never given the character** -- Claude Code, `agy`.
   Those get the guard and a sentence cap today, which is a net, not a voice.

Deliberately *not* applied to instant intents or to Groq's own replies. An intent answers
in microseconds offline; routing "it's twenty past four" through a network call to sound
marginally warmer is the exact trade the cascade exists to refuse.

## Why the facts are checked here rather than asked for

The first sketch of this protected itself with one line of prompt -- "don't add
information that isn't here" -- and nothing else. In this project that has failed three
times: a skill the model would not load when told to, a handover it claimed without
making, a recipe it needed handed to it. A prompt is a convention, not a boundary.

So a narration is *verified* before it is used. Every number and every name in the source
must still be present in the candidate, or the candidate is thrown away and the plain
text stands. This is strict on purpose and will sometimes reject a perfectly good
rewrite, which costs nothing: falling back means the user hears the accurate version
that already existed.

A hallucination introduced here would be worse than one from the backend that produced
the content, because it arrives *after* everything that checks the content.
"""

from __future__ import annotations

import re
from typing import Any, Callable

# Anything with a digit in it: counts, prices, times, versions, ids. Losing or rounding
# one of these is the specific failure this guards against -- "fifteen thousand" in place
# of 15,000 reads fine and is not the same number.
_NUMBERS = re.compile(r"\d[\d,._:/-]*\d|\d")
# Names, products, files and paths: TitleCase or ALLCAPS tokens, and anything with a dot
# in the middle (report.docx, nova_bridge.py).
_NAMES = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b|\b[A-Z]{2,}\b|\b\w+\.\w{2,4}\b")
# Words too ordinary to be worth protecting: they start sentences constantly, and
# requiring them back makes the check reject good rewrites for no reason.
_COMMON = {"the", "this", "that", "there", "then", "they", "and", "but", "you", "your",
           "i", "it", "its", "nova", "okay", "yes", "no", "done", "for", "with", "from",
           "what", "when", "which", "while", "here", "how", "all", "not", "was", "were",
           "claude", "groq", "code"}

ASK = ("Say this to the user as one short spoken answer, in your own voice: two or three "
       "sentences, no lists, no markdown. Join the parts into something a person would "
       "actually say. Keep every number, name and filename exactly as written, and add "
       "nothing that is not here -- if you are unsure of something, leave it out rather "
       "than guessing.\n\nWhat happened:\n{content}")


def facts(text: str) -> set[str]:
    """The tokens a rewrite is not allowed to lose: numbers and names."""
    found = {m.group(0) for m in _NUMBERS.finditer(text or "")}
    for match in _NAMES.finditer(text or ""):
        token = match.group(0)
        if token.lower() not in _COMMON:
            found.add(token)
    return found


def dropped(source: str, candidate: str) -> set[str]:
    """Facts in the source that the candidate failed to carry over."""
    lowered = (candidate or "").lower()
    return {fact for fact in facts(source) if fact.lower() not in lowered}


def acceptable(source: str, candidate: str, max_chars: int = 600) -> tuple[bool, str]:
    """Is this rewrite safe to say instead of the source? With the reason when not."""
    candidate = (candidate or "").strip()
    if not candidate:
        return False, "the narrator said nothing"
    if len(candidate) > max_chars:
        return False, f"it ran to {len(candidate)} characters"
    # Longer than what it was given means it has started explaining rather than saying.
    if len(candidate) > max(200, len(source or "") * 2):
        return False, "it grew instead of condensing"
    missing = dropped(source, candidate)
    if missing:
        return False, "it lost " + ", ".join(sorted(missing)[:4])
    return True, ""


class Narrator:
    """One place that turns raw results into a spoken line, or declines to.

    `ask` is the same free-model callable the journal and the activity record use
    (`app._narrate`): it returns "" when no free backend is configured, which makes every
    path here degrade to the plain text rather than to silence.
    """

    def __init__(self, ask: Callable[[str], str] | None = None, settings: Any = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.ask = ask
        self.settings = settings
        self.log = log

    @property
    def enabled(self) -> bool:
        return bool(self.ask) and bool(getattr(self.settings, "enabled", True))

    def say_it_better(self, source: str, max_chars: int = 600) -> str:
        """The narrated version, or the source unchanged. Never empty if source wasn't.

        Returning the source on every doubt is the whole safety model here: the caller
        cannot end up with less than it started with, so wiring this in can't make an
        answer disappear.
        """
        source = (source or "").strip()
        if not source or not self.enabled:
            return source
        if len(source) < 40:          # already one short sentence; nothing to synthesise
            return source
        try:
            candidate = (self.ask(ASK.format(content=source)) or "").strip()
        except Exception as error:
            self._note(f"narrator failed ({type(error).__name__}); keeping the plain answer")
            return source
        ok, reason = acceptable(source, candidate, max_chars)
        if not ok:
            self._note(f"narration rejected: {reason}")
            return source
        return candidate

    def _note(self, text: str) -> None:
        if self.log:
            self.log(text)
