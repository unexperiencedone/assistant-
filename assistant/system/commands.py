"""Which things you said are worth offering again as quick actions.

Only requests that actually worked are recorded (the controller decides that).
Here they are put in one canonical form, so "Can you open Spotify please." and
"open spotify" count as the same command, and anything that is too long to be a
button or looks like speech-recognition noise is left out.
"""

from __future__ import annotations

import re

from ..audio.gate import Transcript, rejection
from ..intents import normalize as strip_fillers

MAX_WORDS = 8   # longer than this is a one-off request, not a button
MIN_USES = 2    # said once is chance; twice is a habit

_POLITE_LEAD = re.compile(
    r"^(?:(?:can|could|would|will) you(?: please)?|please|i want you to|i'd like you to|i need you to|just|go and)\s+",
    re.I)
_POLITE_TAIL = re.compile(r"[\s,]+(?:please|for me|now|thanks|thank you)$", re.I)


def normalize_command(text: str) -> str:
    text = " ".join(strip_fillers(text).split()).strip(" .!?,;:")
    previous = None
    while previous != text:
        previous = text
        text = _POLITE_TAIL.sub("", _POLITE_LEAD.sub("", text)).strip(" .!?,;:")
    return text.lower()


def worth_keeping(command: str) -> bool:
    words = command.split()
    return 0 < len(words) <= MAX_WORDS and rejection(Transcript(command)) is None
