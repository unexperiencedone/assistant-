"""Decide whether a captured phrase is a real request before anything acts on it.

Garbage requests come from three places: Whisper inventing text in noise or
silence (subtitle phrases like "see you in the next video"), background audio
(a TV, a video, other people talking), and half-heard mumbles. Each layer below
is cheap and catches a different one:

  1. acoustic confidence   Whisper's own scores: a low average log-probability
                           means it was guessing; a high compression ratio means
                           the "text" is one phrase looping
  2. text shape            known hallucination phrases, one word or sentence
                           repeated, too little actual language
  3. addressing            the wake word at the start ("hey Nova, ..."); checked
                           by the controller, which knows when a reply is expected

Typed input never passes through here: only the microphone is noisy.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Transcript:
    text: str
    avg_logprob: float | None = None        # duration-weighted over kept segments; None = engine has no score
    compression_ratio: float | None = None  # worst segment
    duration: float = 0.0                   # seconds of speech Whisper kept


# Whisper was trained on subtitles, so in noise it "hears" the lines videos end with.
_HALLUCINATIONS = re.compile(
    r"\b(?:thanks?(?: you)? (?:so much )?for watching"
    r"|see you (?:in|on) (?:the |a )?(?:next|few|minute|bit|other side)"
    r"|(?:like and |please )?subscribe(?: to)?"
    r"|subtitles? (?:by|created)|captions? by|amara\.org|transcribed by"
    r"|don'?t forget to (?:like|subscribe)"
    r"|(?:bye[- ]?){2,})",
    re.I,
)
# A whole transcript that is only one of these is noise, not a request.
_EMPTY_UTTERANCES = {"you", "thank you", "thanks", "bye", "the end", "hmm", "mm", "uh", "um", "oh", "ah", "huh"}
_WORD = re.compile(r"[a-zA-Z']+")
_SENTENCE = re.compile(r"[.!?]+\s*")

MIN_AVG_LOGPROB = -1.0       # Whisper's own fallback threshold
MAX_COMPRESSION_RATIO = 2.4  # likewise: above this the decode is looping


def rejection(t: Transcript, min_avg_logprob: float = MIN_AVG_LOGPROB) -> str | None:
    """Why this transcript should be ignored, or None if it looks like a real request."""
    text = t.text.strip()
    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "no words"
    if " ".join(words) in _EMPTY_UTTERANCES:
        return "filler"
    if t.avg_logprob is not None and t.avg_logprob < min_avg_logprob:
        return f"low confidence ({t.avg_logprob:.2f})"
    if t.compression_ratio is not None and t.compression_ratio > MAX_COMPRESSION_RATIO:
        return "looping transcript"
    if _HALLUCINATIONS.search(text):
        return "sounds like video audio"
    return _repetition(words, text)


def _repetition(words: list[str], text: str) -> str | None:
    if len(words) >= 4:
        _word, count = Counter(words).most_common(1)[0]
        if count / len(words) >= 0.5:
            return "one word repeated"
    sentences = [s.strip().lower() for s in _SENTENCE.split(text) if s.strip()]
    if len(sentences) >= 3 and len(set(sentences)) * 2 <= len(sentences):
        return "sentences repeated"
    return None
